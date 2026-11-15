"""Human-in-the-loop AI recommendation exchange for Hermes Free Model Manager.

Deterministic validation only. AI determines ranking/reasons; Manager never ranks.
Files contain sanitized catalog metadata, not credentials or full Hermes config.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile

from inventory import default_hermes_home
from manager_catalog import build_manager_catalog
from tool_results import load_manual_results
from tool_evidence_policy import classify, WARNING_DAYS, EXPIRE_DAYS
from manager_ui_model import load_hermes_selection

ROOT = Path(__file__).resolve().parents[1]
REQUEST_PATH = ROOT / "state" / "ai_recommendation_request.json"
RESPONSE_PATH = ROOT / "state" / "ai_recommendation.json"
TEMPLATE_PATH = ROOT / "state" / "ai_recommendation_template.json"
SOURCE_PATH = ROOT / "reports" / "eligibility_catalog.json"
GUIDE_PATH = ROOT / "docs" / "AI_WORKFLOW.md"
REQ_SCHEMA = "hermes-free-manager-ai-request.v1"
REC_SCHEMA = "hermes-free-manager-ai-recommendation.v1"
MAX_RECOMMENDED = 30


def _positive_tool_proofs() -> set[tuple[str, str]]:
    """Read only existing historical test results; never initiate an API call."""
    path = ROOT / "reports" / "tool_calling_evidence.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {
        (r["provider"], r["model"])
        for r in data.get("results", []) if isinstance(r, dict)
        and r.get("result") == "pass"
        and r.get("tool_call_confirmed") is True
        and isinstance(r.get("provider"), str)
        and isinstance(r.get("model"), str)
    }


def read_catalog(source: Path) -> tuple[dict, str]:
    raw = source.read_bytes()
    if len(raw) > 16_000_000:
        raise ValueError("catalog snapshot too large")
    data = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(data, dict) or not isinstance(data.get("candidates"), list):
        raise ValueError("invalid M2 snapshot")
    catalog = build_manager_catalog(
        data, tool_verified=_positive_tool_proofs())
    evidence = load_manual_results(source.parent / "manual_tool_calling")
    for row in catalog["rows"]:
        pair = row["provider"], row["model"]
        row["manual_tool_result"] = evidence["latest"].get(pair)
        row["manual_last_pass"] = evidence["last_pass"].get(pair)
    return catalog, sha256(raw).hexdigest()


def _model_pair(value: object, *, label: str) -> tuple[str, str]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    provider, model = value.get("provider"), value.get("model")
    if (not isinstance(provider, str) or not isinstance(model, str)
            or not provider.strip() or not model.strip()
            or len(provider) > 100 or len(model) > 256):
        raise ValueError(f"{label} missing valid provider/model")
    return provider, model


def _normal_reason(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 1200:
        raise ValueError(f"{label} requires a concise explanation")
    return value.strip()


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".hfm-ai-", suffix=".tmp",
                               dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def make_request(source: Path, home: Path, *,
                 preferred_primary: tuple[str, str] | None = None) -> dict:
    catalog, snapshot_hash = read_catalog(source)
    baseline = load_hermes_selection(home)
    current = baseline["primary"]
    primary = preferred_primary or current
    candidates = {(r["provider"], r["model"]) for r in catalog["rows"]}
    if (preferred_primary is not None and preferred_primary not in candidates
            and preferred_primary != current):
        raise ValueError("preferred primary is not in catalog or existing Hermes primary")
    identity = {
        "catalog_sha256": snapshot_hash,
        "config_sha256": baseline["config_sha256"],
        "preferred_primary": list(primary),
    }
    request_id = sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    rows = [
        {
            "provider": r["provider"],
            "model": r["model"],
            "free_evidence": r["free_evidence"],
            "price_marked_free": r["price_marked_free"],
            "account_ui_evidence_20261113": r["account_ui_evidence_20261113"],
            "tool_calling": r["tool_calling"],
            "latest_manual_tool_calling": (
                {k: r["manual_tool_result"][k] for k in (
                    "label", "result", "reason", "http_status",
                    "attempted_at_utc", "tool_call_confirmed", "report_name"
                )} if r.get("manual_tool_result") else None),
            "historical_tool_calling_pass": (
                bool(r.get("manual_last_pass"))
                or r["tool_calling"] == "tested_pass"),
            "tool_validation": classify(r),
            "catalog_accessible": r["catalog_accessible"],
            "catalog_complete": r["catalog_complete"],
            "catalog_snapshot_utc": r["catalog_snapshot_utc"],
            "policy_observed_at": r["policy_observed_at"],
            "evidence_reference": r["evidence_reference"],
        }
        for r in catalog["rows"]
    ]
    return {
        "schema": REQ_SCHEMA,
        "purpose": "AI proposes; user approves; Manager validates; NO Hermes writes",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "request_id": request_id,
        "catalog_sha256": snapshot_hash,
        "baseline_config_sha256": baseline["config_sha256"],
        "catalog_snapshot_utc": catalog["source_snapshot_at_utc"],
        "current_primary": {"provider": current[0], "model": current[1]},
        "preferred_primary": {"provider": primary[0], "model": primary[1]},
        "current_fallback": [{"provider": p, "model": m}
                             for p, m in baseline["fallback"]],
        "candidate_count": len(rows),
        "provider_counts": catalog["provider_counts"],
        "candidate_models": rows,
        "tool_validation_policy": {
            "warn_after_days": WARNING_DAYS,
            "expire_after_days": EXPIRE_DAYS,
            "recommendation_requires_tool_pass": False,
            "include_new_untested_models": True,
            "post_only_after_user_confirmation": True,
        },
        "account_evidence_documents": [
            "docs/EVIDENCE_PROVENANCE.md",
            "docs/EVIDENCE_PROVENANCE.md",
            "docs/EVIDENCE_PROVENANCE.md",
        ],
        "recommendation_guide": "docs/AI_WORKFLOW.md",
        "cautions": [
            "Candidate is not proof of current account free entitlement.",
            "No inference or mass Tool Calling testing.",
            "Don't use paid wallet, unknown-price routes, or deprecated endpoints.",
            "Explain provider/model selection, quota limits, shared model families.",
            "Explicitly recommend 0..30 fallback entries in preferred order.",
            "Never restrict recommendations to Tool Calling PASS models; evaluate newly listed free candidates.",
            "PASS older than 7 days is suggested for retest; older than 30 days is stale evidence, not FAIL.",
            "Tool Calling tests are optional manual POST with user approval; no automatic batch.",
        ],
    }


def export_request(source: Path, home: Path, path: Path = REQUEST_PATH, *,
                   preferred_primary: tuple[str, str] | None = None) -> dict:
    payload = make_request(source, home, preferred_primary=preferred_primary)
    _atomic_json(path, payload)
    # A ready-to-fill, request-bound template; do not overwrite the AI's response.
    template = {
        "schema": REC_SCHEMA,
        "request_id": payload["request_id"],
        "catalog_sha256": payload["catalog_sha256"],
        "baseline_config_sha256": payload["baseline_config_sha256"],
        "summary": "請 ChatGPT 填寫具證據的推薦摘要",
        "primary": None,
        "fallback": [],
    }
    _atomic_json(path.parent / TEMPLATE_PATH.name, template)
    return payload


def validate_recommendation(request: dict, proposal: dict) -> dict:
    """Strict local data validation. No code, API or Hermes config mutation."""
    if not isinstance(request, dict) or request.get("schema") != REQ_SCHEMA:
        raise ValueError("invalid AI request")
    if not isinstance(proposal, dict) or proposal.get("schema") != REC_SCHEMA:
        raise ValueError("invalid AI recommendation schema")
    for key in ("request_id", "catalog_sha256", "baseline_config_sha256"):
        if proposal.get(key) != request.get(key):
            raise ValueError(f"AI proposal is stale or mismatched: {key}")
    allowed = {(r["provider"], r["model"]): r
               for r in request["candidate_models"] if isinstance(r, dict)}
    selected = proposal.get("primary")
    if selected is None:
        # AI may leave preferred main untouched; recommendation remains a fallback-only proposal.
        main = _model_pair(request["preferred_primary"], label="preferred_primary")
        primary_reason = "保留使用者在 Manager 中選擇的主模型"
        primary_suggested = False
    else:
        main = _model_pair(selected, label="primary")
        primary_reason = _normal_reason(selected.get("reason"), label="primary.reason")
        primary_suggested = True
    # Existing primary may not be a free-catalog entry; don't force an AI change.
    old_primary = _model_pair(request["current_primary"], label="current_primary")
    if main not in allowed and main != old_primary:
        raise ValueError("AI primary not in current free candidate catalog")
    original = proposal.get("fallback")
    if not isinstance(original, list) or len(original) > MAX_RECOMMENDED:
        raise ValueError("fallback must be an ordered list of at most 30 models")
    validated = []
    seen = set()
    for index, item in enumerate(original):
        pair = _model_pair(item, label=f"fallback[{index}]")
        if pair not in allowed:
            raise ValueError(f"fallback[{index}] not in exported candidate catalog")
        if pair == main:
            raise ValueError(f"fallback[{index}] duplicates primary")
        if pair in seen:
            raise ValueError(f"fallback[{index}] duplicates another fallback")
        seen.add(pair)
        reason = _normal_reason(item.get("reason"), label=f"fallback[{index}].reason")
        row = allowed[pair]
        validated.append({
            "provider": pair[0], "model": pair[1], "reason": reason,
            "free_evidence": row["free_evidence"],
            "needs_conditional_review": row["free_evidence"] not in (
                "published_zero_price", "published_free_list"),
        })
    summary = _normal_reason(proposal.get("summary"), label="summary")
    return {
        "request_id": request["request_id"],
        "primary": main,
        "primary_reason": primary_reason,
        "primary_suggested": primary_suggested,
        "fallback": validated,
        "summary": summary,
        "conditional_count": sum(x["needs_conditional_review"] for x in validated),
    }


def import_recommendation(source: Path, home: Path,
                          request_path: Path = REQUEST_PATH,
                          response_path: Path = RESPONSE_PATH) -> dict:
    """Require matching current snapshot + Hermes config + request identity."""
    request = json.loads(request_path.read_text(encoding="utf-8"))
    live_request = make_request(
        source, home,
        preferred_primary=_model_pair(request["preferred_primary"], label="preferred_primary"))
    for key in ("request_id", "catalog_sha256", "baseline_config_sha256"):
        if live_request[key] != request.get(key):
            raise ValueError("request no longer matches live catalog/config; export again")
    proposal = json.loads(response_path.read_text(encoding="utf-8"))
    return validate_recommendation(request, proposal)


def main() -> int:
    parser = argparse.ArgumentParser(description="Manager AI recommendation exchange")
    parser.add_argument("action", choices=("export", "validate"))
    parser.add_argument("--source", type=Path, default=SOURCE_PATH)
    parser.add_argument("--hermes-home", type=Path, default=default_hermes_home())
    parser.add_argument("--request", type=Path, default=REQUEST_PATH)
    parser.add_argument("--response", type=Path, default=RESPONSE_PATH)
    args = parser.parse_args()
    try:
        if args.action == "export":
            value = export_request(args.source, args.hermes_home, args.request)
            print("AI_INPUT_READY", args.request, "models", value["candidate_count"])
            print("AI_GUIDE", GUIDE_PATH)
            print("AI_OUTPUT_TARGET", args.response)
            print("AI_RESPONSE_TEMPLATE", args.request.parent / TEMPLATE_PATH.name)
        else:
            validated = import_recommendation(
                args.source, args.hermes_home, args.request, args.response)
            print("AI_PROPOSAL_VALID", "primary", "/".join(validated["primary"]),
                  "fallback", len(validated["fallback"]))
        return 0
    except (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError) as exc:
        print("AI_EXCHANGE_REJECTED", str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
