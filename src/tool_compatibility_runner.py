"""M2 P4 explicit CLI for read-only preflight and tiny synthetic API tool probes."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from inventory import default_hermes_home
from main import ROOT, run_m2_p3
from tool_compatibility import TARGETS, run_provider_probe, select_probe
from tool_model_lineage import lineage_overlaps, summarize_live_probe_results


def execute(home: Path, requested: list[str], confirm: bool) -> dict:
    if any(p not in TARGETS for p in requested):
        raise ValueError("provider not on limited P4 list")
    if len(set(requested)) != len(requested) or len(requested) > 4:
        raise ValueError("duplicate providers or too many")
    # This revalidates current public evidence and account read-only GETs.
    project = run_m2_p3(home)
    plan = [select_probe(provider, project) | {"provider": provider}
            for provider in requested]
    report: dict = {
        "mode": "M2_P4_SYNTHETIC_FREE_TOOL_COMPATIBILITY_ONLY",
        "preflight": plan,
        "lineage": lineage_overlaps(project["candidates"]),
        "confirmed_by_operator": confirm,
        "results": [],
        "self_imposed_daily_cap": None,
        "production_auto_apply": False,
    }
    if confirm:
        for provider in requested:
            result = run_provider_probe(
                home, project, provider, ROOT / "state" / "m2_p4_budget.json")
            report["results"].append(result)
        report["summary"] = summarize_live_probe_results(report["results"])
    else:
        report["summary"] = {"tool_pass_count": 0, "ready_for_hermes_auto_apply": False}
    # Source reports remain local-only; never copy secrets or raw API responses.
    target = ROOT / "reports" / "m2_p4_live.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                      encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="M2 P4 limited synthetic Tool Calling")
    parser.add_argument("--hermes-home", type=Path, default=default_hermes_home())
    parser.add_argument("--providers", default="z-ai,groq,nvidia,cloudflare")
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    providers = [p.strip() for p in args.providers.split(",") if p.strip()]
    try:
        report = execute(args.hermes_home, providers, args.confirm)
    except Exception as exc:
        # Exception message may contain sensitive URLs or headers, so only type.
        print("M2 P4 not completed safely:", type(exc).__name__)
        return 2
    print("M2 P4", "LIVE SYNTHETIC" if args.confirm else "DRY RUN")
    for row in report["preflight"]:
        print("  PRECHECK", row["provider"], row.get("model", "-"),
              "READY" if row["ready"] else row.get("reason"))
    for row in report["results"]:
        print("  PROBE", row["provider"], row.get("model", "-"),
              "HTTP", row.get("http_status"),
              row.get("result"), row.get("reason"),
              "Tool exact:", row.get("tool_call_confirmed", False))
    print("Actual synthetic tool calls confirmed:",
          report["summary"].get("tool_pass_count", 0))
    print("R&D report: reports/m2_p4_live.json")
    print("Hermes production model/fallback/config: unchanged.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
