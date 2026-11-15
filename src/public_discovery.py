"""Read-only, credential-free official public catalog discovery (optional)."""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import json

# Fixed allowlist: never derive destinations from Hermes user/provider config.
PUBLIC_ENDPOINTS = {
    "openrouter": "https://openrouter.ai/api/v1/models",
    "kilo_custom": "https://api.kilo.ai/api/gateway/models",
}
MAX_BYTES = 12 * 1024 * 1024


class SameHostHttpsOnly(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        base, target = urlsplit(req.full_url), urlsplit(newurl)
        if target.scheme != "https" or target.hostname != base.hostname:
            raise ValueError("public catalog redirect blocked")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def safe_fetch_json(url: str) -> object:
    if url not in PUBLIC_ENDPOINTS.values():
        raise ValueError("unapproved public catalog URL")
    opener = build_opener(SameHostHttpsOnly())
    req = Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "Hermes-Free-Model-Manager-RD/0.1",
    })
    with opener.open(req, timeout=12) as response:
        raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("public catalog exceeded byte limit")
        return json.loads(raw.decode("utf-8-sig"))


def _catalog_items(payload: object) -> list[dict]:
    if isinstance(payload, list):
        entries = payload
    elif isinstance(payload, dict):
        entries = payload.get("data", payload.get("models", []))
    else:
        entries = []
    return [x for x in entries if isinstance(x, dict)] if isinstance(entries, list) else []


def discover_public(transport=None) -> dict:
    """A failure is reported explicitly; no fallback-to-stale masquerading as live."""
    fetch = transport or safe_fetch_json
    now = datetime.now(timezone.utc).isoformat()
    results: dict = {}
    for provider, url in PUBLIC_ENDPOINTS.items():
        try:
            items = _catalog_items(fetch(url))
            if not items:
                raise ValueError("empty_or_unexpected_catalog")
            models: dict[str, dict] = {}
            for item in items:
                model = item.get("id")
                if not isinstance(model, str) or not model or len(model) > 256:
                    continue
                raw_pricing = item.get("pricing")
                pricing = {
                    k: str(v) for k, v in raw_pricing.items()
                    if k in ("prompt", "completion", "request") and v is not None
                } if isinstance(raw_pricing, dict) else None
                params = item.get("supported_parameters")
                architecture = item.get("architecture")
                modalities = architecture.get("output_modalities") if isinstance(architecture, dict) else None
                models[model] = {
                    "pricing": pricing,
                    "isFree": item.get("isFree") is True,
                    "supported_parameters": params if isinstance(params, list) else None,
                    "output_modalities": modalities if isinstance(modalities, list) else None,
                }
            if not models:
                raise ValueError("no_valid_model_ids")
            results[provider] = {"status": "ok", "observed_at": now, "models": models}
        except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError, OSError) as exc:
            results[provider] = {"status": "failed", "error_type": type(exc).__name__,
                                 "models": {}, "observed_at": now}
    return results
