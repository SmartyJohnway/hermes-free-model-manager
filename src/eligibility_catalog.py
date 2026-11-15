"""M2 P3 free-eligibility synthesis from live documented sources, never auto-applies.

Every output is a candidate, not an approved Hermes fallback model.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import re

import httpx

from provider_policy import CLOUDFLARE_PAID_REQUIRED

ZAI_PRICING_URL = "https://docs.z.ai/guides/overview/pricing"
NVIDIA_FAQ_URL = "https://build.nvidia.com/api/runtime/content/help-center-faq.yaml"
AGNES_FAQ_URL = ("https://raw.githubusercontent.com/AgnesAI-Labs/"
                 "AgnesAI-Models/main/docs/TOKEN_PLAN_FAQ.md")
OFFICIAL = {"z-ai": ZAI_PRICING_URL, "nvidia": NVIDIA_FAQ_URL,
            "agnesai": AGNES_FAQ_URL}
MAX_DOCUMENT_BYTES = 2_000_000


class _HTMLTableRows(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows: list[list[str]] = []
        self.row: list[str] | None = None
        self.cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None and self.row is not None:
            self.row.append(" ".join(" ".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None


def parse_zai_zero_prices(html: str) -> list[str]:
    """All FOUR pricing columns must be exactly Free, never 'Limited-time Free'."""
    rows = _HTMLTableRows()
    rows.feed(html)
    found = set()
    for cells in rows.rows:
        if len(cells) < 5:
            continue
        name = cells[0].strip().lower()
        if not re.fullmatch(r"glm-[0-9][a-z0-9.-]{2,65}", name):
            continue
        if all(c.strip().lower() == "free" for c in cells[1:5]):
            found.add(name)
    if not found:
        raise ValueError("no fully free Z.ai pricing rows; cannot claim free")
    return sorted(found)


def parse_nvidia_free_policy(data: object) -> dict:
    if not isinstance(data, list):
        raise ValueError("NVIDIA public FAQ structure changed")
    pairs = {str(x.get("question", "")).lower(): str(x.get("answer", "")).lower()
             for x in data if isinstance(x, dict)}
    all_free = any("all models" in q and "free" in q
                   and "free to prototype" in v for q, v in pairs.items())
    rate = any("how many requests" in q
               and "rate limits" in v and "40 requests per minute" in v
               and "no per-token billing" in v for q, v in pairs.items())
    if not (all_free and rate):
        raise ValueError("NVIDIA free/rate-limited policy not positively verified")
    return {"free_to_prototype": True, "up_to_rpm": 40,
            "per_token_billing": False, "production_authorized": False}


def parse_agnes_free_policy(markdown: str) -> dict:
    if not ("Free / default user" in markdown
            and "Token Plan" in markdown
            and "RPM" in markdown):
        raise ValueError("Agnes Free/default access policy not confirmed")
    return {"free_default_access_type_documented": True,
            "specific_model_entitlement_confirmed": False}


def _get_document(url: str):
    if url not in OFFICIAL.values():
        raise ValueError("unsupported public policy URL")
    with httpx.Client(timeout=15, follow_redirects=False, trust_env=False) as cli:
        response = cli.get(url, headers={
            "Accept": "text/html,application/json,text/plain",
            "User-Agent": "Hermes-Free-Model-Manager-M2P3/0.1"})
        response.raise_for_status()
        if len(response.content) > MAX_DOCUMENT_BYTES:
            raise ValueError("public policy document too large")
        if url == NVIDIA_FAQ_URL:
            return response.json()
        return response.text


def discover_vendor_policies(*, fetch=None) -> dict:
    get = fetch or _get_document
    now = datetime.now(timezone.utc).isoformat()
    parsers = {
        "z-ai": lambda raw: {"free_model_ids": parse_zai_zero_prices(raw)},
        "nvidia": parse_nvidia_free_policy,
        "agnesai": parse_agnes_free_policy,
    }
    found = {}
    for provider, url in OFFICIAL.items():
        try:
            details = parsers[provider](get(url))
            found[provider] = {"status": "verified", "observed_at": now,
                               "documentation": url, **details}
        except (httpx.HTTPError, TimeoutError, OSError, ValueError, TypeError,
                KeyError, json.JSONDecodeError) as exc:
            found[provider] = {"status": "unverified", "observed_at": now,
                               "documentation": url,
                               "error_type": type(exc).__name__}
    return found


def build_eligibility_projection(catalog: dict, accounts: dict, policies: dict,
                                 nous_public: dict, cloudflare: dict) -> dict:
    """No inference. Only model/provider pairs with positive evidence appear."""
    rows: dict[tuple[str, str], dict] = {}
    configured = set(catalog.get("providers", {}))
    configured.add(str(catalog.get("primary", {}).get("provider", "")))

    def add(provider, model, category, basis, *, model_listed, price_confirmed,
            account_authorized, billing_guard):
        if not provider or not model or len(model) > 256:
            return
        rows[(provider, model)] = {
            "provider": provider, "model": model,
            "eligibility": category, "evidence": basis,
            "model_listed": bool(model_listed),
            "price_confirmed": bool(price_confirmed),
            "account_authorized": bool(account_authorized),
            "billing_guard": billing_guard,
            "text_inference_verified": False,
            "tool_calling_verified": False,
            "production_auto_apply": False,
        }

    for item in catalog.get("models", []):
        provider, model = item.get("provider"), item.get("model")
        if provider not in ("openrouter", "kilo_custom"):
            continue
        fresh = bool(item.get("catalog_free_candidate")
                     and item.get("freshness") == "fresh"
                     and item.get("price_freshness") == "fresh")
        if fresh:
            add(provider, model, "fresh_zero_price_catalog", "public_pricing_snapshot",
                model_listed=True, price_confirmed=True,
                account_authorized=accounts.get(provider, {}).get("status") == "account_metadata_read",
                billing_guard="free_model_only; verify account quota before inference")

    if "z-ai" in configured and policies.get("z-ai", {}).get("status") == "verified":
        for model in policies["z-ai"]["free_model_ids"]:
            add("z-ai", model, "official_zero_price", ZAI_PRICING_URL,
                model_listed=True, price_confirmed=True,
                account_authorized=False, billing_guard="no paid hosted tools")

    for provider, category, source in (
        ("nvidia", "rate_limited_prototype_free", NVIDIA_FAQ_URL),
        ("agnesai", "free_default_plan_candidate", AGNES_FAQ_URL),
        ("groq", "free_plan_candidate", "https://console.groq.com/docs/rate-limits"),
    ):
        account = accounts.get(provider, {})
        policy_ok = (provider == "groq" or
                     policies.get(provider, {}).get("status") == "verified")
        ids = account.get("listed_model_ids", [])
        if provider not in configured or not policy_ok or account.get("status") != "account_metadata_read":
            continue
        for model in ids:
            if not isinstance(model, str) or not model or len(model) > 256:
                continue
            # Conservative: no specialized image/audio/embedding endpoints in Hermes chat.
            if any(s in model.lower() for s in ("whisper", "guard", "safety", "embed", "rerank",
                                                "image", "video", "speech", "tts", "transcri")):
                continue
            add(provider, model, category, source, model_listed=True,
                price_confirmed=provider == "nvidia",
                account_authorized=True,
                billing_guard=("prototype-only; no per-token billing; rate-limited"
                               if provider == "nvidia"
                               else "must confirm same API key is in Free/default limit pool"))

    if "nous" in configured and nous_public.get("status") == "ok":
        for model in nous_public.get("models", []):
            if isinstance(model, str) and model.endswith(":free"):
                add("nous", model, "official_public_free_list",
                    "https://portal.nousresearch.com/models",
                    model_listed=True, price_confirmed=True,
                    account_authorized=False,
                    billing_guard="Free plan; account-specific API accessibility unproven")

    if "cloudflare" in configured and cloudflare.get("workers_ai_authorized"):
        models = (cloudflare.get("checks", {}).get("workers_ai_models", {})
                  .get("text_generation_model_ids", []))
        for model in models:
            if not isinstance(model, str) or not model.startswith("@cf/"):
                continue
            if model.lower() in CLOUDFLARE_PAID_REQUIRED:
                continue
            if any(s in model.lower() for s in ("embedding", "whisper", "rerank", "/image",
                                                "/tts", "speech", "guard")):
                continue
            add("cloudflare", model, "free_daily_neuron_allowance_candidate",
                "https://developers.cloudflare.com/workers-ai/platform/pricing/",
                model_listed=True, price_confirmed=False,
                account_authorized=True,
                billing_guard="10k neurons/day; no per-model balance or tools proof")
    projection = sorted(rows.values(), key=lambda r: (r["provider"], r["model"]))
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "M2_P3_ELIGIBILITY_RESEARCH_ONLY",
        "providers": dict(sorted(Counter(r["provider"] for r in projection).items())),
        "counts_by_eligibility": dict(sorted(Counter(r["eligibility"] for r in projection).items())),
        "candidates": projection,
        "candidate_count": len(projection),
        "tool_verified_count": 0,
        "production_auto_apply": False,
        "policy_discovery": {p: {"status": v.get("status"),
                                 "observed_at": v.get("observed_at"),
                                 "source": v.get("documentation"),
                                 "error_type": v.get("error_type")}
                             for p, v in policies.items()},
        "cloudflare": {
            "status": cloudflare.get("status"),
            "workers_ai_authorized": cloudflare.get("workers_ai_authorized", False),
            "models_visible_first_page": cloudflare.get("checks", {}).get(
                "workers_ai_models", {}).get("first_page_models", 0),
            "models_visible_total": cloudflare.get("models_visible", 0),
            "catalog_complete": cloudflare.get("catalog_complete", False),
            "pages_fetched": cloudflare.get("checks", {}).get(
                "workers_ai_models", {}).get("pages_fetched", 0),
            "pages_expected": cloudflare.get("checks", {}).get(
                "workers_ai_models", {}).get("pages_expected"),
        },
    }
