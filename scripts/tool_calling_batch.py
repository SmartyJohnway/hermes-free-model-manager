"""Explicit selected-model synthetic Tool Calling; PREVIEW default, no auto probe.

Use:
  python -B scripts/tool_calling_batch.py plan --selection state/m5_tool_batch_selection.json
  python -B scripts/tool_calling_batch.py run --selection ... --confirm-catalog-sha SHA
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
import re
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))
from inventory import default_hermes_home
from tool_batch_probe import (load_selection, make_plan, run_batch, save_result,
                                 REPORTS)
from manager_ai_exchange import SOURCE_PATH, _atomic_json


def main() -> int:
    parser = argparse.ArgumentParser(description="M5 manually selected synthetic Tool Calling")
    parser.add_argument("action", choices=["plan", "run"])
    parser.add_argument("--selection", type=Path,
                        default=PROJECT / "state" / "m5_tool_batch_selection.json")
    parser.add_argument("--source", type=Path, default=SOURCE_PATH)
    parser.add_argument("--hermes-home", type=Path, default=default_hermes_home())
    parser.add_argument("--confirm-catalog-sha", default="")
    parser.add_argument("--run-tag", default="",
                        help="Unique tagged run for real-time GUI progress / cancellation")
    parser.add_argument("--confirm-live", action="store_true",
                        help="Explicit operator confirmation for real quota-consuming POSTs")
    parser.add_argument("--ack-agnes-fee-risk", action="store_true",
                        help="Additional explicit opt-in for possibly billable AgnesAI models")
    args = parser.parse_args()
    try:
        selected = load_selection(args.selection)
        if args.action == "plan":
            outcome = make_plan(args.source, args.hermes_home, selected)
            print(json.dumps(outcome, ensure_ascii=False, indent=2))
            return 0
        # This is explicitly opt-in. A stale selection cannot be run.
        if not args.confirm_live or not args.confirm_catalog_sha:
            raise ValueError("live POST requires --confirm-live and the approved catalog SHA")
        allow_agnes = (
            args.ack_agnes_fee_risk
            and selected.get("agnes_fee_risk_ack") is True)
        if args.ack_agnes_fee_risk and not allow_agnes:
            raise ValueError("Agnes fee risk requires acknowledgement in selection")
        preview = make_plan(
            args.source, args.hermes_home, selected,
            allow_agnes_cost_risk=allow_agnes)
        if preview["blocked_count"]:
            raise ValueError("selected models contain unresolved fee/route restrictions")
        if preview["catalog_sha256"] != args.confirm_catalog_sha:
            raise ValueError("explicit catalog SHA confirmation changed")
        tag = args.run_tag or (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            + "-" + sha256(os.urandom(10)).hexdigest()[:12])
        if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}", tag):
            raise ValueError("invalid run tag")
        stem = "M5_TOOL_BATCH_" + tag
        result_path = REPORTS / (stem + ".json")
        progress_path = REPORTS / (stem + "_INPROGRESS.json")
        cancel_path = REPORTS / (stem + "_CANCEL")
        # Cancel may arrive immediately after the user presses Stop, even
        # before this process starts. Honor it as zero-request cancellation.
        if result_path.exists() or progress_path.exists():
            raise ValueError("batch run tag already exists")
        def journal(state):
            _atomic_json(progress_path, state)
        journal({"schema": "hfm-m5-tool-batch-progress.v1",
                 "state": "PREFLIGHT_COMPLETE_PREPARING_REQUESTS",
                 "total_selected": preview["selected_count"], "results": []})
        outcome = run_batch(
            args.source, args.hermes_home, selected,
            approved_catalog_sha=args.confirm_catalog_sha,
            progress=journal, stop_requested=cancel_path.exists,
            allow_agnes_cost_risk=allow_agnes)
        save_result(result_path, outcome)
        progress_path.unlink(missing_ok=True)
        cancel_path.unlink(missing_ok=True)
        print(json.dumps({
            "status": "M5_MANUAL_TOOL_BATCH_FINISHED",
            "report_path": str(result_path),
            "attempted_count": outcome["attempted_count"],
            "stop_reason": outcome["stop_reason"],
            "results": outcome["results"],
            "no_hermes_config_writes": True,
        }, ensure_ascii=False, indent=2), flush=True)
        return 0
    except (ValueError, OSError, TypeError, KeyError) as exc:
        print("M5_TOOL_BATCH_REJECTED", type(exc).__name__, str(exc)[:200], flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
