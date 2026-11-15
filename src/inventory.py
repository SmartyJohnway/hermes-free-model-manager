"""Read-only adapters for locally saved Hermes catalog and metadata caches."""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from core import classify_free


def default_hermes_home() -> Path:
    explicit = os.environ.get("HERMES_HOME")
    if explicit:
        return Path(explicit)
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "hermes"


def read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    return value if isinstance(value, dict) else {}


def normalize_url(value: str) -> str:
    parts = urlsplit(value)
    return (parts.scheme.lower() + "://" + parts.netloc.lower() + parts.path.rstrip("/")) if parts.netloc else ""


def configured_provider_for_cache(cache_key: str, configured: dict) -> str:
    if not cache_key.startswith("custom:"):
        return cache_key
    raw = cache_key[len("custom:"):].split("#", 1)[0]
    target = normalize_url(raw)
    if target:
        for provider, cfg in configured.items():
            if isinstance(cfg, dict) and normalize_url(str(cfg.get("base_url", ""))) == target:
                return str(provider)
    # Unknown custom origin is not silently mapped to a trusted provider.
    return "unmapped-custom"


def _metadata_for_cache(cache_key: str, endpoint_meta: dict, router_meta: dict) -> dict:
    if cache_key == "openrouter":
        return router_meta
    if cache_key.startswith("custom:"):
        url = cache_key[len("custom:"):].split("#", 1)[0]
        for endpoint, record in endpoint_meta.items():
            if normalize_url(endpoint) == normalize_url(url):
                return record.get("models", {}) if isinstance(record, dict) else {}
    return {}


def read_inventory(home: Path) -> dict:
    """Never reads .env, credentials, token fields or network endpoints."""
    config_path = home / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8-sig")) or {}
    if not isinstance(config, dict):
        raise ValueError("Hermes config is not a mapping")
    model_cfg = config.get("model") or {}
    if not isinstance(model_cfg, dict):
        model_cfg = {}
    configured = config.get("providers") or {}
    if not isinstance(configured, dict):
        configured = {}
    cache = read_json(home / "provider_models_cache.json")
    endpoint_meta = read_json(home / "cache" / "endpoint_model_metadata.json")
    router_meta = read_json(home / "cache" / "openrouter_model_metadata.json")
    discovered: dict[tuple[str, str], dict] = {}
    cache_counts: dict[str, int] = {}
    for key, payload in cache.items():
        if not isinstance(payload, dict):
            continue
        provider = configured_provider_for_cache(key, configured)
        if provider == "unmapped-custom":
            continue
        raw_models = payload.get("models") or []
        if not isinstance(raw_models, list):
            continue
        cache_counts[provider] = len(raw_models)
        metadata = _metadata_for_cache(key, endpoint_meta, router_meta)
        for raw in raw_models:
            model = raw if isinstance(raw, str) else (raw.get("id") if isinstance(raw, dict) else None)
            if not isinstance(model, str) or not model:
                continue
            meta = metadata.get(model, {}) if isinstance(metadata, dict) else {}
            pricing = meta.get("pricing") if isinstance(meta, dict) else None
            eligibility = classify_free(model, pricing)
            discovered[(provider, model)] = {
                "provider": provider, "model": model, "eligibility": eligibility,
                "health": "unverified", "evidence": "cached_price" if pricing else "model_name_or_unknown",
            }
    fallback = [
        (str(item.get("provider", "")), str(item.get("model", "")))
        for item in (config.get("fallback_providers") or [])
        if isinstance(item, dict)
    ]
    # No credential values, raw URLs, original config, or cache fingerprint IDs in output.
    return {
        "primary": {"provider": str(model_cfg.get("provider", "")),
                    "model": str(model_cfg.get("default", ""))},
        "configured_provider_names": sorted(configured),
        "cached_model_counts": dict(sorted(cache_counts.items())),
        "models": sorted(discovered.values(), key=lambda r: (r["provider"], r["model"])),
        "existing_fallback": [{"provider": p, "model": m} for p, m in fallback],
        "source": "local Hermes files only; no live probe",
    }

