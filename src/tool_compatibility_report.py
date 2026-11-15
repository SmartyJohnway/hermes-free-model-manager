"""Aggregate M2 P4 historical sanitized results; strictly offline, no API calls."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from tool_model_lineage import lineage_overlaps, summarize_live_probe_results

ROOT = Path(__file__).resolve().parents[1]


def read_report() -> dict:
    p = ROOT / "state" / "m2_p4_budget.json"
    if not p.is_file():
        raise RuntimeError("no P4 historical probes")
    state = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(state, dict) or state.get("schema") != 1:
        raise RuntimeError("invalid P4 ledger")
    records = state.get("results") or {}
    if not isinstance(records, dict):
        raise RuntimeError("invalid P4 result structure")
    projection_path = ROOT / "reports" / "eligibility_catalog.json"
    projection = json.loads(projection_path.read_text(encoding="utf-8"))
    if not isinstance(projection, dict):
        raise RuntimeError("missing P3 projection")
    results = sorted(records.values(), key=lambda row: row.get("provider", ""))
    result = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "M2_P4_OFFLINE_AGGREGATED_EVIDENCE",
        "results": results,
        "summary": summarize_live_probe_results(results),
        "reservation_count": len(state.get("reservations", [])),
        "max_daily_remote_calls": 4,
        "shared_model_lineage": lineage_overlaps(projection.get("candidates", [])),
        "nous": {
            "status": "not_probed",
            "reason": "no safely scoped NOUS_API_KEY in existing Hermes .env; avoid copying OAuth credentials",
        },
        "production_hermes_unchanged": True,
        "auto_fallback_permitted": False,
        "independent_acceptance_pass": False,
    }
    report = ROOT / "reports" / "tool_calling_evidence.json"
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    return result


if __name__ == "__main__":
    try:
        r = read_report()
        print("M2 P4 consolidated actual tests (NO network or model calls)")
        for row in r["results"]:
            print("  ", row.get("provider"), row.get("model"),
                  "HTTP", row.get("http_status"), row.get("result"),
                  "Tool:", row.get("tool_call_confirmed"))
        print("  Total reserved calls:", r["reservation_count"])
        print("  Tool-call PASS:", r["summary"]["tool_pass_count"])
        print("  Shared upstream model families:",
              r["shared_model_lineage"]["shared_upstream_model_count"])
        print("  No fallback, model picker or production Hermes writes.")
    except Exception as exc:
        print("Unable to consolidate P4:", type(exc).__name__)
        sys.exit(2)
