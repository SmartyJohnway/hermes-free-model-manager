"""M4 Manager: explicit apply/restore command surface for GUI and ChatGPT.

Run only under installed Hermes venv. No scheduled jobs, no inference.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import secrets
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))
official = Path(os.environ["LOCALAPPDATA"]) / "hermes" / "hermes-agent"
sys.path.insert(0, str(official))
os.environ["HERMES_HOME"] = str(Path(os.environ["LOCALAPPDATA"]) / "hermes")
os.environ["HERMES_CONFIG_BACKEND"] = "file"
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

from configuration_operations import apply_saved_draft, latest_reversible, undo_latest
from configuration_production import M4ProductionError, official_home
from manager_ui_model import load_hermes_selection, save_manager_draft
from manager_ai_exchange import read_catalog
from configuration_reconcile import run as reconcile_after_apply

DRAFT = PROJECT / "state" / "manager_draft.json"
CATALOG = PROJECT / "reports" / "eligibility_catalog.json"


def _reconcile_after_undo(transaction_id: str) -> dict:
    """Rebase Manager on restored official config, retaining applicable reasons."""
    baseline = load_hermes_selection(official_home())
    catalog, _ = read_catalog(CATALOG)
    data = json.loads(DRAFT.read_text(encoding="utf-8"))
    if (data.get("baseline_config_sha256") == baseline["config_sha256"]
            and (data.get("primary", {}).get("provider"),
                 data.get("primary", {}).get("model")) == baseline["primary"]
            and [(x["provider"], x["model"]) for x in data.get("fallback", [])]
                == baseline["fallback"]):
        return {"status": "MANAGER_ALREADY_REBASED_AFTER_ROLLBACK"}
    archive = DRAFT.with_name("manager_draft_pre_rollback_" + transaction_id + ".json")
    with archive.open("xb") as f:
        f.write(DRAFT.read_bytes())
        f.flush()
        os.fsync(f.fileno())
    original_reasons = {
        (x.get("provider"), x.get("model")): x.get("reason")
        for x in data.get("fallback", [])
        if isinstance(x, dict) and isinstance(x.get("reason"), str)
    }
    save_manager_draft(
        DRAFT, primary=baseline["primary"], fallback=baseline["fallback"],
        source_utc=catalog["source_snapshot_at_utc"],
        config_sha256=baseline["config_sha256"],
        ai_reasons=original_reasons,
        ai_summary="已依 M4 備份交易還原 Hermes；若再次調整請重新向 ChatGPT 匯出型錄。",
    )
    return {
        "status": "MANAGER_REBASED_AFTER_ROLLBACK",
        "restored_primary": list(baseline["primary"]),
        "restored_fallback_count": len(baseline["fallback"]),
        "archived_draft": str(archive),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Hermes Manager manual M4 operations")
    commands = parser.add_mutually_exclusive_group(required=True)
    commands.add_argument("--apply-confirmed", action="store_true")
    commands.add_argument("--latest-rollback-info", action="store_true")
    commands.add_argument("--rollback-confirmed", metavar="TRANSACTION_ID")
    parser.add_argument("--expect-draft-sha256",
                        help="Exact saved draft SHA from the UI before confirmation")
    args = parser.parse_args()
    try:
        if args.latest_rollback_info:
            print(json.dumps({"candidate": latest_reversible()},
                             ensure_ascii=False, indent=2))
            return 0
        if args.apply_confirmed:
            if not args.expect_draft_sha256:
                raise M4ProductionError("explicit saved draft SHA is required")
            digest = sha256(DRAFT.read_bytes()).hexdigest()
            if digest.lower() != args.expect_draft_sha256.lower():
                raise M4ProductionError("saved draft changed after confirmation")
            applied = apply_saved_draft(DRAFT, CATALOG)
            if applied["status"] != "ALREADY_MATCHES_HERMES":
                try:
                    applied["manager_reconcile"] = reconcile_after_apply(
                        applied["transaction_id"])
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    applied["manager_reconcile"] = {
                        "status": "NEEDS_MANUAL_RECONCILIATION",
                        "error_type": type(exc).__name__,
                    }
            print(json.dumps(applied, ensure_ascii=False, indent=2), flush=True)
            return 0

        latest = latest_reversible()
        if not latest or latest["transaction_id"] != args.rollback_confirmed:
            raise M4ProductionError("latest reversible transaction differs from confirmation")
        if not latest["current_matches"]:
            raise M4ProductionError("Hermes config changed; restore refused")
        returned = undo_latest()
        try:
            returned["manager_reconcile"] = _reconcile_after_undo(
                returned["transaction_id"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            returned["manager_reconcile"] = {
                "status": "NEEDS_MANUAL_RECONCILIATION",
                "error_type": type(exc).__name__,
            }
        print(json.dumps(returned, ensure_ascii=False, indent=2), flush=True)
        return 0
    except (M4ProductionError, ValueError, OSError, KeyError, TypeError) as exc:
        print("MANAGER_M4_TRANSACTION_REJECTED", type(exc).__name__, str(exc), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
