"""M5 ChatGPT direct, manual-only workflow. No ranking algorithm or background tasks.

The caller (ChatGPT via Desktop Commander) owns proposal reasoning and must
receive an explicit user instruction before production configuration changes.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))
from inventory import default_hermes_home
from manager_ai_exchange import (
    export_request, import_recommendation, read_catalog,
    REQUEST_PATH, RESPONSE_PATH, SOURCE_PATH, GUIDE_PATH,
)
from manager_ui_model import load_hermes_selection, save_manager_draft
from configuration_preview import preview_from_draft

DRAFT_PATH = PROJECT / "state" / "manager_draft.json"


def _native_hermes_operation(*arguments: str) -> dict:
    python = (Path(os.environ["LOCALAPPDATA"]) / "hermes" / "hermes-agent"
              / "venv" / "Scripts" / "python.exe")
    entry = PROJECT / "scripts" / "configuration_transaction.py"
    if not python.is_file() or not entry.is_file():
        raise RuntimeError("official Hermes native operation unavailable")
    result = subprocess.run(
        [str(python), "-B", str(entry), *arguments],
        cwd=str(PROJECT), text=True, encoding="utf-8", errors="replace",
        capture_output=True, timeout=95, check=False,
    )
    if result.returncode:
        raise RuntimeError("Hermes M4 operation rejected; check local M4 status")
    return json.loads(result.stdout)


def _display_catalog(source: Path, *, provider: str | None = None,
                     all_models: bool = False) -> dict:
    catalog, snapshot_hash = read_catalog(source)
    rows = [r for r in catalog["rows"]
            if not provider or r["provider"] == provider]
    return {
        "schema": "hfm-m5-agent-catalog.v1",
        "snapshot_utc": catalog["source_snapshot_at_utc"],
        "snapshot_sha256": snapshot_hash,
        "total_candidates": len(catalog["rows"]),
        "filtered_candidates": len(rows),
        "provider_counts": catalog["provider_counts"],
        "models": [
            {"provider": r["provider"], "model": r["model"],
             "free_evidence": r["free_evidence"],
             "per_model_free_entitlement_verified":
                 r["per_model_free_entitlement_verified"],
             "tool_calling": r["tool_calling"],
             "latest_manual_tool_calling": (
                 {k: r["manual_tool_result"][k] for k in (
                     "label", "http_status", "attempted_at_utc",
                     "result", "tool_call_confirmed", "report_name"
                 )} if r.get("manual_tool_result") else None),
             "historical_tool_calling_pass": (
                 bool(r.get("manual_last_pass"))
                 or r["tool_calling"] == "tested_pass"),
             "price_marked_free": r["price_marked_free"],
             "account_evidence": r["account_ui_evidence_20261113"],
             "evidence_reference": r["evidence_reference"]}
            for r in rows
        ] if all_models else [],
        "no_inference": True,
    }


def _stage(source: Path, home: Path, request: Path, proposal: Path,
           draft: Path) -> dict:
    """Import AI choice directly to Manager draft; no Hermes writes."""
    validated = import_recommendation(source, home, request, proposal)
    baseline = load_hermes_selection(home)
    catalog, _ = read_catalog(source)
    if len(validated["fallback"]) > 30:
        raise ValueError("too many fallback models")
    pairs = [(r["provider"], r["model"]) for r in validated["fallback"]]
    eligible = {(r["provider"], r["model"]) for r in catalog["rows"]}
    if (validated["primary"] not in eligible
            and validated["primary"] != baseline["primary"]):
        raise ValueError("primary is not qualified for the current catalog")
    reasons = {(r["provider"], r["model"]): r["reason"]
               for r in validated["fallback"]}
    if draft.is_file():
        previous = json.loads(draft.read_text(encoding="utf-8"))
        if previous.get("baseline_config_sha256") != baseline["config_sha256"]:
            raise ValueError("prior draft has a different Hermes baseline; export again")
    save_manager_draft(
        draft, primary=validated["primary"], fallback=pairs,
        source_utc=catalog["source_snapshot_at_utc"],
        config_sha256=baseline["config_sha256"],
        ai_reasons=reasons, ai_summary=validated["summary"],
        ai_primary_reason=validated["primary_reason"],
    )
    plan = preview_from_draft(
        json.loads(draft.read_text(encoding="utf-8")), baseline, catalog)
    return {
        "status": "M5_AI_PROPOSAL_STAGED_MANAGER_ONLY",
        "primary": list(plan["primary_after"]),
        "fallback_count": len(plan["fallback_after"]),
        "conditional_count": validated["conditional_count"],
        "draft_path": str(draft),
        "draft_sha256": sha256(draft.read_bytes()).hexdigest(),
        "hermes_writes": False,
    }


def _summary(source: Path, home: Path, draft: Path) -> dict:
    baseline = load_hermes_selection(home)
    catalog, _ = read_catalog(source)
    result = {
        "catalog_utc": catalog["source_snapshot_at_utc"],
        "provider_counts": catalog["provider_counts"],
        "total_candidates": len(catalog["rows"]),
        "current_primary": list(baseline["primary"]),
        "current_fallback_count": len(baseline["fallback"]),
        "hermes_config_sha256": baseline["config_sha256"],
        "saved_draft": draft.is_file(),
        "hermes_writes": False,
    }
    if draft.is_file():
        value = json.loads(draft.read_text(encoding="utf-8"))
        result["draft_sha256"] = sha256(draft.read_bytes()).hexdigest()
        result["draft_baseline_current"] = (
            value.get("baseline_config_sha256") == baseline["config_sha256"])
    return result


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="HFM M5 ChatGPT manual-only workflow")
    parser.add_argument("action", choices=[
        "get", "catalog", "export", "validate", "stage", "preview",
        "status", "restore-info", "apply-confirmed", "restore-confirmed"])
    parser.add_argument("--provider", default="")
    parser.add_argument("--all-models", action="store_true")
    parser.add_argument("--source", type=Path, default=SOURCE_PATH)
    parser.add_argument("--hermes-home", type=Path, default=default_hermes_home())
    parser.add_argument("--request", type=Path, default=REQUEST_PATH)
    parser.add_argument("--proposal", type=Path, default=RESPONSE_PATH)
    parser.add_argument("--draft", type=Path, default=DRAFT_PATH)
    parser.add_argument("--preferred-provider", default="")
    parser.add_argument("--preferred-model", default="")
    parser.add_argument("--expect-draft-sha256")
    parser.add_argument("--transaction-id")
    parser.add_argument("--owner-approved", action="store_true",
                        help="Explicit owner approval for this specific current draft/rollback")
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    try:
        if args.action == "get":
            if args.source.resolve() != SOURCE_PATH.resolve():
                raise ValueError("GET always writes only the canonical research snapshot")
            env = dict(os.environ)
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            # Existing M2 provider metadata GET only: no inference, fallback writes, scheduler.
            outcome = subprocess.run(
                [sys.executable, "-B", str(PROJECT / "src" / "main.py"),
                 "eligibility", "--hermes-home", str(args.hermes_home)],
                cwd=str(PROJECT), env=env, text=True,
                capture_output=True, timeout=180, check=False)
            if outcome.returncode:
                raise RuntimeError("M2 GET failed: " + str(outcome.returncode))
            result = _display_catalog(args.source, provider=args.provider)
            result["status"] = "M5_MANUAL_GET_COMPLETE"
        elif args.action == "catalog":
            result = _display_catalog(
                args.source, provider=args.provider or None,
                all_models=args.all_models)
        elif args.action == "export":
            if bool(args.preferred_provider) != bool(args.preferred_model):
                raise ValueError("preferred provider and model must be given together")
            preferred = ((args.preferred_provider, args.preferred_model)
                         if args.preferred_provider else None)
            value = export_request(
                args.source, args.hermes_home, args.request,
                preferred_primary=preferred)
            result = {
                "status": "M5_REQUEST_EXPORTED", "candidate_count": value["candidate_count"],
                "request_path": str(args.request),
                "guide_path": str(GUIDE_PATH),
                "proposal_target": str(args.proposal),
                "request_id": value["request_id"],
                "no_hermes_writes": True,
            }
        elif args.action == "validate":
            data = import_recommendation(
                args.source, args.hermes_home, args.request, args.proposal)
            result = {"status": "M5_AI_PROPOSAL_VALID",
                      "primary": list(data["primary"]),
                      "fallback_count": len(data["fallback"]),
                      "conditional_count": data["conditional_count"]}
        elif args.action == "stage":
            result = _stage(args.source, args.hermes_home,
                            args.request, args.proposal, args.draft)
        elif args.action == "preview":
            baseline = load_hermes_selection(args.hermes_home)
            catalog, _ = read_catalog(args.source)
            result = preview_from_draft(
                json.loads(args.draft.read_text(encoding="utf-8")),
                baseline, catalog)
            result["status"] = "M5_DRY_RUN_NO_HERMES_WRITES"
        elif args.action == "status":
            result = _summary(args.source, args.hermes_home, args.draft)
        else:
            if (args.source.resolve() != SOURCE_PATH.resolve()
                    or args.hermes_home.resolve() != default_hermes_home().resolve()
                    or args.draft.resolve() != DRAFT_PATH.resolve()):
                raise ValueError("production operation rejects custom source/home/draft")
            if args.action == "restore-info":
                result = _native_hermes_operation("--latest-rollback-info")
            elif not args.owner_approved:
                raise ValueError("owner approval is required to modify Hermes")
            elif args.action == "apply-confirmed":
                if (not args.expect_draft_sha256
                        or sha256(args.draft.read_bytes()).hexdigest()
                           != args.expect_draft_sha256):
                    raise ValueError("draft SHA did not match explicit approval")
                result = _native_hermes_operation(
                    "--apply-confirmed",
                    "--expect-draft-sha256", args.expect_draft_sha256)
            else:
                if not args.transaction_id:
                    raise ValueError("rollback requires exact transaction id")
                result = _native_hermes_operation(
                    "--rollback-confirmed", args.transaction_id)
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError,
            subprocess.TimeoutExpired) as exc:
        print("M5_ACTION_REJECTED", type(exc).__name__, str(exc)[:300])
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
