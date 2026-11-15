"""Reusable M4 Manager transaction over installed official Hermes.

No automatic calls: requires a saved draft and an explicit UI/CLI operation.
Reuse proven M4 backup, lock, SHA, rollback and official config-writer helpers.
No model inference or entitlement billing probes.
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
    rollback as native_rollback
)


def _native_model_config(before: dict, desired: tuple[str, str]) -> dict:
    """Resolve only a configured Provider through installed official Hermes helpers."""
    existing = before.get("model")
    if not isinstance(existing, dict):
        raise M4ProductionError("Hermes model config is malformed")
    if (existing.get("provider"), existing.get("default")) == desired:
        return existing.copy()
    providers = before.get("providers")
    if not isinstance(providers, dict):
        raise M4ProductionError("Hermes configured Provider definitions are missing")
    candidate = providers.get(desired[0])
    if not isinstance(candidate, dict):
        raise M4ProductionError(
            f"Provider {desired[0]!r} has no Hermes configuration; cannot switch main")
    url = candidate.get("base_url")
    if not isinstance(url, str) or not url.strip():
        raise M4ProductionError("selected Provider lacks a configured API endpoint")
    parsed = urlsplit(url.strip())
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise M4ProductionError("unexpected credentials/query in Provider endpoint")
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1")
    ):
        raise M4ProductionError("configured Provider endpoint must use HTTPS or localhost")
    if not isinstance(candidate.get("key_env"), str) or not candidate["key_env"].strip():
        if parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise M4ProductionError("selected Provider has no configured key_env reference")

    from hermes_cli.model_switch import ModelSwitchResult, apply_model_selection
    from hermes_cli.providers import determine_api_mode
    mode = determine_api_mode(desired[0], url, model=desired[1])
    if not isinstance(mode, str) or not mode:
        raise M4ProductionError("official Hermes did not resolve provider API mode")
    switched = ModelSwitchResult(
        success=True, new_model=desired[1], target_provider=desired[0],
        provider_changed=(existing.get("provider") != desired[0]),
        base_url=url, api_mode=mode,
    )
    projected = apply_model_selection(existing, switched)
    if (projected.get("provider"), projected.get("default")) != desired:
        raise M4ProductionError("official model selection projection mismatch")
    if projected.get("base_url") != url:
        raise M4ProductionError("official model selection endpoint mismatch")
    if projected.get("api_mode") != mode:
        raise M4ProductionError("official model selection API mode mismatch")
    if projected.get("provider") != existing.get("provider"):
        for name in ("api_key", "api_key_env", "key_env", "context_length"):
            if name in projected and projected[name] == existing.get(name):
                raise M4ProductionError("old provider credential/context pointer not cleared")
    return projected


def _unrelated(cfg: dict) -> dict:
    return {k: v for k, v in cfg.items() if k not in ("model", "fallback_providers")}


def _official_write(path: Path, updates: dict) -> None:
    from hermes_cli.config_backend import write_config_keys
    write_config_keys(path, updates)


def apply_saved_draft(draft_path: Path, catalog_path: Path) -> dict:
    """Exactly the on-disk draft; if stale or already applied refuse without writing."""
    target = _target()
    initial = target.read_bytes()
    current = load_hermes_selection(target.parent)
    catalog, _ = read_catalog(catalog_path)
    if draft_path.stat().st_size > 500_000:
        raise M4ProductionError("Manager draft exceeds size limit")
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    plan = preview_from_draft(draft, current, catalog)
    if not plan["primary_changed"] and not plan["fallback_reordered"]:
        return {"status": "ALREADY_MATCHES_HERMES", "transaction_id": None,
                "primary": list(current["primary"]), "fallback_count": len(current["fallback"])}
    if _hash(initial) != current["config_sha256"]:
        raise M4ProductionError("Hermes config changed during initial read")

    before_document = _parse(initial)
    desired_main = plan["primary_after"]
    projected_main = (_native_model_config(before_document, desired_main)
                      if plan["primary_changed"] else before_document["model"])
    desired_fallback = [{"provider": p, "model": m} for p, m in plan["fallback_after"]]
    updates = {"model": projected_main, "fallback_providers": desired_fallback}
    # If the main does not change, do not touch model at all.
    if not plan["primary_changed"]:
        updates.pop("model")

    with _one_writer():
        if _hash(target.read_bytes()) != _hash(initial):
            raise M4ProductionError("Hermes config changed since preview; no changes made")
        folder = _prepare_backup()
        backup = folder / "config-original.yaml"
        manifest_path = folder / "manifest.json"
        _save_new(backup, initial)
        if _hash(backup.read_bytes()) != _hash(initial):
            raise M4ProductionError("original Hermes backup SHA verification failed")
        transaction_id = folder.name
        manifest = {
            "schema": BACKUP_SCHEMA, "transaction_id": transaction_id,
            "when_utc": datetime.now(timezone.utc).isoformat(),
            "state": "BACKUP_VERIFIED",
            "before_sha256": _hash(initial),
            "after_sha256": None,
            "primary_before": list(current["primary"]),
            "primary": list(desired_main),
            "fallback_before": [list(x) for x in current["fallback"]],
            "fallback_after": [list(x) for x in plan["fallback_after"]],
            "changed_keys": list(updates),
            "original_file": "config-original.yaml",
        }
        _write_manifest(manifest_path, manifest)
        if _hash(target.read_bytes()) != _hash(initial):
            raise M4ProductionError("Hermes changed during backup; refusing write")
        manifest["state"] = "WRITE_STARTED"
        _write_manifest(manifest_path, manifest)
        try:
            _official_write(target, updates)
            after_bytes = target.read_bytes()
            after_doc = _parse(after_bytes)
            loaded = load_hermes_selection(target.parent)
            if loaded["primary"] != desired_main:
                raise M4ProductionError("post-apply primary does not match draft")
            if loaded["fallback"] != plan["fallback_after"]:
                raise M4ProductionError("post-apply fallback order does not match draft")
            if _unrelated(after_doc) != _unrelated(before_document):
                raise M4ProductionError("unrelated Hermes config fields changed")
            if after_doc.get("model") != projected_main:
                raise M4ProductionError("post-apply primary endpoint/mode mismatch")
            from hermes_cli.fallback_config import get_fallback_chain
            resolved = [(item["provider"], item["model"])
                        for item in get_fallback_chain(after_doc)]
            if resolved != plan["fallback_after"]:
                raise M4ProductionError("official Hermes fallback parser did not match")
            manifest["after_sha256"] = _hash(after_bytes)
            manifest["state"] = "APPLIED_VERIFIED"
            _write_manifest(manifest_path, manifest)
        except Exception as cause:
            try:
                now = target.read_bytes()
                if _hash(now) == _hash(initial):
                    manifest["state"] = "FAILED_NO_CHANGE"
                elif _unrelated(_parse(now)) == _unrelated(before_document):
                    _restore_bytes(target, initial)
                    manifest["state"] = "FAILED_AUTO_RESTORED"
                else:
                    manifest["state"] = "FAILED_MANUAL_RECOVERY_REQUIRED"
            except Exception:
                manifest["state"] = "FAILED_MANUAL_RECOVERY_REQUIRED"
            _write_manifest(manifest_path, manifest)
            raise M4ProductionError(
                "official Hermes apply/readback failed: " + manifest["state"]
            ) from cause

    return {
        "status": "PRODUCTION_MANAGER_DRAFT_APPLY_VERIFIED",
        "transaction_id": transaction_id,
        "before_sha256": manifest["before_sha256"],
        "after_sha256": manifest["after_sha256"],
        "backup_path": str(folder),
        "primary": list(desired_main),
        "fallback_count": len(plan["fallback_after"]),
        "gateway_reloaded": False,
        "runtime_inference_tested": False,
    }


def latest_reversible() -> dict | None:
    """Return only the newest verified transaction if exact current SHA matches.

    Never silently look further back if a newer APPLY exists but the config was
    subsequently changed. The user must review such a mismatch.
    """
    root = _backup_root()
    if not root.is_dir():
        return None
    entries = sorted(
        (d for d in root.iterdir()
         if d.is_dir() and not d.is_symlink() and (d / "manifest.json").is_file()),
        key=lambda x: x.name, reverse=True,
    )
    for folder in entries:
        try:
            data = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("schema") != BACKUP_SCHEMA or data.get("state") != "APPLIED_VERIFIED":
            continue
        if data.get("transaction_id") != folder.name:
            continue
        current_hash = _hash(_target().read_bytes())
        return {
            "transaction_id": folder.name,
            "current_matches": current_hash == data.get("after_sha256"),
            "primary_before": data.get("primary_before"),
            "primary_after": data.get("primary"),
            "fallback_before": data.get("fallback_before", []),
            "fallback_after": data.get("fallback_after", []),
            "before_sha256": data.get("before_sha256"),
            "after_sha256": data.get("after_sha256"),
        }
    return None


def undo_latest() -> dict:
    candidate = latest_reversible()
    if not candidate:
        raise M4ProductionError("no verified M4 backup is eligible for restoration")
    if not candidate["current_matches"]:
        raise M4ProductionError(
            "latest M4 backup differs from current Hermes; refusal protects later changes")
    return native_rollback(candidate["transaction_id"])
