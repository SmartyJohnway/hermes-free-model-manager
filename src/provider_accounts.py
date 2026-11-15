"""Read-only credential-scoped account inspection on approved official HTTPS hosts."""
from __future__ import annotations

import json
from pathlib import Path

import httpx

# M2 never prints, serializes, saves, or forwards tokens outside this allowlist.
APPROVED = {
    "openrouter": ("OPENROUTER_API_KEY", "https://openrouter.ai/api/v1/key"),
    "groq": ("HERMES_CUSTOM_GROQ_API_KEY", "https://api.groq.com/openai/v1/models"),
    "nvidia": ("NVIDIA_API_KEY", "https://integrate.api.nvidia.com/v1/models"),
    "agnesai": ("HERMES_CUSTOM_AGNESAI_API_KEY", "https://apihub.agnes-ai.com/v1/models"),
    "cloudflare": ("HERMES_CUSTOM_CLOUDFLARE_API_KEY", "https://api.cloudflare.com/client/v4/user/tokens/verify"),
}
MAX_RESPONSE = 4_000_000


def read_secret(home: Path, name: str) -> str | None:
    """Extract one approved variable in memory. No subprocess, logging or write."""
    if name not in ({v[0] for v in APPROVED.values()} | {"HERMES_CUSTOM_Z_AI_API_KEY"}):
        raise ValueError("credential identifier not approved")
    path = home / ".env"
    if not path.is_file():
        return None
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, sep, value = line.partition("=")
        if sep and key.strip() == name:
            value = value.strip()
            if len(value) > 1 and value[0] in ("'", '"') and value[-1] == value[0]:
                value = value[1:-1]
            return value or None
    return None


def _official_get(url: str, key: str) -> tuple[int, dict]:
    if url not in {v[1] for v in APPROVED.values()}:
        raise ValueError("endpoint not approved")
    with httpx.Client(timeout=10.0, follow_redirects=False, trust_env=False) as cli:
        response = cli.get(url, headers={"Authorization": "Bearer " + key,
                                        "Accept": "application/json"})
        if len(response.content) > MAX_RESPONSE:
            return 0, {}
        try:
            payload = response.json()
            return response.status_code, payload if isinstance(payload, dict) else {}
        except (json.JSONDecodeError, ValueError):
            return response.status_code, {}


def _safe_daily(data: dict) -> dict:
    if not isinstance(data, dict):
        return {}
    raw = data.get("free_model_daily_requests")
    if not isinstance(raw, dict):
        return {}
    output = {}
    for key in ("limit", "remaining", "used"):
        val = raw.get(key)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            output[key] = max(0, int(val))
    return output


def inspect_accounts(home: Path, *, getter=None) -> dict:
    """Only sanitized status/count data is returned; account fields are never persisted."""
    fetch = getter or _official_get
    result = {}
    for provider, (name, url) in APPROVED.items():
        key = read_secret(home, name)
        if not key:
            result[provider] = {"status": "credential_unavailable",
                                "credential_present": False}
            continue
        try:
            code, obj = fetch(url, key)
            if code == 200:
                data = obj.get("data", {}) if provider == "openrouter" else obj
                result[provider] = {"status": "account_metadata_read",
                                    "credential_present": True}
                if provider == "cloudflare":
                    status = obj.get("result", {}).get("status") if isinstance(obj.get("result"), dict) else None
                    result[provider]["status"] = ("token_active" if obj.get("success") is True
                                                  and status == "active" else "token_not_active")
                    result[provider]["free_plan_proven"] = False
                elif provider == "openrouter":
                    result[provider]["free_daily"] = _safe_daily(data)
                    result[provider]["free_quota_known"] = bool(result[provider]["free_daily"])
                else:
                    models = data.get("data", []) if isinstance(data, dict) else []
                    ids = {m.get("id") for m in models if isinstance(m, dict)
                           and isinstance(m.get("id"), str)}
                    result[provider]["listed_model_count"] = len(ids)
                    # Public model IDs only, never account or credential fields.
                    result[provider]["listed_model_ids"] = sorted(ids)
                    if provider == "groq":
                        result[provider]["gpt_oss_120b_listed"] = "openai/gpt-oss-120b" in ids
                    result[provider]["free_plan_proven"] = False
            elif code in (401, 403):
                result[provider] = {"status": "auth_failed", "credential_present": True,
                                    "http_status": code}
            elif code == 429:
                result[provider] = {"status": "rate_limited", "credential_present": True,
                                    "http_status": code}
            else:
                result[provider] = {"status": "endpoint_error", "credential_present": True,
                                    "http_status": int(code)}
        except (httpx.HTTPError, TimeoutError, OSError, ValueError) as exc:
            result[provider] = {"status": "request_failed", "credential_present": True,
                                "error_type": type(exc).__name__}
    # No Agnes / Cloudflare custom credentials are sent anywhere in M2.
    for provider in ("nous", "z-ai", "ollama-cloud"):
        result[provider] = {"status": "no_verified_account_metadata_endpoint",
                            "credential_present": None}
    return result
