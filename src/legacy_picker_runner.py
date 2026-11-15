"""R&D-only M3 picker preview from confirmed Free API results. No UI writes."""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from inventory import default_hermes_home, read_inventory
from legacy_picker import picker_projection

ROOT = Path(__file__).resolve().parents[1]


def desktop_sync_path() -> Path:
    """Separate app-specific data: do not alter Hermes installed config/state."""
    base = Path(os.environ["LOCALAPPDATA"]) if os.environ.get("LOCALAPPDATA") else (
        Path.home() / ".local" / "share"
    )
    return base / "Hermes-Free-Model-Manager" / "verified-free-models.json"


def publish_verified_snapshot(result: dict, path: Path | None = None) -> dict:
    """Atomically export only verified identities. No API keys or reports."""
    destination = path or desktop_sync_path()
    source = result.get("selectable", [])
    # Do NOT carry over provider quota/history, active model or debug fields.
    rows = []
    seen = set()
    for candidate in source:
        if (candidate.get("selectable") is not True
                or candidate.get("verification") != "real_two_stage_tool_loop"):
            continue
        provider, model = candidate.get("provider"), candidate.get("model")
        if (not isinstance(provider, str) or not provider
                or not isinstance(model, str) or not model):
            continue
        key = (provider, model)
        if key not in seen:
            seen.add(key)
            rows.append({"provider": provider, "model": model,
                         "selectable": True,
                         "verification": "real_two_stage_tool_loop"})
    rows.sort(key=lambda row: (row["provider"], row["model"]))
    payload = {"mode": "M3_PICKER_STRICT_FREE_PREVIEW_NO_UI_WRITES",
               "selectable": rows,
               "generated_at_utc": datetime.now(timezone.utc).isoformat()}
    destination.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    # If identities haven't changed, avoid unnecessary disk writes/UI redraws.
    if destination.exists():
        try:
            previous = json.loads(destination.read_text(encoding="utf-8"))
            if (previous.get("mode") == payload["mode"]
                    and previous.get("selectable") == rows):
                return {"result": "unchanged", "count": len(rows),
                        "path": str(destination)}
        except (OSError, ValueError, AttributeError):
            pass
    # Temporary file is in the same directory for atomic rename on Windows.
    fd, temp = tempfile.mkstemp(prefix=".m3_", suffix=".tmp",
                                dir=str(destination.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, destination)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return {"result": "updated", "count": len(rows),
            "path": str(destination)}


def run_preview(home: Path, *, search: str = "", favorites=None,
                custom_slug: str | None = None) -> dict:
    p3_path = ROOT / "reports" / "eligibility_catalog.json"
    state_path = ROOT / "state" / "m2_p4_budget.json"
    if not p3_path.is_file() or not state_path.is_file():
        raise FileNotFoundError("M2 P3/P5 evidence missing")
    p3 = json.loads(p3_path.read_text(encoding="utf-8"))
    state = json.loads(state_path.read_text(encoding="utf-8"))
    inventory = read_inventory(home)
    primary = (inventory["primary"]["provider"], inventory["primary"]["model"])
    result = picker_projection(p3, state, primary, search=search,
                               favorites=favorites, custom_slug=custom_slug)
    output = ROOT / "reports" / "m3_picker_preview.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                      encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="M3 Free model picker PREVIEW ONLY")
    parser.add_argument("--hermes-home", type=Path, default=default_hermes_home())
    parser.add_argument("--search", default="")
    parser.add_argument("--favorite", action="append", default=[],
                        help="provider::model, may be repeated")
    parser.add_argument("--custom-slug")
    parser.add_argument("--sync", action="store_true",
                        help="Publish a sanitized, atomic Desktop snapshot.")
    args = parser.parse_args()
    favorites = [tuple(x.split("::", 1)) for x in args.favorite if "::" in x]
    try:
        result = run_preview(args.hermes_home, search=args.search,
                             favorites=favorites, custom_slug=args.custom_slug)
    except (OSError, ValueError, KeyError) as exc:
        print("M3 preview failed:", type(exc).__name__)
        return 2
    print("M3 PICKER / R&D STRICT FREE PREVIEW")
    for row in result["selectable"]:
        print("  VERIFIED FREE:", row["provider"], "/", row["model"],
              ("[FAVORITE]" if row["favorite"] else ""))
    if result["active_model"]:
        current = result["active_model"]
        print("  KEEP CURRENT (not automatically selectable):",
              current["provider"], "/", current["model"])
    print("  Unverified models don't become selectable through favorites/search/custom slug.")
    print("  Selectable count:", result["selectable_count"])
    print("  Report: reports/m3_picker_preview.json")
    if args.sync:
        status = publish_verified_snapshot(result)
        print("  Desktop verified model snapshot:", status["result"],
              "| models:", status["count"])
    print("  NO Hermes Desktop, primary, fallback, config or scheduler changes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
