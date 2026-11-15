"""Rebase the Manager draft after a verified, user-authorized Hermes apply.

Never writes Hermes itself. Preserve original Manager draft separately.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import secrets
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from configuration_production import BACKUP_SCHEMA, official_home
from manager_ui_model import load_hermes_selection

DRAFT = ROOT / "state" / "manager_draft.json"


def run(transaction_id: str) -> dict:
    folder = (Path(os.environ["LOCALAPPDATA"]) / "Hermes-Free-Model-Manager"
              / "M4_Backups" / transaction_id)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    if (manifest.get("schema") != BACKUP_SCHEMA
            or manifest.get("transaction_id") != transaction_id
            or manifest.get("state") != "APPLIED_VERIFIED"):
        raise ValueError("M4 production transaction not verified")
    after = load_hermes_selection(official_home())
    if after["config_sha256"] != manifest["after_sha256"]:
        raise ValueError("production config changed since M4 apply")
    if list(after["primary"]) != manifest["primary"]:
        raise ValueError("primary differs from authorized M4 transaction")
    if [list(pair) for pair in after["fallback"]] != manifest["fallback_after"]:
        raise ValueError("fallback differs from authorized M4 transaction")

    previous = DRAFT.read_bytes()
    data = json.loads(previous.decode("utf-8-sig"))
    if data.get("schema") != "hermes-free-manager-selection.v1":
        raise ValueError("invalid Manager draft")
    if (data.get("last_applied_transaction_id") == transaction_id
            and data.get("baseline_config_sha256") == manifest["after_sha256"]):
        return {"status": "MANAGER_ALREADY_RECONCILED",
                "transaction_id": transaction_id}
    if data.get("baseline_config_sha256") != manifest["before_sha256"]:
        raise ValueError("saved draft is not based on approved previous config")
    original_pair = [data["primary"]["provider"], data["primary"]["model"]]
    saved_fallback = [[x["provider"], x["model"]] for x in data["fallback"]]
    if original_pair != manifest["primary"] or saved_fallback != manifest["fallback_after"]:
        raise ValueError("saved draft does not match the authorized applied set")

    archive = DRAFT.with_name("manager_draft_pre_m4_" + transaction_id + ".json")
    with archive.open("xb") as f:
        f.write(previous)
        f.flush()
        os.fsync(f.fileno())

    data["baseline_config_sha256"] = manifest["after_sha256"]
    data["last_applied_transaction_id"] = transaction_id
    data["applied_at_utc"] = datetime.now(timezone.utc).isoformat()
    temp = DRAFT.with_name(".hfm-manager-reconcile-" + secrets.token_hex(6))
    try:
        with temp.open("x", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, DRAFT)
    finally:
        temp.unlink(missing_ok=True)
    return {"status": "MANAGER_DRAFT_RECONCILED",
            "transaction_id": transaction_id,
            "fallback_count": len(saved_fallback),
            "model_reasons_preserved": sum(bool(x.get("reason")) for x in data["fallback"]),
            "draft_archive": str(archive)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("transaction_id")
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.transaction_id), ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print("RECONCILE_FAILED", str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
