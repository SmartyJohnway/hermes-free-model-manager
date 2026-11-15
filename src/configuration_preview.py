"""M4 safe preview only. Uses Manager draft or validated AI proposal, never Hermes writes."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

from inventory import default_hermes_home
from manager_ai_exchange import (
    SOURCE_PATH, REQUEST_PATH, RESPONSE_PATH, import_recommendation, read_catalog,
)
from manager_ui_model import load_hermes_selection

ROOT = Path(__file__).resolve().parents[1]
DRAFT_PATH = ROOT / "state" / "manager_draft.json"


def _pair(entry: object) -> tuple[str, str]:
    if not isinstance(entry, dict):
        raise ValueError("invalid model entry")
    p, m = entry.get("provider"), entry.get("model")
    if not isinstance(p, str) or not isinstance(m, str) or not p or not m:
        raise ValueError("invalid provider or model")
    return p, m


def preview_from_draft(draft: dict, baseline: dict, catalog: dict) -> dict:
    if draft.get("schema") != "hermes-free-manager-selection.v1":
        raise ValueError("invalid Manager draft schema")
    if draft.get("status") != "DRAFT_ONLY_NO_HERMES_WRITES":
        raise ValueError("Manager draft status invalid")
    if draft.get("baseline_config_sha256") != baseline["config_sha256"]:
        raise ValueError("Hermes settings changed; save a new Manager draft")
    if draft.get("catalog_snapshot_utc") != catalog.get("source_snapshot_at_utc"):
        raise ValueError("catalog snapshot changed; save a new Manager draft")
    main = _pair(draft.get("primary"))
    fallback_source = draft.get("fallback")
    if not isinstance(fallback_source, list) or len(fallback_source) > 30:
        raise ValueError("invalid draft fallback array")
    proposed = [_pair(x) for x in fallback_source]
    allowed = {(x["provider"], x["model"]): x for x in catalog["rows"]}
    if main not in allowed and main != baseline["primary"]:
        raise ValueError("draft primary is not in current catalog")
    if any(x not in allowed for x in proposed):
        raise ValueError("draft fallback includes model absent from catalog")
    if len(proposed) != len(set(proposed)):
        raise ValueError("duplicate fallback in draft")
    if main in proposed:
        raise ValueError("draft fallback contains primary")
    existing = baseline["fallback"]
    return {
        "mode": "M4_READ_ONLY_DRY_RUN",
        "source": "USER_APPROVED_MANAGER_DRAFT",
        "ready_for_production_apply": False,
        "primary_before": baseline["primary"],
        "primary_after": main,
        "primary_changed": main != baseline["primary"],
        "fallback_before": existing,
        "fallback_after": proposed,
        "fallback_removed": [x for x in existing if x not in proposed],
        "fallback_added": [x for x in proposed if x not in existing],
        "fallback_reordered": (proposed != existing),
        "conditional_fallback": [
            {"provider": p, "model": m, "free_evidence": allowed[(p, m)]["free_evidence"]}
            for p, m in proposed
            if allowed[(p, m)]["free_evidence"] not in
            {"published_zero_price", "published_free_list"}
        ],
        "baseline_config_sha256": baseline["config_sha256"],
        "catalog_snapshot_utc": catalog["source_snapshot_at_utc"],
        "no_config_writes": True,
    }


def preview_from_ai(home: Path, *, source: Path = SOURCE_PATH) -> dict:
    """Validate the current AI proposal; this is not user approval."""
    suggestion = import_recommendation(source, home, REQUEST_PATH, RESPONSE_PATH)
    baseline = load_hermes_selection(home)
    catalog, _ = read_catalog(source)
    pseudo_draft = {
        "schema": "hermes-free-manager-selection.v1",
        "status": "DRAFT_ONLY_NO_HERMES_WRITES",
        "baseline_config_sha256": baseline["config_sha256"],
        "catalog_snapshot_utc": catalog["source_snapshot_at_utc"],
        "primary": {"provider": suggestion["primary"][0], "model": suggestion["primary"][1]},
        "fallback": suggestion["fallback"],
    }
    preview = preview_from_draft(pseudo_draft, baseline, catalog)
    preview["source"] = "AI_PROPOSAL_NOT_YET_SAVED_BY_USER"
    return preview


def main() -> int:
    parser = argparse.ArgumentParser(description="M4 credential-free Hermes delta preview")
    parser.add_argument("--hermes-home", type=Path, default=default_hermes_home())
    parser.add_argument("--source", type=Path, default=SOURCE_PATH)
    parser.add_argument("--draft", type=Path, default=DRAFT_PATH)
    parser.add_argument("--ai-proposal", action="store_true",
                        help="Only preview the AI proposal; it is not user approval")
    args = parser.parse_args()
    try:
        if args.ai_proposal:
            result = preview_from_ai(args.hermes_home, source=args.source)
        else:
            draft = json.loads(args.draft.read_text(encoding="utf-8"))
            baseline = load_hermes_selection(args.hermes_home)
            catalog, _ = read_catalog(args.source)
            result = preview_from_draft(draft, baseline, catalog)
        print("M4 PREVIEW", result["source"], "(no Hermes writes)")
        print("Primary:", "/".join(result["primary_before"]), "->",
              "/".join(result["primary_after"]),
              "| changed:", result["primary_changed"])
        print("Fallback current:", len(result["fallback_before"]),
              "proposed:", len(result["fallback_after"]))
        for i, (p, m) in enumerate(result["fallback_after"], 1):
            print(f"  {i}. {p} / {m}")
        print("Added:", len(result["fallback_added"]),
              "Removed:", len(result["fallback_removed"]),
              "Reordered:", result["fallback_reordered"])
        print("Conditional free-usage warnings:", len(result["conditional_fallback"]))
        print("PRODUCTION APPLY NOT ENABLED; ISOLATED APPLY/ROLLBACK TESTED; HERMES UNCHANGED")
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print("M4 PREVIEW REJECTED:", str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
