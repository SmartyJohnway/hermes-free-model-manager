"""Manager-owned free catalog. M2 candidates are visible without Tool Calling tests.

This is an offline R&D projection, NOT a Hermes Desktop picker or setting writer.
No HTTP, no inference, no credential loading, no cost-incurring operations.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
EVIDENCE_DOC = "docs/EVIDENCE_PROVENANCE.md"
FOLLOWUP_DOC = "docs/EVIDENCE_PROVENANCE.md"

# Browser evidence is a dated observation, NOT a permanent entitlement assertion.
BROWSER_ACCOUNT_EVIDENCE_20261113 = {}  # Private account observations excluded

FREE_KIND = {
    "fresh_zero_price_catalog": "published_zero_price",
    "official_zero_price": "published_zero_price",
    "official_public_free_list": "published_free_list",
    "rate_limited_prototype_free": "prototype_free_only",
    "free_daily_neuron_allowance_candidate": "free_allowance_candidate",
    "free_plan_candidate": "free_plan_candidate",
    "free_default_plan_candidate": "free_plan_candidate",
}


def build_manager_catalog(p3: dict, *, search: str = "",
                          provider: str = "", tool_verified: set[tuple[str, str]] | None = None) -> dict:
    """Show every M2 free candidate; tool proofs add a badge, never gate inclusion."""
    q = search.strip().casefold()
    wanted = provider.strip().casefold()
    tool_verified = tool_verified or set()
    all_rows = []
    seen = set()
    for source in p3.get("candidates", []):
        if not isinstance(source, dict):
            continue
        p, m = source.get("provider"), source.get("model")
        if not isinstance(p, str) or not isinstance(m, str) or not p or not m:
            continue
        if (p, m) in seen or (wanted and p.casefold() != wanted):
            continue
        if q and q not in (p + " " + m).casefold():
            continue
        kind = FREE_KIND.get(source.get("eligibility"))
        if not kind:
            continue  # Unknown/paid cannot become a free candidate.
        seen.add((p, m))
        all_rows.append({
            "provider": p, "model": m, "free_evidence": kind,
            "catalog_accessible": bool(source.get("model_listed")),
            "evidence_url": source.get("evidence", ""),
            "billing_guard": source.get("billing_guard", ""),
            "price_marked_free": (
                kind == "published_zero_price" and bool(source.get("price_confirmed"))
            ),
            "catalog_snapshot_utc": p3.get("generated_at_utc"),
            "policy_observed_at": (p3.get("policy_discovery", {})
                                   .get(p, {}).get("observed_at")),
            "catalog_complete": (p3.get("cloudflare", {}).get("catalog_complete")
                                 if p == "cloudflare" else None),
            # Source flag means catalog access, not per-model account-free entitlement.
            "provider_catalog_auth": bool(source.get("account_authorized")),
            "per_model_free_entitlement_verified": False,
            "account_ui_evidence_20261113": BROWSER_ACCOUNT_EVIDENCE_20261113.get(p),
            "evidence_reference": (FOLLOWUP_DOC if p in {"z-ai", "nvidia"} else EVIDENCE_DOC),
            "tool_calling": ("tested_pass" if (p, m) in tool_verified
                             else "not_tested_or_no_positive_proof"),
            "visible_in_manager": True,
            "auto_apply_to_hermes": False,
        })
    all_rows.sort(key=lambda r: (r["provider"], r["model"]))
    return {
        "mode": "MANAGER_OWN_FULL_CANDIDATE_CATALOG_NO_TOOL_GATE",
        "source_snapshot_at_utc": p3.get("generated_at_utc"),
        "tool_calling_is_optional": True,
        "no_network_or_inference": True,
        "rows": all_rows,
        "provider_counts": dict(sorted(Counter(r["provider"] for r in all_rows).items())),
        "count": len(all_rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Manager catalog from M2 snapshot")
    parser.add_argument("--search", default="")
    parser.add_argument("--provider", default="")
    parser.add_argument("--all-models", action="store_true", help="Print each matching model ID")
    args = parser.parse_args()
    source = PROJECT / "reports" / "eligibility_catalog.json"
    try:
        p3 = json.loads(source.read_text(encoding="utf-8"))
        output = build_manager_catalog(p3, search=args.search, provider=args.provider)
    except (OSError, ValueError, TypeError) as exc:
        print("Catalog unavailable:", type(exc).__name__)
        return 2
    print("Manager free model catalog (R&D candidates, no inference)")
    print("Snapshot UTC:", output["source_snapshot_at_utc"])
    for p, count in output["provider_counts"].items():
        print(f"  {p}: {count}")
    print("Listed:", output["count"])
    if args.all_models:
        for row in output["rows"]:
            print(" ", row["provider"], "/", row["model"], "|", row["free_evidence"],
                  "| Tool Calling:", row["tool_calling"])
    print("No Hermes configuration or Desktop modifications.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
