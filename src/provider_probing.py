"""Budgeted synthetic, no-tool-execution free model checks. No Hermes writes."""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
import json
import os
from pathlib import Path

import httpx

from core import advance_health

REMOTE_ENDPOINTS = {
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
    "kilo_custom": "https://api.kilo.ai/api/gateway/chat/completions",
}
PROBE_FN = {
    "type": "function",
    "function": {
        "name": "m2_probe_marker",
        "description": "Return the specified harmless marker, never perform an external action.",
        "parameters": {
            "type": "object",
            "properties": {"marker": {"type": "string"}},
            "required": ["marker"],
            "additionalProperties": False,
        },
    },
}


def plan_probes(catalog: dict, accounts: dict, *,
                max_requests: int = 2,
                exclude_pairs: set[tuple[str, str]] | None = None) -> list[dict]:
    """One distinct provider per pass; only freshly priced free, chat-compatible rows."""
    if max_requests not in (1, 2, 3):
        raise ValueError("max requests must be 1 to 3")
    candidates = []
    exclude_pairs = exclude_pairs or set()
    for item in catalog.get("models", []):
        provider = item.get("provider")
        model = item.get("model")
        if provider not in REMOTE_ENDPOINTS or not isinstance(model, str):
            continue
        if (provider, model) in exclude_pairs:
            continue
        if not (item.get("catalog_free_candidate") and item.get("freshness") == "fresh"
                and item.get("price_freshness") == "fresh"):
            continue
        if provider == "openrouter":
            daily = accounts.get("openrouter", {}).get("free_daily", {})
            if not (model.endswith(":free") and
                    accounts.get("openrouter", {}).get("status") == "account_metadata_read"
                    and isinstance(daily.get("remaining"), int) and daily["remaining"] >= 3):
                continue
        if provider == "kilo_custom":
            if not (model.endswith(":free") or model == "kilo-auto/free"):
                continue
        if item.get("chat_hint") == "non_chat_or_specialized":
            continue
        candidates.append({"provider": provider, "model": model,
                           "proof": item.get("reason")})
    chosen = []
    for provider in ("openrouter", "kilo_custom"):
        group = [c for c in candidates if c["provider"] == provider]
        # Deterministic and conservative: free virtual router first (Kilo);
        # OpenRouter one explicit :free with known catalog price.
        group.sort(key=lambda c: (c["model"] != "kilo-auto/free", c["model"]))
        if group:
            chosen.append(group[0])
        if len(chosen) >= max_requests:
            break
    return chosen


def _post_tool_probe(url: str, key: str | None, model: str) -> tuple[int, dict]:
    if url not in REMOTE_ENDPOINTS.values():
        raise ValueError("probe endpoint not approved")
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    req = {
        "model": model,
        "messages": [{"role": "user",
                      "content": "For this harmless API compatibility test call m2_probe_marker with marker M2."}],
        "tools": [PROBE_FN],
        "tool_choice": "required",
        "max_tokens": 96,
        "stream": False,
    }
    with httpx.Client(timeout=22.0, follow_redirects=False, trust_env=False) as client:
        response = client.post(url, headers=headers, json=req)
        if len(response.content) > 300_000:
            return 0, {}
        try:
            obj = response.json()
            return response.status_code, obj if isinstance(obj, dict) else {}
        except (json.JSONDecodeError, ValueError):
            return response.status_code, {}


def _classify_probe(code: int, body: dict) -> tuple[str, str]:
    """Sanitized outcome; never expose provider response/prompt/credentials."""
    if code == 200:
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            return "failure", "missing_choices"
        msg = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        calls = msg.get("tool_calls", []) if isinstance(msg, dict) else []
        if isinstance(calls, list):
            for call in calls:
                fn = call.get("function", {}) if isinstance(call, dict) else {}
                if isinstance(fn, dict) and fn.get("name") == "m2_probe_marker":
                    try:
                        arguments = json.loads(fn.get("arguments") or "{}")
                    except (ValueError, TypeError):
                        arguments = {}
                    if arguments.get("marker") == "M2":
                        return "success", "verified_tool_call"
        return "capability_unverified", "tool_call_not_confirmed"
    if code in (401, 403):
        return "auth_error", "authorization_or_policy"
    if code == 429:
        return "rate_limit", "rate_limited"
    if code in (400, 404):
        return "failure", "unsupported_request_or_missing_model"
    if code in (402, 407):
        return "auth_error", "payment_or_proxy_auth_required"
    return "failure", "http_error_or_timeout"


def _read_state(path: Path) -> dict:
    if not path.exists():
        return {"v": 1, "probes": [], "health": {}}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(obj, dict) and obj.get("v") == 1:
            return obj
    except (OSError, json.JSONDecodeError):
        pass
    # Damaged state fails closed: no probe (never reset the quota counters).
    raise RuntimeError("state unavailable or invalid; refusing to probe")


def _atomic_save(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _run_unlocked_probes(home: Path, catalog: dict, accounts: dict,
                         state_path: Path, *, max_requests: int = 2,
                         poster=None, today=None) -> dict:
    """Reserve before network request; no fixed provider/day or global/day cap."""
    from provider_accounts import read_secret
    state = _read_state(state_path)
    today = today or datetime.now(timezone.utc).date().isoformat()
    recent = [r for r in state.get("probes", []) if r.get("day") == today]
    # Historical successful probes are retained for audit; they do NOT
    # prohibit another research run on the same day.
    plan = plan_probes(catalog, accounts, max_requests=max_requests)
    results = []
    post = poster or _post_tool_probe
    for probe in plan:
        provider, model = probe["provider"], probe["model"]
        # Never send creds to non-allowlisted hosts. Kilo uses anonymous free access.
        key = read_secret(home, "OPENROUTER_API_KEY") if provider == "openrouter" else None
        if provider == "openrouter" and not key:
            results.append({"provider": provider, "model": model, "result": "credential_unavailable"})
            continue
        recent.append({"provider": provider, "model": model, "day": today})
        state["probes"] = state.get("probes", []) + [recent[-1]]
        _atomic_save(state_path, state)  # Reserve before sending: fail closed on crash.
        code = None
        try:
            code, body = post(REMOTE_ENDPOINTS[provider], key, model)
            outcome, reason = _classify_probe(code, body)
        except (httpx.HTTPError, TimeoutError, OSError, ValueError) as exc:
            outcome, reason = "failure", type(exc).__name__
        pair = provider + "::" + model
        before = state.get("health", {}).get(pair)
        if outcome == "capability_unverified":
            updated = {"status": "capability_unverified", "failures": 0, "successes": 0}
        else:
            updated = advance_health(before, outcome)
        state.setdefault("health", {})[pair] = updated
        _atomic_save(state_path, state)
        results.append({"provider": provider, "model": model, "result": outcome,
                        "http_status": code if isinstance(code, int) else None,
                        "reason": reason, "health": updated["status"]})
    return {"day": today, "request_slots_used_today": len(recent),
            "new_probes": results, "production_apply_allowed": False}

@contextmanager
def _exclusive_probe_lock(state_path: Path):
    """Exclusive reservation lock. A crash leaves the lock: fail closed."""
    lock = state_path.with_suffix(".runlock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(lock), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise RuntimeError("another probe or stale lock exists; no network request made") from exc
    try:
        yield
    finally:
        os.close(fd)
        lock.unlink(missing_ok=True)


def run_bounded_probes(home: Path, catalog: dict, accounts: dict,
                       state_path: Path, *, max_requests: int = 2,
                       poster=None, today=None) -> dict:
    with _exclusive_probe_lock(state_path):
        return _run_unlocked_probes(
            home, catalog, accounts, state_path,
            max_requests=max_requests, poster=poster, today=today)
