"""Manual, selected-model, one-request-per-model synthetic Tool Calling batch.

Uses prior M2 P4's synthetic prompt, parser and isolated credential reader.
No ranking, no auto probing, no Hermes config writes, no paid wallet fallback.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

import httpx
import yaml

from provider_accounts import read_secret
from provider_cloudflare import account_id_from_hermes
from tool_compatibility import build_request, parse_response, MAX_RESPONSE_BYTES
from manager_ai_exchange import read_catalog

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports" / "manual_tool_calling"
MAX_BATCH = 6
MAX_REQUESTS_PER_MODEL = 1

# Reuse the previously verified M2 HTTPS routes. Do not dynamically issue POST
# to arbitrary URLs found in model identifiers, catalog entries, or AI proposals.
HOST_ROUTES = {
    "z-ai": ("HERMES_CUSTOM_Z_AI_API_KEY", "https://api.z.ai/api/paas/v4/chat/completions"),
    "groq": ("HERMES_CUSTOM_GROQ_API_KEY", "https://api.groq.com/openai/v1/chat/completions"),
    "nvidia": ("NVIDIA_API_KEY", "https://integrate.api.nvidia.com/v1/chat/completions"),
    "openrouter": ("OPENROUTER_API_KEY", "https://openrouter.ai/api/v1/chat/completions"),
    "agnesai": ("HERMES_CUSTOM_AGNESAI_API_KEY", "https://apihub.agnes-ai.com/v1/chat/completions"),
}
# NOSUS/Kilo may have a different auth contract; do not guess a paid endpoint.
UNSUPPORTED_ROUTE = {
    "nous": "nous_free_plan_route_auth_not_verified_for_direct_post",
    "kilo_custom": "kilo_gateway_session_auth_not_verified_for_direct_post",
}


def _pairs(entries: object) -> list[tuple[str, str]]:
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_BATCH:
        raise ValueError(f"select between 1 and {MAX_BATCH} model entries manually")
    result = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("each selection requires provider and model")
        provider, model = entry.get("provider"), entry.get("model")
        if not isinstance(provider, str) or not isinstance(model, str):
            raise ValueError("invalid provider/model entry")
        if len(provider) > 100 or len(model) > 256 or not provider or not model:
            raise ValueError("invalid model identifier length")
        pair = provider, model
        if pair in result:
            raise ValueError("duplicate selected model")
        result.append(pair)
    return result


def load_selection(path: Path) -> dict:
    if path.stat().st_size > 32_000:
        raise ValueError("selected model request too large")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema") != "hfm-m5-tool-batch.v1":
        raise ValueError("unexpected batch selection schema")
    return value


def _route(provider: str, home: Path) -> tuple[str, str | None]:
    if provider == "cloudflare":
        account = account_id_from_hermes(home)
        if not account or not re.fullmatch("[0-9a-f]{32}", account):
            raise ValueError("cloudflare_account_id_unavailable")
        return (f"https://api.cloudflare.com/client/v4/accounts/{account}"
                "/ai/v1/chat/completions", "HERMES_CUSTOM_CLOUDFLARE_API_KEY")
    name, endpoint = HOST_ROUTES[provider]
    return endpoint, name


def _eligibility(row: dict, home: Path, *,
                 allow_agnes_cost_risk: bool = False) -> tuple[bool, str]:
    provider, model = row["provider"], row["model"]
    kind = row["free_evidence"]
    if provider in UNSUPPORTED_ROUTE:
        return False, UNSUPPORTED_ROUTE[provider]
    if provider not in HOST_ROUTES and provider != "cloudflare":
        return False, "no_verified_m2_tool_probe_adapter"
    if kind not in ("published_zero_price", "published_free_list",
                    "prototype_free_only", "free_allowance_candidate",
                    "free_plan_candidate"):
        return False, "unknown_or_paid_price"
    if provider == "agnesai" and not allow_agnes_cost_risk:
        # Free/default is not proof of zero cost for every Agnes model.
        return False, "agnesai_per_model_zero_price_not_confirmed"
    if kind == "published_zero_price" and not row.get("price_marked_free"):
        return False, "price_marked_free_required_for_zero_price_route"
    if kind == "free_plan_candidate" and provider != "groq":
        if not (provider == "agnesai" and allow_agnes_cost_risk):
            return False, "plan_free_does_not_prove_this_model_free"
    if (provider in ("groq", "openrouter", "nvidia", "cloudflare")
            and not row.get("provider_catalog_auth")):
        return False, "current_provider_catalog_authorization_unverified"
    if kind == "prototype_free_only" and provider != "nvidia":
        return False, "prototype_fee_terms_not_clear_for_this_route"
    if kind == "free_allowance_candidate" and provider != "cloudflare":
        return False, "daily_free_allowance_unverified"
    if provider == "openrouter" and not model.endswith(":free"):
        return False, "openrouter_requires_explicit_free_suffix"
    try:
        url, key_var = _route(provider, home)
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname not in {
            "api.z.ai", "api.groq.com", "integrate.api.nvidia.com",
            "openrouter.ai", "apihub.agnes-ai.com", "api.cloudflare.com"
        }:
            return False, "HTTPS_provider_route_not_allowed"
        if not key_var or not read_secret(home, key_var):
            return False, "approved_free_provider_credential_missing"
    except (OSError, ValueError):
        return False, "provider_route_unavailable"
    if provider == "agnesai":
        return True, "agnesai_fee_unknown_explicit_opt_in_only"
    return True, "eligible_for_one_optional_synthetic_request"


def make_plan(source: Path, home: Path, selection: dict, *,
              allow_agnes_cost_risk: bool = False) -> dict:
    catalog, catalog_sha = read_catalog(source)
    by_pair = {(r["provider"], r["model"]): r for r in catalog["rows"]}
    if selection.get("catalog_sha256") != catalog_sha:
        raise ValueError("catalog changed; reselect and review costs")
    pairs = _pairs(selection.get("models"))
    plan = []
    for p, m in pairs:
        if (p, m) not in by_pair:
            raise ValueError("selected model not in current free candidate catalog")
        row = by_pair[(p, m)]
        ok, reason = _eligibility(
            row, home, allow_agnes_cost_risk=allow_agnes_cost_risk)
        can_override = (
            p == "agnesai" and not ok
            and _eligibility(row, home, allow_agnes_cost_risk=True)[0])
        plan.append({
            "provider": p, "model": m,
            "free_evidence": row["free_evidence"],
            "account_evidence": row["account_ui_evidence_20261113"],
            "source_snapshot_utc": catalog["source_snapshot_at_utc"],
            "eligible": ok, "reason": reason,
            "fee_risk": p == "agnesai",
            "risk_override_available": can_override,
            "maximum_requests": MAX_REQUESTS_PER_MODEL if ok else 0,
            "quota_remaining": "unknown",
            "cost_guarantee": False,
        })
    return {
        "schema": "hfm-m5-tool-batch-plan.v1",
        "catalog_sha256": catalog_sha,
        "source_snapshot_utc": catalog["source_snapshot_at_utc"],
        "selected_count": len(plan),
        "eligible_count": sum(x["eligible"] for x in plan),
        "blocked_count": sum(not x["eligible"] for x in plan),
        "fee_risk_count": sum(x["fee_risk"] for x in plan),
        "fee_risk_override_count": sum(
            x["risk_override_available"] for x in plan),
        "allow_agnes_cost_risk": allow_agnes_cost_risk,
        "maximum_remote_requests": sum(x["maximum_requests"] for x in plan),
        "may_consume_provider_free_quota": True,
        "never_use_paid_wallet_implicitly": True,
        "no_model_requests_during_plan": True,
        "entries": plan,
    }


def _send_once(provider: str, model: str, home: Path) -> tuple[int, dict, dict]:
    url, secret_name = _route(provider, home)
    key = read_secret(home, secret_name) if secret_name else None
    if not key:
        raise ValueError("credential missing before send")
    message = build_request(model)
    # Reuse exact P4 compatibility request and parser; controlled synthetic text.
    with httpx.Client(timeout=24, follow_redirects=False, trust_env=False) as client:
        response = client.post(
            url, headers={"Authorization": "Bearer " + key,
                          "Content-Type": "application/json",
                          "Accept": "application/json"},
            json=message)
        if len(response.content) > MAX_RESPONSE_BYTES:
            return 0, {}, {}
        try:
            body = response.json()
        except ValueError:
            body = {}
        return response.status_code, body if isinstance(body, dict) else {}, dict(response.headers)


def run_batch(source: Path, home: Path, selection: dict, *,
              approved_catalog_sha: str, sender=None, progress=None,
              stop_requested=None, allow_agnes_cost_risk: bool = False) -> dict:
    """Explicit-only; refuses *any* blocked selection before sending."""
    if allow_agnes_cost_risk and selection.get("agnes_fee_risk_ack") is not True:
        raise ValueError("Agnes fee risk requires explicit selection acknowledgement")
    plan = make_plan(
        source, home, selection,
        allow_agnes_cost_risk=allow_agnes_cost_risk)
    if approved_catalog_sha != plan["catalog_sha256"]:
        raise ValueError("confirmation pinned an older catalog")
    if plan["blocked_count"]:
        raise ValueError("selected batch contains unavailable/fee-uncertain models; no POST")
    rows = []
    abort = None
    for entry in plan["entries"]:
        if stop_requested and stop_requested():
            abort = "user_stopped_before_next_request"
            break
        provider, model = entry["provider"], entry["model"]
        utc = datetime.now(timezone.utc).isoformat()
        if progress:
            progress({"schema": "hfm-m5-tool-batch-progress.v1",
                      "catalog_sha256": plan["catalog_sha256"],
                      "state": "REQUEST_RESERVED_BEFORE_POST",
                      "reserved_model": {"provider": provider, "model": model,
                                         "reserved_at_utc": utc},
                      "results": rows,
                      "total_selected": plan["selected_count"],
                      "note": "If interrupted, reserved remote quota may already have been used."})
        try:
            status, payload, headers = (
                sender(provider, model, home) if sender
                else _send_once(provider, model, home))
            outcome = parse_response(status, payload, headers)
        except (httpx.HTTPError, OSError, TimeoutError, ValueError) as exc:
            outcome = {"http_status": None, "result": "hold",
                       "reason": "network_or_auth_failure",
                       "error_type": type(exc).__name__,
                       "tool_call_confirmed": False}
        rows.append({
            "provider": provider, "model": model, "attempted_at_utc": utc,
            "request_attempted": True, "request_limit": 1,
            "http_status": outcome.get("http_status"),
            "result": outcome.get("result", "unverified"),
            "reason": outcome.get("reason", "unknown"),
            "tool_call_confirmed": outcome.get("tool_call_confirmed", False),
            "quota_headers": outcome.get("quota_headers", {}),
            "usage": outcome.get("usage", {}),
        })
        if progress:
            progress({"schema": "hfm-m5-tool-batch-progress.v1",
                      "catalog_sha256": plan["catalog_sha256"],
                      "state": "REQUEST_RECORDED",
                      "results": rows,
                      "total_selected": plan["selected_count"],
                      "no_hermes_config_writes": True})
        # Avoid burning additional accounts following platform-wide trouble.
        if outcome.get("http_status") in (401, 402, 403, 429):
            abort = f"stopped_after_http_{outcome['http_status']}"
            break
        if outcome.get("http_status") is None or outcome.get("result") == "cooldown":
            abort = "stopped_after_network_or_upstream_failure"
            break
    return {
        "schema": "hfm-m5-tool-batch-result.v1",
        "utc": datetime.now(timezone.utc).isoformat(),
        "catalog_sha256": plan["catalog_sha256"],
        "planned_count": plan["selected_count"],
        "attempted_count": len(rows),
        "stop_reason": abort or "finished_selected_batch",
        "results": rows,
        "no_hermes_config_writes": True,
        "contains_credentials": False,
    }


def save_result(path: Path, outcome: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ValueError("result file already exists; refusing overwrite")
    fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(outcome, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
