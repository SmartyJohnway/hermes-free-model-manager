"""Pure decision logic for Hermes free-model management. No network or Hermes writes."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation


def _price_zero(value: object) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, str) and value.strip().lower() == "free":
        return True
    try:
        return Decimal(str(value).strip()) == 0
    except (InvalidOperation, ValueError):
        return False


def classify_free(model: str, pricing: object, *, local: bool = False) -> str:
    """Fail closed on missing pricing; free-looking names alone prove nothing."""
    if local:
        return "local"
    if isinstance(pricing, dict):
        inp, out = pricing.get("prompt"), pricing.get("completion")
        if inp is not None and out is not None:
            request = pricing.get("request", 0)
            if _price_zero(inp) and _price_zero(out) and _price_zero(request):
                return "catalog_free"
            try:
                if any(Decimal(str(x)) > 0 for x in (inp, out, request)):
                    return "paid"
            except (InvalidOperation, ValueError, TypeError):
                pass
    if model.lower().endswith(":free") or model.lower().endswith("/free"):
        return "free_tag_unverified"
    return "unknown"


def advance_health(previous: dict | None, outcome: str, *, fail_at: int = 3, recover_at: int = 2) -> dict:
    """Hysteresis: suspend after N consecutive failures, restore after M successes."""
    if fail_at < 1 or recover_at < 1:
        raise ValueError("thresholds must be positive")
    state = dict(previous or {})
    status = state.get("status", "unverified")
    failures = int(state.get("failures", 0))
    successes = int(state.get("successes", 0))
    if outcome == "auth_error":
        return {"status": "auth_blocked", "failures": failures + 1, "successes": 0}
    if outcome == "rate_limit":
        return {"status": "cooldown", "failures": failures, "successes": 0}
    if outcome == "failure":
        failures += 1
        status = "suspended" if failures >= fail_at or status in ("suspended", "recovering") else "suspect"
        return {"status": status, "failures": failures, "successes": 0}
    if outcome != "success":
        raise ValueError(f"unknown outcome: {outcome}")
    if status in ("suspended", "recovering", "auth_blocked", "cooldown"):
        successes += 1
        return {"status": "healthy" if successes >= recover_at else "recovering",
                "failures": 0, "successes": successes}
    return {"status": "healthy", "failures": 0, "successes": 1}
