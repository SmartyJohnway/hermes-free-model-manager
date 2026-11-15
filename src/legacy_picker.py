"""M3 R&D picker projection. No Hermes UI/config modification or network calls."""
from __future__ import annotations

from probe_policy import can_probe_now

ELIGIBILITY = frozenset(("free_plan_candidate", "free_daily_neuron_allowance_candidate",
                         "official_zero_price", "fresh_zero_price_catalog"))


def picker_projection(p3: dict, state: dict, primary: tuple[str, str], *,
                      search: str = "", favorites: list[tuple[str, str]] | None = None,
                      custom_slug: str | None = None) -> dict:
    """Only genuine free-plan+tool-loop eligible rows are selectable in strict preview.

    Search/favorites/custom can't reintroduce an unverified or paid model.
    Preserve current active choice as non-selectable status when not eligible;
    never silently switch it. User confirmation remains required to select.
    """
    favorites = set(map(tuple, favorites or []))
    candidates = {(r.get("provider"), r.get("model")): r
                  for r in p3.get("candidates", [])
                  if isinstance(r, dict)}
    q = search.casefold().strip()
    eligible = []
    rejected = []
    for r in state.get("p5_results", {}).values():
        if not isinstance(r, dict):
            continue
        p, m = r.get("provider"), r.get("model")
        if not isinstance(p, str) or not isinstance(m, str) or not p or not m:
            continue
        candidate = candidates.get((p, m), {})
        health = state.get("p5_health", {}).get(p + "::" + m, {})
        health_ok, health_reason = can_probe_now(health)
        if (not r.get("tool_round_trip_confirmed")
                or r.get("status") != "loop_pass"
                or not candidate.get("account_authorized")
                or candidate.get("eligibility") not in ELIGIBILITY
                or not health_ok):
            rejected.append({"provider": p, "model": m,
                             "reason": health_reason if not health_ok
                             else "unverified_free_plan_or_tool_loop"})
            continue
        pair = (p, m)
        if q and q not in (p + " " + m).casefold():
            continue
        eligible.append({"provider": p, "model": m, "selectable": True,
                         "favorite": pair in favorites,
                         "active": pair == primary,
                         "verification": "real_two_stage_tool_loop",
                         "billing": "documented_free_plan_subject_to_provider_limits"})
    eligible.sort(key=lambda x: (not x["favorite"], x["provider"], x["model"]))
    current_valid = any((r["provider"], r["model"]) == primary for r in eligible)
    # This is informational; not an actionable escape hatch around strict mode.
    current_display = None if current_valid else {
        "provider": primary[0], "model": primary[1],
        "selectable": False, "active": True,
        "warning": "current_model_preserved_not_in_verified_free_subset",
    }
    return {
        "mode": "M3_PICKER_STRICT_FREE_PREVIEW_NO_UI_WRITES",
        "selectable": eligible,
        "selectable_count": len(eligible),
        "active_model": current_display,
        "filtered_out": rejected,
        "unverified_favorites_cannot_bypass": True,
        "custom_slug_requested": bool(custom_slug),
        "custom_slug_selectable": False,
        "model_picker_modified": False,
        "primary_modified": False,
        "requires_distinct_utc_days": False,
    }
