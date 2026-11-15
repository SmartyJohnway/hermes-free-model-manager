"""M2 P4 alias and upstream-model lineage review, not a fallback selector."""
from __future__ import annotations

from collections import defaultdict
import re


def model_lineage(provider: str, model: str) -> str:
    """Conservative underlying model identity; unknown routers are never distinct proof."""
    name = str(model).lower()
    if name == "kilo-auto/free":
        return "routing-unknown:kilo-auto"
    if name.startswith("@cf/zai-org/"):
        return "zai/" + name.split("/", 2)[2]
    if provider == "z-ai" and re.fullmatch(r"glm-[0-9][a-z0-9.-]+", name):
        return "zai/" + name
    if name.startswith("@cf/"):
        return name.removeprefix("@cf/")
    if name.startswith("nvidia/") and provider == "nvidia":
        return name
    if "/" in name:
        return name
    return provider + "/" + name


def lineage_overlaps(rows: list[dict]) -> dict:
    clusters: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        provider, model = str(r.get("provider", "")), str(r.get("model", ""))
        if provider and model:
            clusters[model_lineage(provider, model)].add(provider)
    collisions = {model: sorted(providers) for model, providers in clusters.items()
                  if len(providers) > 1}
    return {"shared_upstream_model_count": len(collisions),
            "shared_upstream_models": collisions,
            "note": ("Different API providers can have separate capacity while the "
                     "same upstream model creates correlated model-level risk. "
                     "Auto routers have unknown lineage and cannot prove diversity.")}


def summarize_live_probe_results(results: list[dict]) -> dict:
    return {
        "tool_pass_count": sum(1 for x in results if x.get("tool_call_confirmed")),
        "api_rate_limit_count": sum(1 for x in results if x.get("result") == "cooldown"),
        "provider_results": [{
            "provider": x.get("provider"), "model": x.get("model"),
            "result": x.get("result"), "reason": x.get("reason"),
            "http_status": x.get("http_status"),
            "tool_call_confirmed": x.get("tool_call_confirmed", False),
        } for x in results],
        "ready_for_hermes_auto_apply": False,
    }
