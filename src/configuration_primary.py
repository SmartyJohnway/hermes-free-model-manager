"""One-time owner-authorized Hermes primary+18 fallback production promotion.

No model inference, quota probes, or Desktop changes. Uses official installed
Hermes model selection / Config backend with private recovery backup.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.parse import urlsplit

from manager_ai_exchange import read_catalog
from configuration_preview import preview_from_draft
from manager_ui_model import load_hermes_selection
from configuration_production import (
    BACKUP_SCHEMA, M4ProductionError, _target, _backup_root, _prepare_backup,
    _one_writer, _hash, _parse, _save_new, _write_manifest, _restore_bytes,
)

AUTH_PRIMARY = ("z-ai", "glm-4.7-flash")
AUTH_FALLBACK = [
    ("kilo_custom", "kilo-auto/free"),
    ("openrouter", "cohere/north-mini-code:free"),
    ("cloudflare", "@cf/zai-org/glm-4.7-flash"),
    ("nous", "stepfun/step-3.7-flash:free"),
    ("openrouter", "google/gemma-4-31b-it:free"),
    ("kilo_custom", "dots-studio/dots-3-note-preview:free"),
    ("nvidia", "nvidia/nemotron-3-ultra-550b-a55b"),
    ("openrouter", "nvidia/nemotron-3.5-lightning:free"),
    ("cloudflare", "@cf/openai/gpt-oss-120b"),
    ("z-ai", "glm-4.5-flash"),
    ("kilo_custom", "nvidia/nemotron-3.5-super-vl:free"),
    ("nous", "meituan/longcat-2.5:free"),
    ("openrouter", "nvidia/nemotron-3-ultra-550b-a55b:free"),
    ("cloudflare", "@cf/qwen/qwen3-30b-a3b-fp8"),
    ("nvidia", "nvidia/nemotron-3.5-super-vl-120b-a12b"),
    ("openrouter", "google/gemma-4-26b-a4b-it:free"),
    ("groq", "openai/gpt-oss-20b"),
    ("agnesai", "agnes-2.5-flash"),
]
AUTH_PREVIOUS_MAIN = ("kilo_custom", "kilo-auto/free")
AUTH_PREVIOUS_SHA = "6a347ed0b76b1ef1002f34f21a56faa092b6c13a15bdef86395e4f07b57bf388"


def resolved_model_from_official(config: dict) -> dict:
    """Offline official route shaping; never requests a model or token."""
    providers = config.get("providers", {})
    if not isinstance(providers, dict) or not isinstance(providers.get("z-ai"), dict):
        raise M4ProductionError("Z.ai provider not configured")
    settings = providers["z-ai"]
    url = settings.get("base_url", "")
    parsed = urlsplit(url) if isinstance(url, str) else None
    if (not parsed or parsed.scheme != "https" or parsed.hostname != "api.z.ai"
            or parsed.path.rstrip("/") != "/api/paas/v4"
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise M4ProductionError("unexpected Z.ai route; refuse to switch")
    if not isinstance(settings.get("key_env"), str) or not settings["key_env"]:
        raise M4ProductionError("Z.ai provider has no credential reference")
    configured_models = settings.get("models")
    if (isinstance(configured_models, list) and configured_models
            and not any(
                (x == AUTH_PRIMARY[1]) if isinstance(x, str)
                else (x.get("id") == AUTH_PRIMARY[1] or x.get("model") == AUTH_PRIMARY[1])
                if isinstance(x, dict) else False for x in configured_models
            ) and settings.get("model") != AUTH_PRIMARY[1]):
        raise M4ProductionError("requested Z.ai primary not in configured models")
    from hermes_cli.model_switch import ModelSwitchResult, apply_model_selection
    from hermes_cli.providers import determine_api_mode
    mode = determine_api_mode(AUTH_PRIMARY[0], url, model=AUTH_PRIMARY[1])
    if mode != "chat_completions":
        raise M4ProductionError("unexpected Z.ai API wire mode")
    current = config.get("model")
    if not isinstance(current, dict):
        raise M4ProductionError("Hermes model section malformed")
    selected = ModelSwitchResult(
        success=True, new_model=AUTH_PRIMARY[1], target_provider=AUTH_PRIMARY[0],
        provider_changed=True, base_url=url, api_mode=mode,
    )
    merged = apply_model_selection(current, selected)
    if (merged.get("provider"), merged.get("default")) != AUTH_PRIMARY:
        raise M4ProductionError("native route resolver returned unexpected model")
    if merged.get("base_url") != url or merged.get("api_mode") != mode:
        raise M4ProductionError("native route resolver returned unexpected endpoint")
    for stale in ("api_key", "key_env", "api_key_env"):
        if stale in merged and stale not in current:
            raise M4ProductionError("native route introduced an unexpected credential")
    return merged


def _writer(path: Path, updates: dict) -> None:
    from hermes_cli.config_backend import write_config_keys
    write_config_keys(path, updates)


def apply_authorized(draft_path: Path, catalog_path: Path) -> dict:
    """Apply the exact user-approved main+18 fallback once, then verify+backup."""
    target = _target()
    baseline = load_hermes_selection(target.parent)
    current_bytes = target.read_bytes()
    if _hash(current_bytes) != AUTH_PREVIOUS_SHA:
        raise M4ProductionError("production version no longer matches approved baseline")
    if baseline["config_sha256"] != AUTH_PREVIOUS_SHA:
        raise M4ProductionError("config changed during baseline read")
    if baseline["primary"] != AUTH_PREVIOUS_MAIN:
        raise M4ProductionError("current primary differs from authorized previous model")
    catalog, _ = read_catalog(catalog_path)
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    plan = preview_from_draft(draft, baseline, catalog)
    if plan["primary_after"] != AUTH_PRIMARY or plan["fallback_after"] != AUTH_FALLBACK:
        raise M4ProductionError("saved Manager draft does not match exact approved 18-model plan")
    if len({p for p, _ in AUTH_FALLBACK}) != 8:
        raise M4ProductionError("expected eight providers missing")
    prior_doc = _parse(current_bytes)
    new_model = resolved_model_from_official(prior_doc)
    selected = [{"provider": p, "model": m} for p, m in AUTH_FALLBACK]
    updates = {"model": new_model, "fallback_providers": selected}

    with _one_writer():
        if _hash(target.read_bytes()) != AUTH_PREVIOUS_SHA:
            raise M4ProductionError("Hermes was edited after preflight; not writing")
        folder = _prepare_backup()
        saved = folder / "config-original.yaml"
        manifest_file = folder / "manifest.json"
        _save_new(saved, current_bytes)
        if _hash(saved.read_bytes()) != AUTH_PREVIOUS_SHA:
            raise M4ProductionError("backup mismatch; aborting")
        transaction_id = folder.name
        manifest = {
            "schema": BACKUP_SCHEMA, "state": "BACKUP_VERIFIED",
            "transaction_id": transaction_id,
            "when_utc": datetime.now(timezone.utc).isoformat(),
            "before_sha256": AUTH_PREVIOUS_SHA, "after_sha256": None,
            "primary_before": list(AUTH_PREVIOUS_MAIN),
            "primary": list(AUTH_PRIMARY),
            "fallback_before": [list(x) for x in baseline["fallback"]],
            "fallback_after": [list(x) for x in AUTH_FALLBACK],
            "original_file": "config-original.yaml",
            "changed_keys": ["model", "fallback_providers"],
        }
        _write_manifest(manifest_file, manifest)
        if _hash(target.read_bytes()) != AUTH_PREVIOUS_SHA:
            raise M4ProductionError("Config changed after backup, no write")
        manifest["state"] = "WRITE_STARTED"
        _write_manifest(manifest_file, manifest)
        try:
            _writer(target, updates)
            written = target.read_bytes()
            after = _parse(written)
            live = load_hermes_selection(target.parent)
            if live["primary"] != AUTH_PRIMARY or live["fallback"] != AUTH_FALLBACK:
                raise M4ProductionError("native config readback does not match authorized order")
            if after.get("model") != new_model:
                raise M4ProductionError("primary route endpoint/mode readback mismatch")
            before_untouched = {k: v for k, v in prior_doc.items()
                                if k not in ("model", "fallback_providers")}
            after_untouched = {k: v for k, v in after.items()
                               if k not in ("model", "fallback_providers")}
            if before_untouched != after_untouched:
                raise M4ProductionError("unrelated Hermes configuration modified")
            from hermes_cli.fallback_config import get_fallback_chain
            native = get_fallback_chain(after)
            pairs = [(x["provider"], x["model"]) for x in native]
            if pairs != AUTH_FALLBACK:
                raise M4ProductionError("Hermes native fallback parser mismatch")
            manifest["after_sha256"] = _hash(written)
            manifest["state"] = "APPLIED_VERIFIED"
            _write_manifest(manifest_file, manifest)
        except Exception as exc:
            present = target.read_bytes()
            if _hash(present) == AUTH_PREVIOUS_SHA:
                manifest["state"] = "FAILED_NO_CHANGE"
            else:
                try:
                    changed = _parse(present)
                    unaffected = {k: v for k, v in changed.items()
                                  if k not in ("model", "fallback_providers")}
                    before_untouched = {k: v for k, v in prior_doc.items()
                                        if k not in ("model", "fallback_providers")}
                    if unaffected == before_untouched:
                        _restore_bytes(target, current_bytes)
                        manifest["state"] = "FAILED_AUTO_RESTORED"
                    else:
                        manifest["state"] = "FAILED_MANUAL_RECOVERY_REQUIRED"
                except Exception:
                    manifest["state"] = "FAILED_MANUAL_RECOVERY_REQUIRED"
            _write_manifest(manifest_file, manifest)
            raise M4ProductionError(
                "Primary+Fallback apply failed: " + manifest["state"]
            ) from exc
    return {
        "status": "PRODUCTION_PRIMARY_AND_18_FALLBACK_APPLY_VERIFIED",
        "transaction_id": transaction_id,
        "before_sha256": AUTH_PREVIOUS_SHA,
        "after_sha256": manifest["after_sha256"],
        "backup_path": str(folder),
        "primary": list(AUTH_PRIMARY),
        "fallback": [list(v) for v in AUTH_FALLBACK],
        "provider_count": 8, "gateway_reloaded": False,
        "free_entitlements_verified_by_inference": False,
    }
