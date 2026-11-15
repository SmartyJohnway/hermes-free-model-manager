"""M2 P5 research CLI: offline status by default; live requires explicit opt-in."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from inventory import default_hermes_home
from main import ROOT, run_m2_p3
from tool_compatibility import _load_state, TARGETS
from probe_policy import (
    can_probe_now, data_route_allowed, evaluate_primary_exclusion, summarize_gate,
)
from probe_roundtrip import P5_PROVIDERS, run_round_trip
from provider_quota import provider_quota_evidence


STATE_PATH = ROOT / "state" / "m2_p4_budget.json"
SNAPSHOT = ROOT / "reports" / "eligibility_catalog.json"
REPORT = ROOT / "reports" / "m2_p5_gate.json"


def current_budget_status(state: dict, provider: str, today: str) -> dict:
    reservations = state.get("reservations", [])
    spent = [r for r in reservations if r.get("utc_date") == today]
    if provider not in P5_PROVIDERS:
        return {"ok": False, "reason": "provider_not_approved"}
    model = TARGETS[provider][0]
    previous = state.get("results", {}).get(provider + "::" + model, {})
    if not previous.get("tool_call_confirmed") or previous.get("result") != "pass":
        return {"ok": False, "reason": "real_P4_tool_proof_missing"}
    health = state.get("p5_health", {}).get(provider + "::" + model, {})
    allowed, health_reason = can_probe_now(health)
    if not allowed:
        return {"ok": False, "reason": health_reason}
    # Historical self-imposed call count is NOT an account quota.
    return {"ok": True, "reason": "research_allowed_subject_to_real_provider_limits",
            "request_history_today": len(spent)}


def collect_gate(home: Path, state: dict, p3: dict, *, mode: str,
                 requested: str | None = None, live_result: dict | None = None) -> dict:
    today = datetime.now(timezone.utc).date().isoformat()
    checks = {p: current_budget_status(state, p, today)
              for p in sorted(P5_PROVIDERS)}
    policy = {p: {
        "synthetic": data_route_allowed(
            p, "synthetic", privacy_docs_reviewed=True)[0],
        "confidential_default": data_route_allowed(
            p, "confidential", privacy_docs_reviewed=True,
            company_approval=False)[0],
    } for p in sorted(P5_PROVIDERS)}
    # Optional reliability history must NOT block M3 product development.
    # Config/fallback writes are a separate, explicit production action.
    preview = evaluate_primary_exclusion([], primary=(
        "kilo_custom", "kilo-auto/free"))
    gate = summarize_gate(state, p3)
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "provider_requested": requested,
        "live_result": live_result,
        "daily_budget": {
            "utc_date": today,
            "used": sum(x.get("utc_date") == today
                        for x in state.get("reservations", [])),
            "self_imposed_limit": None,
            "source": "state/m2_p4_budget.json; shared P4+P5 audit log",
            "vendor_remaining": None,
            "vendor_quota_currently_verified": False,
        },
        "p5_provider_preflight": checks,
        "provider_quota_evidence": provider_quota_evidence(state),
        "data_routing": policy,
        "gate": gate,
        "fallback_preview": preview,
        "no_production_writes": True,
        "independent_acceptance_pass": False,
    }


def run_cli(home: Path, provider: str | None, *, confirm_live: bool,
            ack_free_plan: bool = False) -> dict:
    state = _load_state(STATE_PATH)
    if not SNAPSHOT.exists():
        raise RuntimeError("M2 P3 local snapshot missing")
    p3 = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    if not isinstance(p3, dict):
        raise ValueError("M2 P3 snapshot not a dict")
    result = None
    if confirm_live and not ack_free_plan:
        raise ValueError("explicit current free-tier/no-paid-billing attestation required")
    if confirm_live:
        if provider not in P5_PROVIDERS:
            raise ValueError("P5 real probe only available for Groq or Cloudflare")
        today = datetime.now(timezone.utc).date().isoformat()
        check = current_budget_status(state, provider, today)
        if check["ok"]:
            # Re-fetch current free eligibility before a manually approved
            # probe. No self-imposed daily call quota is checked.
            fresh = run_m2_p3(home)
            result = run_round_trip(
                home, fresh, provider, STATE_PATH)
        else:
            result = {"provider": provider, "status": "hold",
                      "reason": check["reason"], "steps": 0}
        state = _load_state(STATE_PATH)
    mode = "P5_BUDGETED_LIVE" if confirm_live else "P5_OFFLINE_GATE_AUDIT"
    report = collect_gate(home, state, p3, mode=mode, requested=provider,
                          live_result=result)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="M2 P5 controlled tool loop / offline safety audit")
    parser.add_argument("--hermes-home", type=Path, default=default_hermes_home())
    parser.add_argument("--provider", choices=sorted(P5_PROVIDERS))
    parser.add_argument("--confirm-live", action="store_true",
                        help="Opt in to up to two synthetic API requests; budget shared with P4")
    parser.add_argument("--ack-free-plan", action="store_true",
                        help="Operator confirms current Free tier/Workers Free allowance and no paid billing")
    args = parser.parse_args()
    try:
        report = run_cli(args.hermes_home, args.provider,
                         confirm_live=args.confirm_live,
                         ack_free_plan=args.ack_free_plan)
    except Exception as exc:
        print("M2 P5 fail closed:", type(exc).__name__)
        return 2
    print("M2 P5:", report["mode"])
    budget = report["daily_budget"]
    print("R&D P4/P5 audit calls today (NOT vendor remaining):",
          budget["used"], "| arbitrary daily call cap: NONE")
    print("Provider remaining quota: UNKNOWN; stored API rate headers are HISTORICAL")
    for provider, info in report["provider_quota_evidence"]["provider_status"].items():
        if info["historical_response_headers"]:
            for entry in info["historical_response_headers"]:
                print("  HISTORICAL", provider, entry["model"],
                      entry.get("observed_at_utc") or "time unknown",
                      entry["vendor_response_header_snapshot"],
                      "(NOT current quota)")
    for provider, status in report["p5_provider_preflight"].items():
        print(" ", provider, status["reason"])
    print("Previous single-function API PASS:",
          ", ".join(report["gate"]["p4_one_step_pass_providers"]))
    print("Actual full tool-result loops confirmed:",
          report["gate"]["p5_real_round_trips_confirmed"])
    if args.confirm_live:
        live = report["live_result"]
        print("Requested P5 round trip:", live.get("status"),
              live.get("reason"), "API steps:", live.get("steps"))
    print("R&D only: reports/m2_p5_gate.json")
    print("M3 R&D development ready:",
          report["gate"]["m3_development_ready"],
          "| distinct UTC days required: NO")
    print("Production Hermes primary/picker/fallback untouched (separate release action).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
