# Hermes Free Model Manager

[English](README.md) | [繁體中文](README.zh-TW.md)

[![Release](https://img.shields.io/github/v/release/SmartyJohnway/hermes-free-model-manager?include_prereleases&label=Release)](https://github.com/SmartyJohnway/hermes-free-model-manager/releases/tag/v0.1.0-beta)
[![Windows](https://img.shields.io/badge/Windows-Portable-0078D4?logo=windows)](https://github.com/SmartyJohnway/hermes-free-model-manager/releases/tag/v0.1.0-beta)
[![Python Included](https://img.shields.io/badge/Python-Included-3776AB?logo=python&logoColor=white)](https://github.com/SmartyJohnway/hermes-free-model-manager/releases/tag/v0.1.0-beta)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Status: Beta](https://img.shields.io/badge/Status-Public_Beta-orange)](https://github.com/SmartyJohnway/hermes-free-model-manager/releases)

**Discover free-tier AI models across providers. Manage Hermes Agent fallback models from a standalone Windows desktop GUI.**

A community-built, local-first companion for [Hermes Agent](https://github.com/NousResearch/hermes-agent). Find model candidates, inspect free-tier eligibility evidence, plan cross-provider fallbacks, and review configuration changes before applying them. **Your primary model remains your choice.**

> [!IMPORTANT]
> **Unofficial project, not affiliated with NousResearch.** Free-tier eligibility depends on the provider, account, region, and time. Model availability, pricing, tool calling, and Hermes version compatibility are not guaranteed.

## Download & quick start

**[Download v0.1.0-beta — Windows Portable ZIP](https://github.com/SmartyJohnway/hermes-free-model-manager/releases/tag/v0.1.0-beta)**

1. Download the Windows Portable ZIP from **Assets** on the release page.
2. Extract the **entire ZIP** into a writable folder.
3. Double-click `START-MANAGER.cmd` to open the GUI.

**No Python installation or installer required.** Hermes Agent must be installed separately for reading or applying a real Hermes configuration. Without Hermes, the Manager can start with an empty model catalog. The app is currently unsigned and may trigger a Windows SmartScreen warning.

## Features

| Feature | What it does |
| --- | --- |
| **Free-model discovery** | Explore candidates from multiple providers, including models not already configured in Hermes |
| **Evidence-aware catalog** | Distinguish public model metadata from account-specific free eligibility and tested tool-calling support |
| **Manual primary model** | Keep primary-model selection under your control |
| **Cross-provider fallback planning** | Prepare and review fallback ordering with ChatGPT; no hard-coded automatic ranking |
| **Controlled configuration** | Preview changes and use explicit confirmation, backups, and restore for supported Hermes settings |
| **Opt-in checks** | Avoid continuous inference probes and unexpected API usage |

## Who is it for?

Hermes Agent users who want to compare free-tier model options, reduce manual fallback maintenance, and keep configuration changes transparent. This is **not** a provider subscription, free API-key generator, or replacement for Hermes Agent.

## Current beta scope

- **Platform:** Windows, portable ZIP containing Python runtime.
- **First-run validation:** clean Windows Sandbox without Python or Hermes installed — ZIP extraction and GUI smoke test passed.
- **Not yet established:** compatibility with every Hermes version, provider, account entitlement, or model.
- **Privacy:** local runtime `state/` and private reports are excluded from the public repository; never upload credentials or Hermes config to GitHub.
- **License:** MIT for this project's original code. Bundled dependency licenses are included in `THIRD_PARTY_LICENSES/`.

## Develop from source

Windows + Python 3 with Tkinter:

```powershell
python -m pip install -r requirements.txt
python -B src/manager_ui.py
python -B -m unittest discover -s tests -v
```

To build the portable distribution, install `requirements-build.txt` and run `packaging/build-portable.ps1`. Review [third-party notices](THIRD_PARTY_NOTICES.md) before redistribution.

## Documentation & feedback

- [繁體中文使用說明](README.zh-TW.md)
- [Release notes and downloads](https://github.com/SmartyJohnway/hermes-free-model-manager/releases)
- [Issues — bugs and feature requests](https://github.com/SmartyJohnway/hermes-free-model-manager/issues)
- [Security and privacy](SECURITY.md)
- [License](LICENSE) · [Third-party notices](THIRD_PARTY_NOTICES.md)

If this project helps your Hermes workflow, consider starring the repository so other users can find it.
