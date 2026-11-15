"""M2 P4 bounded real function-tool probes; no tools executed, no production changes.

No arbitrary per-provider/day or global/day quota: provider real free
entitlements and rate-limit/cooldown controls apply. Request reservations
are retained for audit and concurrency protection BEFORE any HTTP POST.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

import httpx

from provider_accounts import read_secret
from provider_cloudflare import account_id_from_hermes
from provider_probing import PROBE_FN

TARGETS = {
    "z-ai": ("glm-4.7-flash", "HERMES_CUSTOM_Z_AI_API_KEY",
             "https://api.z.ai/api/paas/v4/chat/completions"),
    "groq": ("openai/gpt-oss-20b", "HERMES_CUSTOM_GROQ_API_KEY",
             "https://api.groq.com/openai/v1/chat/completions"),
    "nvidia": ("nvidia/nemotron-3-super-120b-a12b", "NVIDIA_API_KEY",
               "https://integrate.api.nvidia.com/v1/chat/completions"),
    "cloudflare": ("@cf/zai-org/glm-4.7-flash",
                   "HERMES_CUSTOM_CLOUDFLARE_API_KEY", None),
}
MAX_P4_OUTPUT_TOKENS = 96
MAX_RESPONSE_BYTES = 300_000
P4_MARKER = "M2P4"


def validated_endpoint(provider: str, home: Path) -> str:
    if provider not in TARGETS:
        raise ValueError("unapproved provider")
    fixed = TARGETS[provider][2]
    if fixed is not None:
        return fixed
    account_id = account_id_from_hermes(home)
    if not account_id:
        raise ValueError("cloudflare account URL not validated")
    return f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1/chat/completions"


def build_request(model: str) -> dict:
    # Only controlled, synthetic data. Do not use real user conversation content.
    return {
        "model": model,
        "messages": [{"role": "user",
                      "content": "Call m2_probe_marker with marker M2P4. No text answer."}],
        "tools": [PROBE_FN],
        "tool_choice": "required",
        "stream": False,
        "max_tokens": MAX_P4_OUTPUT_TOKENS,
    }


def parse_response(status: int, body: object, headers: dict | None = None) -> dict:
    payload = body if isinstance(body, dict) else {}
    headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    allowed_headers = {
        "x-ratelimit-remaining-requests": "remaining_requests",
        "x-ratelimit-remaining-tokens": "remaining_tokens",
        "x-ratelimit-limit-requests": "limit_requests",
        "retry-after": "retry_after",
    }
    quota = {}
    for name, label in allowed_headers.items():
        raw = headers.get(name, "")
        if re.fullmatch(r"\d{1,9}(?:\.\d{1,4})?", raw):
            quota[label] = float(raw) if "." in raw else int(raw)
    usage = payload.get("usage", {})
    metering = {}
    if isinstance(usage, dict):
        for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
            v = usage.get(k)
            if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
                metering[k] = v
    outcome = {
        "http_status": status,
        "result": "unverified",
        "reason": "unknown",
        "quota_headers": quota,
        "usage": metering,
        "tool_call_confirmed": False,
    }
    if status == 200:
        choices = payload.get("choices", [])
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            outcome.update(result="tool_unverified", reason="missing_choices")
            return outcome
        finish = choices[0].get("finish_reason")
        if isinstance(finish, str) and finish in ("stop", "length", "tool_calls", "content_filter"):
            outcome["finish_reason"] = finish
        message = choices[0].get("message", {})
        calls = message.get("tool_calls", []) if isinstance(message, dict) else []
        for call in calls if isinstance(calls, list) else []:
            func = call.get("function", {}) if isinstance(call, dict) else {}
            if func.get("name") != "m2_probe_marker":
                continue
            args = func.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except (ValueError, TypeError):
                    args = {}
            if isinstance(args, dict) and args.get("marker") == P4_MARKER:
                outcome.update(result="pass", reason="exact_synthetic_function_call",
                               tool_call_confirmed=True)
                return outcome
        outcome.update(result="tool_unverified", reason="tool_call_not_confirmed")
        return outcome
    if status == 429:
        outcome.update(result="cooldown", reason="rate_limited")
    elif status == 402:
        outcome.update(result="hold", reason="payment_required")
    elif status in (401, 403):
        outcome.update(result="hold", reason="auth_or_plan_restricted")
    elif status == 404:
        outcome.update(result="unavailable", reason="model_route_not_found")
    elif status in (400, 422):
        outcome.update(result="incompatible", reason="tool_request_rejected")
    elif status >= 500:
        outcome.update(result="cooldown", reason="upstream_unavailable")
    else:
        outcome.update(result="hold", reason="http_or_network_failure")
    # Never serialize provider raw error messages, account ID, tokens or prompt.
    code = payload.get("error", {})
    if isinstance(code, dict) and isinstance(code.get("code"), (int, str)):
        c = str(code["code"])
        if re.fullmatch(r"\d{1,6}", c):
            outcome["provider_error_code"] = c
    return outcome


def _post(provider: str, home: Path, key: str, request: dict) -> tuple[int, dict, dict]:
    url = validated_endpoint(provider, home)
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in {
        "api.z.ai", "api.groq.com", "integrate.api.nvidia.com", "api.cloudflare.com"
    }:
        raise ValueError("unapproved HTTPS API host")
    with httpx.Client(timeout=32, trust_env=False, follow_redirects=False) as cli:
        resp = cli.post(url, headers={"Authorization": "Bearer " + key,
                                      "Accept": "application/json",
                                      "Content-Type": "application/json"},
                        json=request)
        if len(resp.content) > MAX_RESPONSE_BYTES:
            return 0, {}, {}
        try:
            parsed_body = resp.json()
        except ValueError:
            parsed_body = {}
        return resp.status_code, parsed_body if isinstance(parsed_body, dict) else {}, dict(resp.headers)


def _load_state(path: Path) -> dict:
    if not path.exists():
        return {"schema": 1, "reservations": [], "results": {}}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema") != 1:
        raise ValueError("invalid M2P4 state: fail closed")
    if not isinstance(raw.get("reservations"), list) or not isinstance(raw.get("results"), dict):
        raise ValueError("invalid M2P4 state fields: fail closed")
    return raw


def _save_state(path: Path, state: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.replace(path)


@contextmanager
def _exclusive(path: Path):
    lock = path.with_suffix(".runlock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(lock), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise RuntimeError("another run or stale lock exists; no API request") from exc
    try:
        yield
    finally:
        os.close(fd)
        lock.unlink(missing_ok=True)


def select_probe(provider: str, projection: dict) -> dict:
    if provider not in TARGETS:
        raise ValueError("unsupported provider")
    model = TARGETS[provider][0]
    # As of 2026-11-13, NVIDIA's official build page marks this Free
    # Endpoint Deprecated and our live POST returned 404. Do NOT re-probe
    # simply because /v1/models still lists it.
    if provider == "nvidia" and model == "nvidia/nemotron-3-super-120b-a12b":
        return {"ready": False, "reason": "official_free_endpoint_deprecated_404"}
    eligible = [x for x in projection.get("candidates", [])
                if x.get("provider") == provider and x.get("model") == model]
    if len(eligible) != 1:
        return {"ready": False, "reason": "model_not_in_fresh_qualified_projection"}
    row = eligible[0]
    if not row.get("model_listed"):
        return {"ready": False, "reason": "model_not_listed"}
    if provider in ("z-ai",) and not row.get("price_confirmed"):
        return {"ready": False, "reason": "zero_price_not_confirmed"}
    if provider == "groq" and not row.get("account_authorized"):
        return {"ready": False, "reason": "free_account_model_not_authorized"}
    if provider == "nvidia" and not (row.get("price_confirmed") and row.get("account_authorized")):
        return {"ready": False, "reason": "prototype_free_account_not_confirmed"}
    if provider == "cloudflare" and not row.get("account_authorized"):
        return {"ready": False, "reason": "workers_ai_account_token_not_authorized"}
    return {"ready": True, "model": model, "provider": provider}


def run_provider_probe(home: Path, projection: dict, provider: str,
                       state_path: Path, *, sender=None, today: str | None = None) -> dict:
    """No inference until positive free evidence, key, and provider health all pass."""
    check = select_probe(provider, projection)
    if not check["ready"]:
        return {"provider": provider, "result": "not_run", "reason": check["reason"]}
    key = read_secret(home, TARGETS[provider][1])
    if not key:
        return {"provider": provider, "result": "not_run", "reason": "credential_unavailable"}
    today = today or datetime.now(timezone.utc).date().isoformat()
    with _exclusive(state_path):
        state = _load_state(state_path)
        # Existing reservations are audit history, NOT a vendor quota. Only
        # a real provider health/cooldown restriction can block reuse.
        from probe_policy import can_probe_now
        model = check["model"]
        health = state.get("p5_health", {}).get(provider + "::" + model, {})
        allowed, health_reason = can_probe_now(health)
        if not allowed:
            return {"provider": provider, "result": "not_run", "reason": health_reason}
        # Reserve before any POST; on timeouts/crash never reclaim the request slot.
        state["reservations"].append({
            "utc_date": today, "provider": provider, "model": model,
            "reserved_at_utc": datetime.now(timezone.utc).isoformat()})
        _save_state(state_path, state)
        try:
            status, payload, headers = (
                sender(provider, home, key, build_request(model)) if sender
                else _post(provider, home, key, build_request(model)))
            detail = parse_response(int(status), payload, headers)
        except (httpx.HTTPError, TimeoutError, OSError, ValueError) as exc:
            detail = {"http_status": None, "result": "cooldown",
                      "reason": "network_error", "error_type": type(exc).__name__,
                      "tool_call_confirmed": False}
        detail.update(provider=provider, model=model, utc_date=today,
                      production_auto_apply=False)
        pair = provider + "::" + model
        state["results"][pair] = detail
        # A real 429/402/401/403/404 from a P4 probe must also protect P5.
        # A P4 one-step PASS never counts as a full health-loop success.
        from probe_policy import advance_p5_health
        http = detail.get("http_status")
        event = ("rate_limited" if http == 429 else
                 "payment_required" if http == 402 else
                 "auth_error" if http in (401, 403) else
                 "route_missing" if http == 404 else None)
        if event:
            before = state.get("p5_health", {}).get(pair, {})
            retry = detail.get("quota_headers", {}).get("retry_after")
            state.setdefault("p5_health", {})[pair] = advance_p5_health(
                before, event, utc_date=today, retry_after_seconds=retry)
        _save_state(state_path, state)
        return detail
