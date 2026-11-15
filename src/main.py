"""Development-only CLI: snapshots and plans, never writes to Hermes."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from inventory import default_hermes_home, read_inventory
from provider_catalog import read_m1_catalog
from public_discovery import discover_public
from provider_accounts import inspect_accounts
from provider_probing import plan_probes, run_bounded_probes
from provider_ollama import inspect_local_ollama
from provider_nous import discover_nous_public
from provider_cloudflare import inspect_cloudflare
from eligibility_catalog import discover_vendor_policies, build_eligibility_projection

ROOT = Path(__file__).resolve().parents[1]


def run_scan(home: Path, report: Path) -> dict:
    """Local model inventory only; all fallback ranking belongs to ChatGPT + user."""
    allowed = (ROOT / "reports").resolve()
    if not report.resolve().is_relative_to(allowed):
        raise ValueError("report output must stay inside the R&D reports directory")
    data = read_inventory(home)
    counts = dict(sorted(Counter(m["eligibility"] for m in data["models"]).items()))
    result = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "OFFLINE_INVENTORY_AI_HUMAN_RECOMMENDATION_ONLY",
        "not_live_verified": True,
        "eligible_for_auto_apply": False,
        "recommendation_mode": "CHATGPT_WITH_USER_NO_ALGORITHM",
        "counts": counts,
        "catalog": data,
    }
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def run_m1_catalog(home: Path, report: Path, *, refresh_public: bool = False) -> dict:
    allowed = (ROOT / "reports").resolve()
    if not report.resolve().is_relative_to(allowed):
        raise ValueError("M1 report output must stay inside the R&D reports directory")
    public = discover_public() if refresh_public else {}
    result = read_m1_catalog(home, public=public)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def run_m2(home: Path, *, refresh_public: bool, probe: bool, max_requests: int) -> dict:
    public = discover_public() if refresh_public else {}
    catalog = read_m1_catalog(home, public=public)
    accounts = inspect_accounts(home)
    cf = inspect_cloudflare(home)
    accounts["cloudflare"] = {
        "status": cf["status"],
        "credential_present": cf.get("credential_present"),
        "workers_ai_authorized": cf.get("workers_ai_authorized", False),
        "models_visible_first_page": cf.get("checks", {}).get("workers_ai_models", {}).get("first_page_models", 0),
        "models_visible_total": cf.get("models_visible", 0),
        "catalog_complete": cf.get("catalog_complete", False),
    }
    nous_free = discover_nous_public() if refresh_public else {"status": "skipped", "models": []}
    plan = plan_probes(catalog, accounts, max_requests=max_requests)
    result = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "M2_READONLY_ACCOUNTS_WITH_OPTIONAL_BUDGETED_SYNTHETIC_PROBE",
        "account_status": accounts,
        "public_catalog_status": catalog["public_refresh"],
        "nous_public_free": nous_free,
        "catalog_free_candidates": catalog["catalog_free_candidates"],
        "fresh_catalog_free_candidates": catalog["fresh_catalog_free_candidates"],
        "proposed_probe_plan": plan,
        "safe_to_auto_apply": False,
        "model_picker_modified": False,
        "hermes_fallback_modified": False,
    }
    if probe:
        result["probe_result"] = run_bounded_probes(
            home, catalog, accounts, ROOT / "state" / "m2_health.json",
            max_requests=max_requests)
    report = ROOT / "reports" / "m2_account_health.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def run_m2_p3(home: Path) -> dict:
    """Read-only current-provider free eligibility, not a fallback writer."""
    public = discover_public()
    catalog = read_m1_catalog(home, public=public)
    accounts = inspect_accounts(home)
    cloudflare = inspect_cloudflare(home)
    accounts["cloudflare"] = {
        "status": cloudflare["status"],
        "credential_present": cloudflare.get("credential_present"),
        "workers_ai_authorized": cloudflare.get("workers_ai_authorized", False),
        "models_visible_first_page": cloudflare.get("checks", {}).get("workers_ai_models", {}).get("first_page_models", 0),
        "models_visible_total": cloudflare.get("models_visible", 0),
        "catalog_complete": cloudflare.get("catalog_complete", False),
    }
    policies = discover_vendor_policies()
    nous = discover_nous_public()
    result = build_eligibility_projection(catalog, accounts, policies, nous, cloudflare)
    result["account_checks"] = {
        provider: {"status": data.get("status"),
                   "listed_model_count": data.get("listed_model_count")}
        for provider, data in accounts.items()
    }
    result["nous_public"] = {"status": nous.get("status"),
                              "count": len(nous.get("models", []))}
    result["other_public_catalogs"] = {
        provider: {"status": x.get("status"), "model_count": len(x.get("models", {}))}
        for provider, x in public.items()
    }
    report = ROOT / "reports" / "eligibility_catalog.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Hermes Free Model Manager - offline R&D only")
    parser.add_argument("action", choices=("scan", "catalog", "accounts", "probe-free", "probe-ollama", "eligibility"))
    parser.add_argument("--hermes-home", type=Path, default=default_hermes_home())
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--refresh-public", action="store_true", help="GET two allowlisted public catalogs; no credentials")
    parser.add_argument("--max-requests", type=int, choices=(1, 2, 3), default=2,
                        help="Only relevant to probe-free; limits one CLI run, not UTC-day quota")
    parser.add_argument("--confirm-synthetic-probes", action="store_true",
                        help="Required to run bounded free-model POST probes")
    args = parser.parse_args()
    if args.action == "eligibility":
        try:
            result = run_m2_p3(args.hermes_home)
        except Exception as exc:
            print(f"M2 P3 FAILED (Hermes unchanged): {type(exc).__name__}")
            return 2
        print("M2 P3 | READ-ONLY vendor free eligibility research")
        for provider, count in result["providers"].items():
            print(f"  {provider}: {count} free/plan/rate-limit CANDIDATES")
        for provider, record in result["policy_discovery"].items():
            print(f"  Official policy {provider}: {record['status']}")
        print(f"  Cloudflare: {result['cloudflare']['status']}")
        print(f"  Public Nous: {result['nous_public']['status']}")
        print(f"  Total candidate records: {result['candidate_count']}")
        print(f"  Hermes Tool Calling verified by this scan: {result['tool_verified_count']}")
        print("  Report: reports/eligibility_catalog.json")
        print("NO model inference, payments, Hermes config/picker/fallback writes, or scheduler changes.")
        return 0
    if args.action == "probe-ollama":
        if not args.confirm_synthetic_probes:
            print("Refusing local inference without --confirm-synthetic-probes.")
            return 2
        result = inspect_local_ollama()
        report = ROOT / "reports" / "m2_local_ollama.json"
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("Ollama M2 local tool probe:", result.get("status"))
        print("Report:", report)
        print("No Hermes config changes or external model requests.")
        return 0 if result.get("status") == "local_tools_verified" else 1
    if args.action in ("accounts", "probe-free"):
        live = args.action == "probe-free"
        if live and not args.confirm_synthetic_probes:
            print("Refusing live model inference without --confirm-synthetic-probes.")
            return 2
        try:
            result = run_m2(args.hermes_home, refresh_public=(args.refresh_public or live),
                            probe=live, max_requests=args.max_requests)
        except Exception as exc:
            print(f"M2 FAILED (Hermes unchanged): {type(exc).__name__}")
            return 2
        print("Hermes M2 account/probe research (no Hermes changes)")
        for provider, status in result["account_status"].items():
            detail = status.get("free_daily", {}) if provider == "openrouter" else {}
            print(f"  {provider}: {status['status']}"
                  + (f" (remaining free quota: {detail.get('remaining', 'unknown')})" if detail else ""))
        print(f"Fresh catalog-free candidates: {result['fresh_catalog_free_candidates']}")
        print(f"Proposed bounded probes: {len(result['proposed_probe_plan'])}")
        print(f"Nous public free showcase: {result['nous_public_free']['status']}"
              + (f" ({len(result['nous_public_free']['models'])} model IDs)" if result['nous_public_free']['status'] == 'ok' else ''))
        if live:
            for row in result["probe_result"]["new_probes"]:
                print(f"  {row['provider']} / {row['model']}: {row['result']}"
                      + (f" ({row.get('reason')})" if row.get("reason") else ""))
            print(f"Today's reserved request slots: {result['probe_result']['request_slots_used_today']}")
        print("Sanitized report: reports/m2_account_health.json")
        print("No provider credentials, response content or prompts are saved.")
        return 0
    if args.action == "catalog":
        report = args.report or ROOT / "reports" / "provider_catalog.json"
        try:
            result = run_m1_catalog(args.hermes_home, report, refresh_public=args.refresh_public)
        except Exception as exc:
            print(f"M1 DISCOVERY FAILED (Hermes unchanged): {type(exc).__name__}")
            return 2
        print("M1 free model catalog / RESEARCH ONLY")
        print(f"Provider groups: {len(result['providers'])}; model records: {result['total']}")
        print(f"Catalog-priced free candidates: {result['catalog_free_candidates']}")
        print(f"Candidates with fresh catalog-price evidence: {result['fresh_catalog_free_candidates']}")
        print(f"Account/inference validated: {result['currently_usable_verified']}")
        for provider, info in result["providers"].items():
            if info["models"]:
                print(f"  {provider}: {info['models']} models, {info['catalog_free_candidates']} priced free")
        for provider, info in result["public_refresh"].items():
            print(f"  Public GET {provider}: {info['status']} ({info['model_count']} models)")
        print(f"Report: {report}")
        print("NO Hermes writes, API keys, completion calls, or fallback changes.")
        return 0
    if args.refresh_public:
        print("--refresh-public is only available for 'catalog'")
        return 2
    report = args.report or ROOT / "reports" / "latest_inventory.json"
    try:
        result = run_scan(args.hermes_home, report)
    except Exception as exc:
        print(f"SCAN FAILED (Hermes unchanged): {type(exc).__name__}: {exc}")
        return 2
    data = result["catalog"]
    print("Hermes Free Model Manager | OFFLINE R&D scan")
    print(f"Primary kept: {data['primary']['provider']} / {data['primary']['model']}")
    print(f"Cached provider rows: {len(data['cached_model_counts'])}")
    print(f"Cached model rows: {len(data['models'])}")
    print(f"Free-price verified in cached catalog: {result['counts'].get('catalog_free', 0)}")
    print(f"Free-looking but unverified: {result['counts'].get('free_tag_unverified', 0)}")
    print("Fallback selection: user + ChatGPT (Manager does not rank models)")
    print("AI guide: docs/AI_WORKFLOW.md")
    print(f"Report: {report}")
    print("No network probes; no changes to Hermes, API keys, model picker or fallback.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
