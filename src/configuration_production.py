"""M4 explicit-user-authorized one-shot Hermes Fallback update and recovery.

Uses Hermes's installed config writer; preserve original bytes in an ACL-restricted
local backup. This module is NOT called by Manager startup or discovery.
The caller pins the exact primary and Fallback list authorized by the user.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import secrets
import subprocess

import yaml

from manager_ai_exchange import read_catalog
from configuration_preview import preview_from_draft
from manager_ui_model import load_hermes_selection

EXPECTED_MAIN = ("kilo_custom", "kilo-auto/free")
EXPECTED_FALLBACK = [
    ("z-ai", "glm-4.7-flash"),
    ("openrouter", "cohere/north-mini-code:free"),
    ("nous", "stepfun/step-3.7-flash:free"),
    ("cloudflare", "@cf/zai-org/glm-4.7-flash"),
    ("openrouter", "nvidia/nemotron-3-ultra-550b-a55b:free"),
]
BACKUP_SCHEMA = "hfm-m4-production-backup.v1"


class M4ProductionError(RuntimeError):
    pass


def _hash(value: bytes) -> str:
    return sha256(value).hexdigest()


def official_home() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise M4ProductionError("Windows LOCALAPPDATA is missing")
    return (Path(local) / "hermes").resolve(strict=True)


def _target() -> Path:
    home = official_home()
    path = home / "config.yaml"
    if path.is_symlink() or not path.is_file():
        raise M4ProductionError("official config is missing or redirected")
    if path.resolve(strict=True) != home / "config.yaml":
        raise M4ProductionError("unexpected official config path")
    return path


def _backup_root() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "Hermes-Free-Model-Manager" / "M4_Backups"


def _current_sid() -> str:
    result = subprocess.run(["whoami.exe", "/user", "/fo", "csv", "/nh"],
                            capture_output=True, text=True, check=True)
    value = re.search(r"S-1-(?:\d+-)+\d+", result.stdout)
    if not value:
        raise M4ProductionError("cannot identify current Windows user SID")
    return value.group(0)


def _protect_directory(path: Path) -> None:
    """Windows explicit user + LocalSystem + Administrators access only."""
    result = subprocess.run([
        "icacls.exe", str(path), "/inheritance:r", "/grant:r",
        f"*{_current_sid()}:(OI)(CI)F",
        "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F",
    ], capture_output=True, text=True)
    if result.returncode != 0:
        raise M4ProductionError("private backup ACL setup failed")
    # A fresh directory should have no other explicit ACEs. Test read/write
    # inherited file ACL under this directory before copying credentials.
    probe = path / ".hfm-acl-probe"
    try:
        probe.write_text("test", encoding="utf-8")
        check = subprocess.run(["icacls.exe", str(probe)],
                               capture_output=True, text=True)
        if check.returncode != 0:
            raise M4ProductionError("cannot verify backup ACL")
    finally:
        probe.unlink(missing_ok=True)


def _prepare_backup() -> Path:
    root = _backup_root()
    parent = root.parent
    parent.mkdir(parents=True, exist_ok=True)
    if root.is_symlink() or parent.is_symlink():
        raise M4ProductionError("reparse backup path not allowed")
    root.mkdir(mode=0o700, exist_ok=True)
    _protect_directory(root)
    tid = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(6)
    folder = root / tid
    folder.mkdir(mode=0o700)
    _protect_directory(folder)
    return folder


@contextmanager
def _one_writer():
    """Exclusive lock for Manager-side production transactions."""
    lock = _backup_root().parent / ".hfm-m4-production.lock"
    fd = None
    owned = False
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        owned = True
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            fd = None
            f.write("HFM M4 production transaction\n")
        yield
    except FileExistsError as exc:
        raise M4ProductionError("another Manager production operation is running") from exc
    finally:
        if fd is not None:
            os.close(fd)
        if owned and lock.is_file():
            lock.unlink()


def _save_new(path: Path, content: bytes) -> None:
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def _atomic_replace(path: Path, content: bytes) -> None:
    tmp = path.with_name(".hfm-m4-" + secrets.token_hex(6) + ".tmp")
    try:
        _save_new(tmp, content)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _write_manifest(path: Path, payload: dict) -> None:
    value = (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    if path.exists():
        _atomic_replace(path, value)
    else:
        _save_new(path, value)


def _parse(content: bytes) -> dict:
    data = yaml.safe_load(content.decode("utf-8-sig")) or {}
    if not isinstance(data, dict):
        raise M4ProductionError("unexpected Hermes config structure")
    return data


def _other_fields(data: dict) -> dict:
    return {key: value for key, value in data.items() if key != "fallback_providers"}


def _restore_bytes(path: Path, content: bytes) -> None:
    # Windows replace may change the NTFS file ACL. The official file is under
    # the same user-controlled Hermes profile and must remain private.
    _atomic_replace(path, content)
    if _hash(path.read_bytes()) != _hash(content):
        raise M4ProductionError("original bytes restore verification failed")


def _official_writer(path: Path, selected: list[dict]) -> None:
    from hermes_cli.config_backend import write_config_keys
    write_config_keys(path, {"fallback_providers": selected})


def apply_once(draft_path: Path, catalog_path: Path) -> dict:
    target = _target()
    prior = target.read_bytes()
    baseline = load_hermes_selection(target.parent)
    catalog, _ = read_catalog(catalog_path)
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    plan = preview_from_draft(draft, baseline, catalog)
    if plan["primary_changed"] or tuple(plan["primary_after"]) != EXPECTED_MAIN:
        raise M4ProductionError("user authorized no primary model change")
    if plan["fallback_after"] != EXPECTED_FALLBACK:
        raise M4ProductionError("saved draft differs from user-authorized five models")
    if not plan["fallback_reordered"]:
        raise M4ProductionError("no new fallback changes needed")
    if _hash(prior) != plan["baseline_config_sha256"]:
        raise M4ProductionError("Hermes settings changed during preflight")

    selected = [{"provider": p, "model": m} for p, m in EXPECTED_FALLBACK]
    folder = None
    with _one_writer():
        # Recheck immediately before backing up, in case Hermes was edited.
        actual = target.read_bytes()
        if _hash(actual) != _hash(prior):
            raise M4ProductionError("Hermes config changed; refusing to overwrite")
        folder = _prepare_backup()
        original_file = folder / "config-original.yaml"
        manifest_file = folder / "manifest.json"
        _save_new(original_file, actual)
        if _hash(original_file.read_bytes()) != _hash(actual):
            raise M4ProductionError("backup data mismatch")
        transaction_id = folder.name
        manifest = {
            "schema": BACKUP_SCHEMA,
            "state": "BACKUP_VERIFIED",
            "transaction_id": transaction_id,
            "when_utc": datetime.now(timezone.utc).isoformat(),
            "before_sha256": _hash(actual),
            "after_sha256": None,
            "primary": list(EXPECTED_MAIN),
            "fallback_before": [list(v) for v in baseline["fallback"]],
            "fallback_after": [list(v) for v in EXPECTED_FALLBACK],
            "original_file": "config-original.yaml",
        }
        _write_manifest(manifest_file, manifest)
        if _hash(target.read_bytes()) != _hash(actual):
            raise M4ProductionError("config changed after backup, no write performed")
        manifest["state"] = "WRITE_STARTED"
        _write_manifest(manifest_file, manifest)
        try:
            _official_writer(target, selected)
            updated = target.read_bytes()
            parsed = _parse(updated)
            native = load_hermes_selection(target.parent)
            if native["primary"] != EXPECTED_MAIN:
                raise M4ProductionError("primary was modified unexpectedly")
            if native["fallback"] != EXPECTED_FALLBACK:
                raise M4ProductionError("fallback readback does not match authorized order")
            if _other_fields(parsed) != _other_fields(_parse(actual)):
                raise M4ProductionError("unexpected unrelated config changes")
            # Verify the exact official fallback interpretation, not just YAML.
            from hermes_cli.fallback_config import get_fallback_chain
            effective = get_fallback_chain(parsed)
            pairs = [(p["provider"], p["model"]) for p in effective]
            if pairs != EXPECTED_FALLBACK:
                raise M4ProductionError("native Hermes fallback interpretation mismatch")
            manifest["after_sha256"] = _hash(updated)
            manifest["state"] = "APPLIED_VERIFIED"
            _write_manifest(manifest_file, manifest)
        except Exception as exc:
            current = target.read_bytes()
            if _hash(current) != _hash(actual):
                # Only restore automatically when non-fallback fields still match.
                # Otherwise preserve the backup, as another writer may have acted.
                try:
                    unrelated_safe = _other_fields(_parse(current)) == _other_fields(_parse(actual))
                except Exception:
                    unrelated_safe = False
                if unrelated_safe:
                    _restore_bytes(target, actual)
                    manifest["state"] = "FAILED_AUTO_RESTORED"
                else:
                    manifest["state"] = "FAILED_MANUAL_RECOVERY_REQUIRED"
            else:
                manifest["state"] = "FAILED_NO_CHANGE"
            _write_manifest(manifest_file, manifest)
            raise M4ProductionError(
                "native apply/readback failed; backup retained: " + manifest["state"]
            ) from exc

    return {
        "status": "PRODUCTION_APPLY_VERIFIED",
        "transaction_id": transaction_id,
        "before_sha256": manifest["before_sha256"],
        "after_sha256": manifest["after_sha256"],
        "backup_path": str(folder),
        "primary": list(EXPECTED_MAIN),
        "fallback": [list(v) for v in EXPECTED_FALLBACK],
        "gateway_reloaded": False,
    }


def rollback(transaction_id: str) -> dict:
    if not re.fullmatch(r"\d{8}T\d{6}Z-[0-9a-f]{12}", transaction_id):
        raise M4ProductionError("invalid transaction ID")
    target = _target()
    folder = _backup_root() / transaction_id
    if folder.is_symlink() or not folder.is_dir():
        raise M4ProductionError("backup transaction not found")
    with _one_writer():
        manifest_path = folder / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (manifest.get("schema") != BACKUP_SCHEMA
                or manifest.get("transaction_id") != transaction_id
                or manifest.get("state") != "APPLIED_VERIFIED"):
            raise M4ProductionError("transaction is not currently reversible")
        original = (folder / "config-original.yaml").read_bytes()
        if _hash(original) != manifest.get("before_sha256"):
            raise M4ProductionError("backup SHA verification failed")
        current = target.read_bytes()
        if _hash(current) != manifest.get("after_sha256"):
            raise M4ProductionError("current Hermes config has changed; do not overwrite")
        _restore_bytes(target, original)
        manifest["state"] = "ROLLED_BACK_VERIFIED"
        manifest["rolled_back_at_utc"] = datetime.now(timezone.utc).isoformat()
        _write_manifest(manifest_path, manifest)
        return {"status": "PRODUCTION_ROLLBACK_VERIFIED",
                "transaction_id": transaction_id,
                "restored_sha256": _hash(original)}
