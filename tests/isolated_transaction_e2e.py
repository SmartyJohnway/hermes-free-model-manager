"""Official Hermes API + HFM M4 transaction integration — synthetic profile only."""
from __future__ import annotations
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

import yaml

RND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RND / "src"))
official = Path(os.environ["LOCALAPPDATA"]) / "hermes" / "hermes-agent"
sys.path.insert(0, str(official))
from configuration_apply import apply_isolated, rollback_isolated
from manager_ui_model import load_hermes_selection, save_manager_draft


def run() -> int:
    real_config = Path(os.environ["LOCALAPPDATA"]) / "hermes" / "config.yaml"
    real_hash = sha256(real_config.read_bytes()).hexdigest()
    with TemporaryDirectory(prefix="hfm-M4-official-integration-") as folder:
        home = Path(folder) / "isolated-hermes"
        home.mkdir()
        config = home / "config.yaml"
        original = {
            "model": {
                "provider": "kilo_custom", "default": "kilo-auto/free",
                "api_mode": "chat_completions", "key_env": "SYNTHETIC",
            },
            "fallback_providers": [
                {"provider": "groq", "model": "original-synthetic"},
            ],
            "unrelated": {"sentinel": "must-remain"},
            "providers": {"synthetic": {"api_key": "DO_NOT_EXPORT"}},
        }
        before = yaml.safe_dump(original, sort_keys=False).encode("utf-8")
        config.write_bytes(before)
        candidate = [
            {"provider": "kilo_custom", "model": "kilo-auto/free",
             "eligibility": "fresh_zero_price_catalog"},
            {"provider": "z-ai", "model": "glm-4.7-flash",
             "eligibility": "official_zero_price"},
            {"provider": "openrouter", "model": "cohere/north-mini-code:free",
             "eligibility": "fresh_zero_price_catalog"},
            {"provider": "nous", "model": "stepfun/step-3.7-flash:free",
             "eligibility": "official_public_free_list"},
            {"provider": "cloudflare", "model": "@cf/zai-org/glm-4.7-flash",
             "eligibility": "free_daily_neuron_allowance_candidate"},
            {"provider": "openrouter",
             "model": "nvidia/nemotron-3-ultra-550b-a55b:free",
             "eligibility": "fresh_zero_price_catalog"},
        ]
        source = home / "m2-synthetic.json"
        source.write_text(json.dumps({
            "generated_at_utc": "2026-11-14T08:00:00+00:00",
            "candidates": candidate
        }), encoding="utf-8")
        draft = home / "manager-draft.json"
        fallback = [(r["provider"], r["model"]) for r in candidate[1:]]
        baseline = load_hermes_selection(home)
        save_manager_draft(
            draft, primary=("kilo_custom", "kilo-auto/free"),
            fallback=fallback, source_utc="2026-11-14T08:00:00+00:00",
            config_sha256=baseline["config_sha256"])
        os.environ["HERMES_HOME"] = str(home)
        os.environ["HERMES_CONFIG_BACKEND"] = "file"
        os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
        from hermes_cli.config_backend import write_config_keys
        print("OFFICIAL_WRITER_IMPORT_PASS", flush=True)
        result = apply_isolated(
            home, draft, source,
            writer=lambda path, changes: write_config_keys(path, changes),
        )
        assert result["fallback"] == fallback
        after = yaml.safe_load(config.read_text(encoding="utf-8"))
        assert after["model"] == original["model"]
        assert after["unrelated"] == original["unrelated"]
        assert after["providers"] == original["providers"]
        assert sha256(real_config.read_bytes()).hexdigest() == real_hash
        print("NATIVE_APPLY_READBACK_PASS", len(result["fallback"]), flush=True)
        revert = rollback_isolated(home, result["transaction_id"])
        assert revert["status"] == "ISOLATED_ROLLBACK_PASS"
        assert config.read_bytes() == before
        assert sha256(real_config.read_bytes()).hexdigest() == real_hash
        print("NATIVE_ORIGINAL_BYTES_RESTORED_PASS", flush=True)
        print("OFFICIAL_PRODUCTION_HASH_UNCHANGED", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
