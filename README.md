# Hermes Free Model Manager

**Unofficial Windows companion for [Hermes Agent](https://github.com/NousResearch/hermes-agent).** Discover candidate free-tier models, review tool-calling evidence, and prepare a user-controlled cross-provider fallback configuration. This project is not affiliated with NousResearch.

## Download (Windows)

Download the latest **Windows Portable ZIP** from [Releases](https://github.com/SmartyJohnway/hermes-free-model-manager/releases). Extract the complete archive to a writable folder and double-click **START-MANAGER.cmd**. No Python installation required. Windows SmartScreen may warn about an unsigned community application; review the source and checksums before running.

**Hermes Agent/Desktop is a separate prerequisite for reading or applying actual Hermes configuration.** Without Hermes, the Manager can launch with an empty catalog, but configuration actions are unavailable. This is a beta: provider free-tier eligibility and model capabilities vary by account and time; no free quota or Tool Calling capability is guaranteed.

## What it does

- Collects model candidates across providers, including candidates not yet configured in Hermes.
- Separates candidate discovery from account-specific eligibility and Tool Calling evidence.
- Keeps primary-model choice manual; fallback ordering can be prepared with ChatGPT.
- Offers preview, explicit confirmation, backups, and restore for supported Hermes configuration transactions.
- Avoids unattended paid inference; active compatibility probes require user action.

## Source development

Requires Windows and Python 3 with Tkinter. Install `requirements.txt` into a virtual environment and run `python -B src/manager_ui.py` from the repository root. CLI: `python -B scripts/manager_cli.py --help`. Tests: `python -B -m unittest discover -s tests -v`.

## Build a portable ZIP

Install `requirements-build.txt`, then run `powershell -NoProfile -ExecutionPolicy Bypass -File packaging/build-portable.ps1` on Windows. The script uses PyInstaller one-folder mode; review `THIRD_PARTY_NOTICES.md` and the bundled dependencies before redistributing.

## Privacy and limitations

The public repository excludes local `state/`, `reports/`, credentials, and personal account audits. Do not commit your Hermes config, tokens, provider login data or local reports. See [Security](SECURITY.md). This first beta has been smoke-tested in a clean Windows Sandbox without Python; that test does **not** establish compatibility with every Hermes release or provider account.

## License

MIT for this project's original source code; third-party dependencies retain their own licenses. See [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
