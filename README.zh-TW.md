# Hermes Free Model Manager

[English](README.md) | **繁體中文**

[![Release](https://img.shields.io/github/v/release/SmartyJohnway/hermes-free-model-manager?include_prereleases&label=Release)](https://github.com/SmartyJohnway/hermes-free-model-manager/releases/tag/v0.1.0-beta)
[![Windows Portable](https://img.shields.io/badge/Windows-Portable-0078D4?logo=windows)](https://github.com/SmartyJohnway/hermes-free-model-manager/releases)
[![MIT License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**探索各家 AI Provider 的免費模型，透過獨立 Windows GUI 管理 Hermes Agent 的備援模型設定。**

這是一個非官方、以本機操作為主的 [Hermes Agent](https://github.com/NousResearch/hermes-agent) 輔助工具。你可以探索候選模型、檢視免費資格與 Tool Calling 證據、規劃跨供應商 Fallback，並在實際修改 Hermes 設定前先預覽差異。

> [!IMPORTANT]
> 本專案與 NousResearch 無隸屬關係。免費模型的額度、價格、可用性與工具呼叫能力可能隨供應商、帳戶及時間變動，無法保證。

## Windows 下載與啟動

**[下載 v0.1.0-beta Windows Portable](https://github.com/SmartyJohnway/hermes-free-model-manager/releases/tag/v0.1.0-beta)**

1. 在 Release 頁面的 **Assets** 下載 Windows Portable ZIP。
2. 將 ZIP **完整解壓縮**至可寫入的資料夾。
3. 雙擊 `START-MANAGER.cmd` 開啟圖形介面。

**不需要另外安裝 Python，也不需要安裝程式。** 要讀取或修改實際 Hermes 設定，仍須另外安裝 Hermes Agent。尚未安裝 Hermes 時，Manager 可以開啟，但模型型錄初始為空。此 Beta 尚未簽署程式碼憑證，Windows SmartScreen 可能顯示警告。

## 功能

| 功能 | 說明 |
| --- | --- |
| 免費模型探索 | 探索多家 Provider 的候選模型，不限於 Hermes 已設定的模型 |
| 資格證據區分 | 區分公開型錄資訊、個人帳戶免費資格與 Tool Calling 實測結果 |
| 主模型人工選擇 | 主模型由使用者決定，不強制自動改動 |
| 跨供應商 Fallback | 可與 ChatGPT 協作安排備援順序，不使用硬編碼推薦排名 |
| 受控設定變更 | 支援設定預覽、明確確認、備份與還原流程 |
| 主動觸發測試 | 避免持續背景推論及不必要的 API 消耗 |

## Beta 已驗證範圍

- Windows Portable ZIP 已於**沒有 Python、沒有 Hermes** 的乾淨 Windows Sandbox 完成解壓縮及 GUI 啟動測試。
- 尚未宣稱相容所有 Hermes 版本、Provider 或帳戶方案。
- 公開程式碼不包含私人 `state/`、帳戶稽核資料及使用者憑證。
- 原始程式採 MIT License；隨 ZIP 提供第三方套件授權文件。

## 原始碼開發

Windows + Python 3（含 Tkinter）：

```powershell
python -m pip install -r requirements.txt
python -B src/manager_ui.py
python -B -m unittest discover -s tests -v
```

## 相關連結

- [下載與版本紀錄](https://github.com/SmartyJohnway/hermes-free-model-manager/releases)
- [問題回報及功能建議](https://github.com/SmartyJohnway/hermes-free-model-manager/issues)
- [隱私與安全](SECURITY.md)
- [第三方授權](THIRD_PARTY_NOTICES.md)
- [MIT License](LICENSE)

若你覺得這個工具實用，歡迎為 Repository 加上 Star，讓其他 Hermes 使用者更容易找到。
