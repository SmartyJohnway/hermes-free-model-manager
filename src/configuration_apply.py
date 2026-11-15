"""M4 Hermes native configuration transaction — ISOLATED PROFILE ONLY.

The production profile is deliberately hard-blocked. No inference or networking.
After independent testing and explicit user authorization, production integration
can be addressed separately. No Hermes Desktop or system config is mutated here.
"""
from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import secrets
import tempfile
from typing import Callable

import yaml

from inventory import default_hermes_home
from configuration_preview import preview_from_draft
from manager_ai_exchange import read_catalog
from manager_ui_model import load_hermes_selection


class M4ApplyError(ValueError):
    pass


def _sha(data: bytes) -> str:
    return sha256(data).hexdigest()


def _restrict_to_isolated(home: Path) -> Path:
    home = home.resolve(strict=True)
    # HERMES_HOME can point at our temporary test profile; production
    # identity must instead be based on the real Windows APPDATA location.
    local = os.environ.get("LOCALAPPDATA")
    official = ((Path(local) / "hermes").resolve() if local
                else default_hermes_home().resolve())
    # Reject the real profile and all of its ancestors/descendants.
    if home == official or official in home.parents or home in official.parents:
        raise M4ApplyError("production Hermes profile is HARD BLOCKED in this build")
    if not (home / "config.yaml").is_file():
        raise M4ApplyError("isolated config.yaml missing")
    return home


@contextmanager
def _exclusive_lock(home: Path):
    lock = home / ".hfm-m4.lock"
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise M4ApplyError("another M4 transaction is active") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write("ISOLATED_HERMES_M4_TRANSACTION\n")
        yield
    finally:
        lock.unlink(missing_ok=True)


def _write_private_file(path: Path, value: bytes) -> None:
    """No world-readable temp files. Keep backup inside private profile."""
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())


def _atomic_restore(path: Path, saved: bytes) -> None:
    fd, name = tempfile.mkstemp(prefix=".hfm-restore-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(saved)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        if _sha(path.read_bytes()) != _sha(saved):
            raise M4ApplyError("restored config did not match original bytes")
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _parse(data: bytes) -> dict:
    parsed = yaml.safe_load(data.decode("utf-8-sig")) or {}
    if not isinstance(parsed, dict):
        raise M4ApplyError("unexpected Hermes config structure")
    return parsed


def _without_target_keys(cfg: dict) -> dict:
    return {key: value for key, value in cfg.items()
            if key not in ("fallback_providers", "model")}


def _native_writer(config_path: Path, updates: dict) -> None:
    from hermes_cli.config_backend import write_config_keys
    write_config_keys(config_path, updates)


def apply_isolated(
    home: Path, draft_path: Path, catalog_path: Path, *,
    writer: Callable[[Path, dict], None] | None = None,
) -> dict:
    """Apply exactly one saved draft to a synthetic isolated Hermes profile.

    This intentionally supports fallback-only. Changing primary requires the
    native route resolver/credential cleanup integration in a separate phase.
    """
    home = _restrict_to_isolated(home)
    config = home / "config.yaml"
    if not draft_path.is_file():
        raise M4ApplyError("saved Manager draft required")
    if draft_path.stat().st_size > 500_000:
        raise M4ApplyError("draft file too large")
    catalog, _ = read_catalog(catalog_path)
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    with _exclusive_lock(home):
        before_bytes = config.read_bytes()
        baseline = load_hermes_selection(home)
        if baseline["config_sha256"] != _sha(before_bytes):
            raise M4ApplyError("config changed during preflight")
        plan = preview_from_draft(draft, baseline, catalog)
        if plan["primary_changed"]:
            raise M4ApplyError("primary changes need official route resolution; blocked")
        if not plan["fallback_reordered"]:
            raise M4ApplyError("no fallback change to apply")

        original_document = _parse(before_bytes)
        backup_root = home / ".hfm-m4-backups"
        backup_root.mkdir(mode=0o700, exist_ok=True)
        transaction_id = secrets.token_hex(12)
        backup = backup_root / transaction_id
        backup.mkdir(mode=0o700)
        saved_path = backup / "config-original.yaml"
        manifest_path = backup / "manifest.json"
        _write_private_file(saved_path, before_bytes)
        manifest = {
            "schema": "hfm-m4-isolated-backup.v1",
            "mode": "ISOLATED_ONLY",
            "transaction_id": transaction_id,
            "before_sha256": _sha(before_bytes),
            "after_sha256": None,
            "original_model": list(plan["primary_before"]),
            "fallback_count_before": len(plan["fallback_before"]),
            "fallback_count_after": len(plan["fallback_after"]),
            "state": "BACKUP_CREATED",
        }
        _write_private_file(
            manifest_path, (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
        )
        if _sha(config.read_bytes()) != manifest["before_sha256"]:
            raise M4ApplyError("config modified externally after backup")

        update = {
            "fallback_providers": [
                {"provider": provider, "model": model}
                for provider, model in plan["fallback_after"]
            ]
        }
        try:
            (writer or _native_writer)(config, update)
            after_bytes = config.read_bytes()
            after_document = _parse(after_bytes)
            after = load_hermes_selection(home)
            if after["primary"] != baseline["primary"]:
                raise M4ApplyError("native writer unexpectedly changed primary")
            if after["fallback"] != plan["fallback_after"]:
                raise M4ApplyError("native writer fallback readback mismatch")
            if _without_target_keys(after_document) != _without_target_keys(original_document):
                raise M4ApplyError("native writer changed unrelated config fields")
            manifest["after_sha256"] = _sha(after_bytes)
            manifest["state"] = "APPLIED"
            # Atomic manifest rewrite only after all validations.
            tmp = backup / ".manifest-next.json"
            _write_private_file(
                tmp, (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
            )
            os.replace(tmp, manifest_path)
        except Exception:
            # An isolated profile has no live users. Restore only when no
            # competing writer is apparent; leave backup for manual recovery.
            if _sha(config.read_bytes()) != manifest["before_sha256"]:
                _atomic_restore(config, before_bytes)
            raise

        return {
            "status": "ISOLATED_APPLY_READBACK_PASS",
            "transaction_id": transaction_id,
            "primary": after["primary"],
            "fallback": after["fallback"],
            "before_sha256": manifest["before_sha256"],
            "after_sha256": manifest["after_sha256"],
            "backup_dir": str(backup),
        }


def rollback_isolated(home: Path, transaction_id: str) -> dict:
    home = _restrict_to_isolated(home)
    if len(transaction_id) != 24 or any(c not in "0123456789abcdef" for c in transaction_id):
        raise M4ApplyError("invalid transaction ID")
    config = home / "config.yaml"
    backup = home / ".hfm-m4-backups" / transaction_id
    manifest_path = backup / "manifest.json"
    original_path = backup / "config-original.yaml"
    with _exclusive_lock(home):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (manifest.get("schema") != "hfm-m4-isolated-backup.v1"
                or manifest.get("mode") != "ISOLATED_ONLY"
                or manifest.get("transaction_id") != transaction_id
                or manifest.get("state") != "APPLIED"):
            raise M4ApplyError("no completed transaction to restore")
        original = original_path.read_bytes()
        if _sha(original) != manifest.get("before_sha256"):
            raise M4ApplyError("backup hash verification failed")
        if _sha(config.read_bytes()) != manifest.get("after_sha256"):
            raise M4ApplyError("config changed since apply; refusing to overwrite")
        _atomic_restore(config, original)
        manifest["state"] = "ROLLED_BACK"
        next_manifest = backup / ".manifest-rollback.json"
        _write_private_file(
            next_manifest, (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
        )
        os.replace(next_manifest, manifest_path)
        return {
            "status": "ISOLATED_ROLLBACK_PASS",
            "transaction_id": transaction_id,
            "restored_sha256": manifest["before_sha256"],
        }
