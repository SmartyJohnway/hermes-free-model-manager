"""M1 read-only cross-provider catalog and free-tier classification."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
import re

import yaml

from inventory import configured_provider_for_cache, normalize_url, read_json
from provider_policy import evaluate_model

CACHE_TTL_SECONDS = 3600
CANONICAL_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.~-]*/[a-zA-Z0-9@_.:/~-]+$")


def _read_config(path: Path) -> dict:
    cfg = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    if not isinstance(cfg, dict):
        raise ValueError("Hermes config must be a mapping")
    return cfg


def _iso(epoch: object) -> str | None:
    try:
        timestamp = float(epoch)
        if timestamp <= 0:
            return None
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _timestamp_for(path: Path, fallback: object = None) -> str | None:
    if fallback is not None:
        output = _iso(fallback)
        if output:
            return output
    return _iso(path.stat().st_mtime) if path.is_file() else None


def _meta_lookup(provider: str, cache_key: str, endpoint_meta: dict, router_meta: dict):
    if provider == "openrouter":
        return router_meta, None
    if cache_key.startswith("custom:"):
        raw = cache_key[len("custom:"):].split("#", 1)[0]
        for endpoint, data in endpoint_meta.items():
            if normalize_url(endpoint) == normalize_url(raw) and isinstance(data, dict):
                models = data.get("models") or {}
                return (models if isinstance(models, dict) else {}), data.get("at")
    return {}, None


def _declared_models(value: object) -> list[str]:
    if isinstance(value, list):
        return [x for x in value if isinstance(x, str) and x.strip()]
    if isinstance(value, dict):
        return [x for x in value if isinstance(x, str) and x.strip()]
    return []


def read_m1_catalog(home: Path, *, public: dict | None = None,
                    now: datetime | None = None) -> dict:
    """Does not import Hermes, touch secrets, update caches, or call the network."""
    now = now or datetime.now(timezone.utc)
    cfg = _read_config(home / "config.yaml")
    configured = cfg.get("providers") or {}
    if not isinstance(configured, dict):
        configured = {}
    fallback = [x for x in cfg.get("fallback_providers", []) if isinstance(x, dict)]
    active_providers = set(configured)
    active_providers.update(str(x.get("provider", "")) for x in fallback)
    cache_path = home / "provider_models_cache.json"
    cached = read_json(cache_path)
    endpoint_file = home / "cache" / "endpoint_model_metadata.json"
    endpoint_meta = read_json(endpoint_file)
    router_file = home / "cache" / "openrouter_model_metadata.json"
    router_meta = read_json(router_file)
    rows: dict[tuple[str, str], dict] = {}
    observed_sources = set()

    def add(provider: str, model: str, *, source: str,
            observed_at: str | None = None, pricing=None,
            price_observed_at: str | None = None,
            is_free_flag=None, billing_mode=None,
            supported_parameters=None, output_modalities=None) -> None:
        if not provider or not model or not isinstance(model, str) or len(model) > 256:
            return
        key = (provider, model)
        if key not in rows:
            rows[key] = {"provider": provider, "model": model, "source": source,
                         "observed_at": observed_at, "pricing": pricing,
                         "price_observed_at": price_observed_at,
                         "is_free_flag": is_free_flag, "billing_mode": billing_mode,
                         "supported_parameters": supported_parameters,
                         "output_modalities": output_modalities}
            return
        current = rows[key]
        weight = {"configured": 0, "fallback_declared": 0, "nous_metadata": 1,
                  "hermes_cached": 2, "public_live": 3}
        if weight.get(source, 0) > weight.get(current["source"], 0):
            rows[key] = {"provider": provider, "model": model, "source": source,
                         "observed_at": observed_at, "pricing": pricing,
                         "price_observed_at": price_observed_at,
                         "is_free_flag": is_free_flag, "billing_mode": billing_mode,
                         "supported_parameters": supported_parameters,
                         "output_modalities": output_modalities}
        elif current["pricing"] is None and isinstance(pricing, dict):
            current["pricing"] = pricing
            current["price_observed_at"] = price_observed_at

    for key, record in cached.items():
        if not isinstance(record, dict) or not isinstance(record.get("models"), list):
            continue
        provider = configured_provider_for_cache(key, configured)
        if provider == "unmapped-custom":
            continue
        observed_sources.add(provider)
        metadata, at = _meta_lookup(provider, key, endpoint_meta, router_meta)
        source_time = _timestamp_for(cache_path, record.get("at"))
        price_time = (_timestamp_for(router_file) if provider == "openrouter"
                      else _timestamp_for(endpoint_file, at) if at is not None else None)
        for value in record["models"]:
            model = value if isinstance(value, str) else (
                value.get("id", "") if isinstance(value, dict) else "")
            if not isinstance(model, str) or not model:
                continue
            meta = metadata.get(model, {}) if isinstance(metadata, dict) else {}
            if not isinstance(meta, dict):
                meta = {}
            add(provider, model, source="hermes_cached", observed_at=source_time,
                pricing=meta.get("pricing"), price_observed_at=price_time,
                is_free_flag=meta.get("isFree"),
                billing_mode=meta.get("billing_mode"),
                supported_parameters=meta.get("supported_parameters"))

    # Nous pricing metadata exists even when provider-model cache has no Nous row.
    # Only canonical zero-priced rows and explicitly configured fallbacks enter.
    if "nous" in active_providers:
        for endpoint, data in endpoint_meta.items():
            if "inference-api.nousresearch.com" not in endpoint or not isinstance(data, dict):
                continue
            models = data.get("models") or {}
            when = _timestamp_for(endpoint_file, data.get("at"))
            fallback_ids = {str(x.get("model")) for x in fallback if x.get("provider") == "nous"}
            if isinstance(models, dict):
                for model, meta in models.items():
                    if not CANONICAL_ID.fullmatch(model) or model.startswith("~"):
                        continue
                    pricing = meta.get("pricing") if isinstance(meta, dict) else None
                    assessment = evaluate_model("nous", model, pricing=pricing)
                    if assessment["eligibility"] != "catalog_free" and model not in fallback_ids:
                        continue
                    add("nous", model, source="nous_metadata", observed_at=when,
                        pricing=pricing, price_observed_at=when,
                        billing_mode=meta.get("billing_mode") if isinstance(meta, dict) else None)
                observed_sources.add("nous")

    for provider, provider_cfg in configured.items():
        if not isinstance(provider_cfg, dict):
            continue
        for model in _declared_models(provider_cfg.get("models")):
            add(provider, model, source="configured")
        default = provider_cfg.get("model") or provider_cfg.get("default_model")
        if isinstance(default, str):
            add(provider, default, source="configured")
    for entry in fallback:
        add(str(entry.get("provider") or ""), str(entry.get("model") or ""),
            source="fallback_declared")
    main_cfg = cfg.get("model") or {}
    if isinstance(main_cfg, dict):
        add(str(main_cfg.get("provider") or ""), str(main_cfg.get("default") or ""),
            source="configured")

    public = public or {}
    public_status = {name: {"status": item.get("status", "unknown"),
                            "observed_at": item.get("observed_at"),
                            "model_count": len(item.get("models") or {}),
                            "error_type": item.get("error_type")}
                     for name, item in public.items() if isinstance(item, dict)}
    for provider, data in public.items():
        if not isinstance(data, dict) or data.get("status") != "ok":
            continue
        latest = data.get("models") or {}
        if not isinstance(latest, dict):
            continue
        observed_sources.add(provider)
        for model, meta in latest.items():
            if not isinstance(meta, dict):
                continue
            add(provider, model, source="public_live", observed_at=data.get("observed_at"),
                pricing=meta.get("pricing"), price_observed_at=data.get("observed_at"),
                is_free_flag=meta.get("isFree"),
                supported_parameters=meta.get("supported_parameters"))

    result = []
    for (provider, model), row in sorted(rows.items()):
        observed_time = row.get("observed_at")
        try:
            age_seconds = max(0, (now - datetime.fromisoformat(observed_time)).total_seconds()) if observed_time else None
        except (TypeError, ValueError):
            age_seconds = None
        freshness = "fresh" if age_seconds is not None and age_seconds <= CACHE_TTL_SECONDS else "stale_or_unknown"
        price_time = row.get("price_observed_at")
        try:
            price_age_seconds = max(0, (now - datetime.fromisoformat(price_time)).total_seconds()) if price_time else None
        except (TypeError, ValueError):
            price_age_seconds = None
        price_freshness = ("fresh" if price_age_seconds is not None and price_age_seconds <= CACHE_TTL_SECONDS
                           else "stale_or_unknown")
        presence = "observed"
        live = public.get(provider)
        if isinstance(live, dict) and live.get("status") == "ok":
            if model not in live.get("models", {}):
                presence = "missing_from_public_refresh"
        assessment = evaluate_model(
            provider, model, pricing=row.get("pricing"),
            is_free_flag=row.get("is_free_flag"),
            billing_mode=row.get("billing_mode"),
            source=row.get("source", "unknown"),
            supported_parameters=row.get("supported_parameters"),
            output_modalities=row.get("output_modalities"))
        public_candidate = assessment["catalog_free_candidate"] and presence == "observed"
        result.append({
            "provider": provider, "model": model,
            "source": row.get("source"), "observed_at": observed_time,
            "freshness": freshness, "price_observed_at": price_time,
            "price_freshness": price_freshness, "presence": presence,
            **assessment,
            "catalog_free_candidate": public_candidate,
        })
    providers = {}
    for provider in sorted(active_providers | observed_sources):
        group = [r for r in result if r["provider"] == provider]
        providers[provider] = {
            "models": len(group),
            "source_types": sorted({r["source"] for r in group}),
            "categories": dict(Counter(r["eligibility"] for r in group)),
            "catalog_free_candidates": sum(1 for r in group if r["catalog_free_candidate"]),
            "fresh_catalog_free_candidates": sum(1 for r in group if r["catalog_free_candidate"]
                                                  and r["freshness"] == "fresh"
                                                  and r["price_freshness"] == "fresh"),
            "health_verified": 0,
        }
    return {
        "generated_at": now.isoformat(),
        "mode": "M1_CATALOG_DISCOVERY_ONLY_NO_INFERENCE_OR_HERMES_WRITES",
        "account_entitlement_verified": False,
        "auto_apply_permitted": False,
        "primary": {
            "provider": str(main_cfg.get("provider", "")) if isinstance(main_cfg, dict) else "",
            "model": str(main_cfg.get("default", "")) if isinstance(main_cfg, dict) else "",
        },
        "public_refresh": public_status,
        "providers": providers,
        "models": result,
        "existing_fallback_evaluation": [
            {"provider": str(e.get("provider", "")), "model": str(e.get("model", "")),
             "eligibility": next((
                 x["eligibility"] for x in result if x["provider"] == e.get("provider")
                 and x["model"] == e.get("model")), "unknown")}
            for e in fallback
        ],
        "total": len(result),
        "catalog_free_candidates": sum(1 for r in result if r["catalog_free_candidate"]),
        "fresh_catalog_free_candidates": sum(1 for r in result if r["catalog_free_candidate"]
                                              and r["freshness"] == "fresh"
                                              and r["price_freshness"] == "fresh"),
        "currently_usable_verified": 0,
    }
