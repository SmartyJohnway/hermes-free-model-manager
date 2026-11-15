"""Separate vendor HTTP quota observations from local R&D request history.

These are historical snapshots, not a current entitlement or remaining-balance
API. No credential, account ID, private data, or raw provider error is emitted.
"""
from __future__ import annotations

from datetime import datetime, timezone
from collections import Counter

from probe_policy import can_probe_now


def provider_quota_evidence(state: dict, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone aware")
    day = now.astimezone(timezone.utc).date().isoformat()
    reservations = state.get("reservations", [])
    counts = Counter(str(r.get("provider")) for r in reservations
                     if isinstance(r, dict) and r.get("utc_date") == day)
    p4 = state.get("results", {})
    p5 = state.get("p5_results", {})
    health = state.get("p5_health", {})
    providers = sorted({"groq", "cloudflare", "z-ai", "nvidia"} |
                       set(counts) - {"None"})
    output = {}
    for provider in providers:
        rows = []
        for pair, v in p4.items():
            if not isinstance(v, dict) or v.get("provider") != provider:
                continue
            evidence = v.get("quota_headers") or {}
            safe = {k: evidence[k] for k in ("limit_requests",
                                              "remaining_requests",
                                              "remaining_tokens",
                                              "retry_after")
                    if isinstance(evidence.get(k), (int, float))
                    and not isinstance(evidence[k], bool) and evidence[k] >= 0}
            if safe:
                observed = next((r.get("reserved_at_utc")
                    for r in reversed(reservations) if isinstance(r, dict)
                    and r.get("provider") == provider
                    and r.get("model") == v.get("model")
                    and not r.get("stage")), None)
                rows.append({
                    "model": v.get("model"), "http_status": v.get("http_status"),
                    "observed_at_utc": observed,
                    "vendor_response_header_snapshot": safe,
                    "is_current_quota": False,
                    "source": "p4_single_call",
                })
        for pair, v in p5.items():
            if not isinstance(v, dict) or v.get("provider") != provider:
                continue
            for stage_no, stage in enumerate(v.get("stages", []), start=1):
                if not isinstance(stage, dict):
                    continue
                limits = stage.get("quota_headers") or {}
                safe = {k: limits[k] for k in ("limit_requests",
                                                "remaining_requests",
                                                "remaining_tokens",
                                                "retry_after")
                        if isinstance(limits.get(k), (int, float))
                        and not isinstance(limits[k], bool) and limits[k] >= 0}
                if not safe:
                    continue
                kind = "p5_tool_request" if stage_no == 1 else "p5_tool_result"
                observed = next((r.get("reserved_at_utc")
                    for r in reversed(reservations) if isinstance(r, dict)
                    and r.get("provider") == provider
                    and r.get("model") == v.get("model")
                    and r.get("stage") == kind), None)
                rows.append({
                    "model": v.get("model"), "http_status": stage.get("http_status"),
                    "observed_at_utc": observed,
                    "vendor_response_header_snapshot": safe,
                    "is_current_quota": False,
                    "source": kind,
                })
        statuses = {}
        for key, v in health.items():
            if key.startswith(provider + "::") and isinstance(v, dict):
                allowed, reason = can_probe_now(v, now=now)
                statuses[key.split("::", 1)[1]] = {
                    "status": v.get("status"),
                    "research_allowed_by_health": allowed, "reason": reason,
                    "cooldown_until_utc": v.get("cooldown_until_utc"),
                }
        output[provider] = {
            "request_history_utc_day": counts.get(provider, 0),
            "arbitrary_app_daily_cap": None,
            "vendor_quota_currently_verified": False,
            "vendor_remaining_current": None,
            "historical_response_headers": rows,
            "model_health": statuses,
        }
    return {
        "generated_at_utc": now.astimezone(timezone.utc).isoformat(),
        "message": ("Local request history is NOT remaining vendor quota. "
                    "Rate headers are historical snapshots only."),
        "provider_status": output,
        "production_auto_apply": False,
    }
