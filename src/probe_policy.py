"""M2 P5 health, quota and privacy gates: pure R&D policy functions.

Repeated distinct-day successes are an optional reliability signal, NOT a
prerequisite for M3 development. No automatic production writes occur here.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import Counter
from typing import Any

from tool_model_lineage import model_lineage

P5_HEALTH_STATUSES = frozenset({
    "unverified", "probation", "suspect", "suspended", "recovering",
    "runtime_healthy_candidate", "cooldown", "auth_blocked",
    "billing_blocked", "route_unavailable", "capability_unverified",
})
CONFIDENTIAL_CLASSES = frozenset(("internal", "confidential", "restricted", "proprietary"))


def advance_p5_health(prior: dict | None, outcome: str, *,
                      utc_date: str, now: datetime | None = None,
                      retry_after_seconds: int | float | None = None) -> dict:
    """Never equate a single response or mere P4 Function Call with healthy."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    current = dict(prior or {})
    status = str(current.get("status", "unverified"))
    if status not in P5_HEALTH_STATUSES:
        raise ValueError("invalid health state")
    days = list(current.get("loop_pass_days", []))
    streak_days = list(current.get("streak_pass_days", []))
    if not all(isinstance(d, str) and len(d) == 10 for d in days + streak_days):
        raise ValueError("invalid pass-day history")
    failures = int(current.get("consecutive_failures", 0))
    recovery = int(current.get("recovery_successes", 0))
    output = {
        "status": status, "consecutive_failures": failures,
        "recovery_successes": recovery,
        "loop_pass_days": days[-7:],
        "streak_pass_days": streak_days[-7:],
        "cooldown_until_utc": current.get("cooldown_until_utc"),
        "production_auto_apply": False,
    }

    if outcome == "loop_pass":
        if utc_date not in days:
            days.append(utc_date)
        if utc_date not in streak_days:
            streak_days.append(utc_date)
        output["loop_pass_days"] = days[-7:]
        output["streak_pass_days"] = streak_days[-7:]
        output["consecutive_failures"] = 0
        output["cooldown_until_utc"] = None
        if status in ("suspect", "suspended", "recovering", "cooldown",
                      "auth_blocked", "billing_blocked", "route_unavailable",
                      "capability_unverified"):
            # Require two successful passes since the last restriction, not
            # merely two historical passes prior to being blocked.
            recovery += 1
            output["recovery_successes"] = recovery
            output["status"] = ("runtime_healthy_candidate" if recovery >= 2
                                else "recovering")
        else:
            # One complete real Function Call -> Tool Result -> final
            # verification is enough for a research runtime candidate.
            # A later real provider failure still demotes it normally.
            output["status"] = "runtime_healthy_candidate"
            output["recovery_successes"] = min(recovery + 1, 2)
    elif outcome in ("transport_failure", "tool_result_mismatch", "server_error"):
        failures += 1
        output["consecutive_failures"] = failures
        output["recovery_successes"] = 0
        output["streak_pass_days"] = []
        output["status"] = "suspended" if failures >= 3 else "suspect"
    elif outcome == "tool_capability_unverified":
        output["status"] = "capability_unverified"
        output["recovery_successes"] = 0
        output["streak_pass_days"] = []
    elif outcome == "rate_limited":
        try:
            retry = int(retry_after_seconds) if retry_after_seconds is not None else 60
        except (ValueError, TypeError):
            retry = 60
        seconds = max(60, min(retry, 3600))
        output["status"] = "cooldown"
        output["cooldown_until_utc"] = (now + timedelta(seconds=seconds)).isoformat()
        output["recovery_successes"] = 0
        output["streak_pass_days"] = []
    elif outcome == "auth_error":
        output.update(status="auth_blocked", recovery_successes=0,
                      streak_pass_days=[])
    elif outcome == "payment_required":
        output.update(status="billing_blocked", recovery_successes=0,
                      streak_pass_days=[])
    elif outcome == "route_missing":
        output.update(status="route_unavailable", recovery_successes=0,
                      streak_pass_days=[])
    else:
        raise ValueError("unknown P5 health outcome")
    return output


def can_probe_now(health: dict, *, now: datetime | None = None) -> tuple[bool, str]:
    """Check provider cooldown/hold gate independently from daily budget."""
    now = now or datetime.now(timezone.utc)
    status = health.get("status", "unverified")
    if status in ("auth_blocked", "billing_blocked", "route_unavailable"):
        return False, str(status)
    if status == "suspended":
        # A suspended model must not be used as an automatic fallback,
        # but research recovery probes must remain possible on the same day.
        return True, "manual_recovery_probe_allowed"
    if status == "cooldown":
        deadline = health.get("cooldown_until_utc")
        if not isinstance(deadline, str):
            return False, "cooldown_until_unknown"
        try:
            if datetime.fromisoformat(deadline) > now:
                return False, "cooldown_not_elapsed"
        except (ValueError, TypeError):
            return False, "invalid_cooldown_deadline"
    return True, "eligible_for_bounded_research_only"


def data_route_allowed(provider: str, data_class: str, *,
                       privacy_docs_reviewed: bool,
                       company_approval: bool = False) -> tuple[bool, str]:
    """Privacy review is not the same thing as company data authorization."""
    if provider not in ("groq", "cloudflare"):
        return False, "provider_privacy_gate_not_reviewed"
    if data_class == "synthetic":
        return True, "synthetic_public_test_data_only"
    if data_class == "public":
        return (privacy_docs_reviewed,
                "public_only_after_policy_review" if privacy_docs_reviewed
                else "privacy_policy_not_reviewed")
    if data_class in CONFIDENTIAL_CLASSES:
        return (privacy_docs_reviewed and company_approval,
                "company_security_approval_required"
                if not (privacy_docs_reviewed and company_approval)
                else "reviewed_company_approval_only")
    return False, "unknown_data_class_blocked"


def evaluate_primary_exclusion(records: list[dict], *,
                               primary: tuple[str, str],
                               local_last: dict | None = None) -> dict:
    """Preview only: eliminate same provider/model and shared model lineage.

    Current P5 has no sustained production-qualified models; only records
    explicitly passed through upstream health/entitlement gates may enter.
    """
    primary_provider, primary_model = primary
    primary_lineage = model_lineage(primary_provider, primary_model)
    selected: list[dict] = []
    seen_lineages = {primary_lineage}
    seen_providers = set()
    skipped = Counter()
    for record in sorted(records, key=lambda r: (r.get("provider", ""),
                                                  r.get("model", ""))):
        provider, model = record.get("provider"), record.get("model")
        if not isinstance(provider, str) or not isinstance(model, str):
            skipped["invalid_record"] += 1
            continue
        if (provider, model) == primary:
            skipped["selected_primary"] += 1
            continue
        if not (record.get("full_loop_verified") and
                record.get("runtime_status") == "runtime_healthy_candidate"
                and record.get("entitlement_current") and
                record.get("privacy_approved")):
            skipped["gates_incomplete"] += 1
            continue
        lineage = model_lineage(provider, model)
        if lineage in seen_lineages:
            skipped["shared_upstream_model"] += 1
            continue
        if provider in seen_providers:
            skipped["duplicate_provider"] += 1
            continue
        selected.append({"provider": provider, "model": model,
                         "lineage": lineage})
        seen_lineages.add(lineage)
        seen_providers.add(provider)
    if local_last:
        if (local_last.get("health") == "healthy"
                and local_last.get("tool_loop_verified")
                and (local_last.get("provider"), local_last.get("model")) != primary):
            selected.append({"provider": local_last["provider"],
                             "model": local_last["model"],
                             "local_last": True})
        else:
            skipped["ollama_not_certified"] += 1
    return {"preview": selected, "skipped": dict(sorted(skipped.items())),
            "hermes_fallback_modified": False,
            "production_auto_apply": False}


def summarize_gate(p4_state: dict, p3_projection: dict) -> dict:
    """Actual outcomes, not extrapolations from a simulated two-turn success."""
    p4_results = p4_state.get("results", {})
    p5_results = p4_state.get("p5_results", {})
    confirmed_one_step = sorted(
        (x.get("provider") for x in p4_results.values()
         if isinstance(x, dict) and x.get("tool_call_confirmed"))
    )
    confirmed_p5 = sorted(
        x.get("provider") for x in p5_results.values()
        if isinstance(x, dict) and x.get("tool_round_trip_confirmed"))
    return {
        "candidate_model_count": p3_projection.get("candidate_count", 0),
        "p4_one_step_pass_providers": confirmed_one_step,
        "p5_live_round_trip_pass_providers": confirmed_p5,
        "p5_real_round_trips_confirmed": len(confirmed_p5),
        # Product development depends on demonstrated functionality, not
        # the calendar. This does NOT authorize changing production Hermes.
        "m3_development_ready": bool(confirmed_p5),
        "m3_development_requires_distinct_utc_days": False,
        "optional_reliability_observation": "repeat later, never a development gate",
        "runtime_sustained_healthy": 0,
        "production_auto_apply": False,
        "m3_authorized": False,  # legacy: permission to modify production picker
        "m4_authorized": False,  # legacy: permission to modify production fallback
    }
