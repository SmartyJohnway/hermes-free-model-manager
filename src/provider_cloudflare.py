"""Cloudflare user/account token verification and Workers AI catalog; GET only.

Credentials live in Hermes .env; never copy into logs, requests to alternate hosts,
or persistent state. Account ID is validated and never placed in returned reports.
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit
import httpx
import yaml

from provider_accounts import read_secret

API_ORIGIN = "https://api.cloudflare.com"
USER_VERIFY = "/client/v4/user/tokens/verify"
ACCOUNT_RE = re.compile(r"^/client/v4/accounts/([a-fA-F0-9]{32})/ai/v1/?$")
MAX_RESPONSE = 1_000_000
PAGE_SIZE = 50
MAX_CATALOG_PAGES = 24  # Bounded metadata GETs; no inference or billing calls.


def account_id_from_hermes(home: Path) -> str | None:
    cfg = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8-sig")) or {}
    entry = (cfg.get("providers") or {}).get("cloudflare") or {}
    raw = entry.get("base_url")
    if not isinstance(raw, str):
        return None
    uri = urlsplit(raw)
    if uri.scheme != "https" or uri.netloc.lower() != "api.cloudflare.com":
        return None
    if uri.query or uri.fragment or uri.username or uri.password:
        return None
    found = ACCOUNT_RE.fullmatch(uri.path)
    return found.group(1) if found else None


def _safe_result(status: int, payload: object) -> dict:
    data = payload if isinstance(payload, dict) else {}
    result = data.get("result")
    token_status = result.get("status") if isinstance(result, dict) else None
    errors = data.get("errors")
    error_codes = sorted({str(row["code"]) for row in errors
                          if isinstance(row, dict) and isinstance(row.get("code"), int)}) if isinstance(errors, list) else []
    return {
        "http_status": int(status),
        "token_active": status == 200 and data.get("success") is True and token_status == "active",
        "token_status": token_status if token_status in ("active", "disabled", "expired") else None,
        "error_codes": error_codes,
    }


def _get_fixed(path: str, key: str) -> tuple[int, dict]:
    # All paths are generated in code from a validated account id, never from
    # external data. HTTPS origin is fixed, redirects are blocked.
    # Page one keeps the legacy path; later pages are exact, bounded fixed-origin
    # requests. Never send credentials to an arbitrary URL or accept a redirect.
    root, mark, query = path.partition("?")
    model_path = bool(re.fullmatch(
        r"/client/v4/accounts/[a-fA-F0-9]{32}/ai/models/search", root))
    allowed = root == USER_VERIFY or bool(re.fullmatch(
        r"/client/v4/accounts/[a-fA-F0-9]{32}/tokens/verify", root))
    page = 1
    if mark:
        match = re.fullmatch(r"page=([0-9]+)&per_page=50", query)
        if not model_path or match is None:
            raise ValueError("non-allowlisted Cloudflare page query")
        page = int(match.group(1))
        if not 2 <= page <= MAX_CATALOG_PAGES:
            raise ValueError("unbounded Cloudflare page")
    elif not (allowed or model_path):
        raise ValueError("non-allowlisted Cloudflare GET path")
    params = ({"page": page, "per_page": PAGE_SIZE} if model_path else None)
    with httpx.Client(timeout=12.0, trust_env=False, follow_redirects=False) as client:
        response = client.get(API_ORIGIN + root, headers={
            "Authorization": "Bearer " + key,
            "Accept": "application/json",
        }, params=params)
        if len(response.content) > MAX_RESPONSE:
            return 0, {}
        try:
            value = response.json()
            return response.status_code, value if isinstance(value, dict) else {}
        except ValueError:
            return response.status_code, {}


def _fetch_all_models(get, path: str, key: str) -> dict:
    """Fetch all permitted model-catalog pages; never run inference.

    Supports the documented result_info.total_pages contract and older responses
    without pagination metadata. A full 50-row page without result_info triggers
    a bounded follow-up page, not an unjustified 'complete' claim.
    """
    by_id: dict[str, dict] = {}
    page_signatures: set[tuple[str, ...]] = set()
    fetched = 0
    first_page_count = 0
    expected: int | None = None
    failure: str | None = None
    last_status = 0
    complete = False

    for page in range(1, MAX_CATALOG_PAGES + 1):
        request_path = path if page == 1 else f"{path}?page={page}&per_page={PAGE_SIZE}"
        try:
            status, body = get(request_path, key)
        except (httpx.HTTPError, TimeoutError, OSError, ValueError) as exc:
            failure = type(exc).__name__
            break
        last_status = int(status)
        if status != 200 or not isinstance(body, dict) or body.get("success") is not True:
            failure = "catalog_http_or_api_error"
            break
        models = body.get("result")
        if not isinstance(models, list):
            failure = "invalid_catalog_page"
            break
        if page == 1:
            first_page_count = len(models)

        info = body.get("result_info")
        if isinstance(info, dict):
            reported_page = info.get("page")
            if isinstance(reported_page, int) and reported_page != page:
                failure = "unexpected_page_number"
                break
            reported_total = info.get("total_pages")
            if reported_total is not None:
                if (not isinstance(reported_total, int)
                        or isinstance(reported_total, bool) or reported_total < 1):
                    failure = "invalid_total_pages"
                    break
                if expected is not None and expected != reported_total:
                    failure = "pagination_changed_mid_scan"
                    break
                expected = reported_total

        ids: list[str] = []
        for model in models:
            if not isinstance(model, dict):
                continue
            value = model.get("name") or model.get("id")
            if isinstance(value, str) and value.startswith("@cf/") and len(value) <= 256:
                ids.append(value)
                if value not in by_id:
                    by_id[value] = {
                        "task": model.get("task", {}).get("name")
                        if isinstance(model.get("task"), dict) else None,
                        "task_shape": type(model.get("task")).__name__,
                    }
        signature = tuple(sorted(ids))
        if signature and signature in page_signatures:
            failure = "repeated_catalog_page"
            break
        page_signatures.add(signature)
        fetched += 1

        if expected is not None:
            if page >= expected:
                complete = True
                break
            if not models:
                failure = "premature_empty_page"
                break
        elif len(models) < PAGE_SIZE:
            complete = True
            break
    else:
        failure = "max_catalog_pages_exceeded"

    return {
        "http_status": last_status,
        "status": "ok" if complete else "partial",
        "catalog_complete": complete,
        "pages_fetched": fetched,
        "pages_expected": expected,
        "first_page_models": first_page_count,
        "error_type": failure,
        "listed_models": len(by_id),
        "model_ids": sorted(by_id),
        "text_generation_model_ids": sorted(
            m for m, meta in by_id.items() if meta["task"] == "Text Generation"
        ),
        "model_task_labels": sorted({
            meta["task"][:55] for meta in by_id.values() if isinstance(meta["task"], str)
        }),
        "task_field_shapes": sorted({meta["task_shape"] for meta in by_id.values()}),
    }


def inspect_cloudflare(home: Path, *, getter=None) -> dict:
    """Checks both token scopes and model-list permissions with no inference."""
    key = read_secret(home, "HERMES_CUSTOM_CLOUDFLARE_API_KEY")
    account_id = account_id_from_hermes(home)
    if not key:
        return {"status": "credential_unavailable", "account_configured": bool(account_id)}
    if not account_id:
        return {"status": "account_id_unavailable", "credential_present": True}
    # One known configuration failure is an accidental prefixed Bearer string.
    has_prefix = key.lower().startswith("bearer ")
    if has_prefix:
        key = key[7:].strip()
    if not key:
        return {"status": "invalid_credential_format", "credential_present": True}
    get = getter or _get_fixed
    base = f"/client/v4/accounts/{account_id}"
    paths = {
        "user_token": USER_VERIFY,
        "account_token": base + "/tokens/verify",
        "workers_ai_models": base + "/ai/models/search",
    }
    result = {
        "status": "checked",
        "credential_present": True,
        "account_configured": True,
        "credential_had_bearer_prefix": has_prefix,
        "checks": {},
        "workers_ai_authorized": False,
        "models_visible": 0,
        "eligible_for_auto_apply": False,
    }
    for kind, path in paths.items():
        if kind == "workers_ai_models":
            safe = _fetch_all_models(get, path, key)
            # Catalog access is not a claim about each model's free entitlement.
            result["workers_ai_authorized"] = safe["pages_fetched"] > 0
            result["catalog_complete"] = safe["catalog_complete"]
            result["models_visible"] = safe["listed_models"]
            result["checks"][kind] = safe
            continue
        try:
            status, body = get(path, key)
            result["checks"][kind] = _safe_result(status, body)
        except (httpx.HTTPError, TimeoutError, OSError, ValueError) as exc:
            result["checks"][kind] = {
                "status": "request_failed", "error_type": type(exc).__name__
            }
    token_a = result["checks"].get("account_token", {})
    token_u = result["checks"].get("user_token", {})
    if result["workers_ai_authorized"]:
        result["status"] = ("workers_ai_metadata_accessible" if result.get("catalog_complete")
                            else "workers_ai_metadata_partial")
    elif token_a.get("token_active"):
        result["status"] = "account_token_active_workers_ai_not_authorized"
    elif token_u.get("token_active"):
        result["status"] = "user_token_active_workers_ai_not_authorized"
    else:
        result["status"] = "unverified_token_or_scope"
    return result


if __name__ == "__main__":
    import json
    from inventory import default_hermes_home
    summary = inspect_cloudflare(default_hermes_home())
    print("Cloudflare:", summary.get("status"))
    for label, row in summary.get("checks", {}).items():
        print("  ", label, "HTTP", row.get("http_status", "unavailable"),
              "active" if row.get("token_active") else "")
    print("Workers AI authorized:", summary.get("workers_ai_authorized", False))
    print("All fetched models:", summary.get("models_visible", 0))
    print("Catalog complete:", summary.get("catalog_complete", False))
    print("Pages fetched:", summary.get("checks", {}).get("workers_ai_models", {}).get("pages_fetched", 0))
    print("Text Generation across all fetched pages:",
          len(summary.get("checks", {}).get("workers_ai_models", {})
              .get("text_generation_model_ids", [])))
    print("No model inference, token/Account ID exposure, or Hermes writes.")
