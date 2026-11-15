"""Standalone Windows Manager model browser. Never changes Hermes Desktop/config.

Tkinter (stdlib) GUI: read catalog, search, inspect evidence, choose a draft primary,
plan fallback and save a Manager-only draft. GET refresh runs only on explicit click.
"""
from __future__ import annotations

import argparse
import json
import hashlib
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from inventory import default_hermes_home
from manager_catalog import build_manager_catalog
from manager_ui_model import (
    ACCOUNT_LABELS, FREE_LABELS, STRONG_FREE, catalog_age_label,
    filter_models, load_hermes_selection, local_time, save_manager_draft,
)
from manager_ai_exchange import (
    GUIDE_PATH, REQUEST_PATH, RESPONSE_PATH, export_request, import_recommendation,
)
from configuration_preview import preview_from_draft
from configuration_operations import latest_reversible
from tool_batch_probe import MAX_BATCH
from tool_batch_controller import BatchTestController
from tool_results import load_manual_results, tool_evidence_description
from recommendation_dialog import open_recommendation_review

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = PROJECT / "reports" / "eligibility_catalog.json"
DEFAULT_DRAFT = PROJECT / "state" / "manager_draft.json"


def prior_tool_proofs() -> set[tuple[str, str]]:
    """Only historical positive records; never run an inference to earn a badge."""
    report = PROJECT / "reports" / "tool_calling_evidence.json"
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {
        (r["provider"], r["model"])
        for r in data.get("results", []) if isinstance(r, dict)
        and r.get("tool_call_confirmed") is True
        and r.get("result") == "pass"
        and isinstance(r.get("provider"), str)
        and isinstance(r.get("model"), str)
    }


class ManagerWindow:
    def __init__(self, root: tk.Tk, source: Path, home: Path, draft: Path):
        self.root, self.source, self.home, self.draft = root, source, home, draft
        self.data = {}
        self.rows: list[dict] = []
        self.filtered: list[dict] = []
        self.by_item: dict[str, dict] = {}
        self.fallback: list[tuple[str, str]] = []
        self.ai_reasons: dict[tuple[str, str], str] = {}
        self.ai_summary = ""
        self.ai_primary_reason = ""
        self.primary = ("", "")
        self.user_chose_primary = False
        self.refresh_queue: queue.Queue[tuple[bool, str]] = queue.Queue()
        self.refreshing = False
        self.tool_batch_running = False
        self.manual_results_folder = self.source.parent / "manual_tool_calling"
        self.manual_tool_results = {"latest": {}, "last_pass": {},
                                    "valid_files": 0, "ignored_files": 0,
                                    "incomplete_files": 0}
        self.baseline = {"primary": ("", ""), "fallback": [], "config_sha256": ""}
        self.root.title("Hermes Free Model Manager｜免費模型管理（研發預覽）")
        self.root.geometry("1280x860")
        self.root.minsize(980, 710)
        self._layout()
        self.batch_controller = BatchTestController(self)
        self._load(refresh_baseline=True)
        self._restore_draft()
        self.root.bind("<Control-f>", lambda _: self.search_entry.focus_set())

    def _layout(self):
        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure(".", font=("Microsoft JhengHei UI", 10))
        style.configure("Treeview", rowheight=24, font=("Microsoft JhengHei UI", 9))
        style.configure("Treeview.Heading", font=("Microsoft JhengHei UI", 9, "bold"))
        style.configure("Workflow.TButton", padding=(13, 9), font=("Microsoft JhengHei UI", 10, "bold"))

        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Hermes Free Model Manager",
                  font=("Microsoft JhengHei UI", 17, "bold")).pack(anchor="w")
        ttk.Label(outer, text="免費模型清單與 AI 合作推薦｜僅手動確認才會套用／還原 Hermes；不自動推論",
                  foreground="#56616d").pack(anchor="w", pady=(2, 4))

        self.summary = tk.StringVar(value="載入中…")
        ttk.Label(outer, textvariable=self.summary).pack(anchor="w", pady=(0, 3))

        bar = ttk.Frame(outer)
        bar.pack(fill="x", pady=(0, 4))
        ttk.Label(bar, text="供應商").pack(side="left")
        self.provider = tk.StringVar(value="全部")
        self.provider_combo = ttk.Combobox(bar, textvariable=self.provider,
                                           width=17, state="readonly")
        self.provider_combo.pack(side="left", padx=(5, 12))
        self.provider_combo.bind("<<ComboboxSelected>>", lambda _: self._render())
        ttk.Label(bar, text="免費依據").pack(side="left")
        self.kind = tk.StringVar(value="全部")
        self.kind_combo = ttk.Combobox(
            bar, textvariable=self.kind, width=18, state="readonly",
            values=["全部"] + list(FREE_LABELS.values()))
        self.kind_combo.pack(side="left", padx=(5, 12))
        self.kind_combo.bind("<<ComboboxSelected>>", lambda _: self._render())
        ttk.Label(bar, text="搜尋").pack(side="left")
        self.query = tk.StringVar()
        self.search_entry = ttk.Entry(bar, textvariable=self.query, width=26)
        self.search_entry.pack(side="left", padx=(5, 12), fill="x", expand=True)
        self.query.trace_add("write", lambda *_: self._render())
        self.refresh_button = ttk.Button(bar, text="手動更新官方型錄（GET）",
                                         command=self._confirm_refresh)
        self.refresh_button.pack(side="right")

        # Pin all next-step actions ABOVE both scrolling panes. Never place
        # critical actions after an expanding Treeview (they get clipped).
        workflow = ttk.LabelFrame(outer, text="操作流程｜推薦 → 儲存 → 預覽 → 套用／還原", padding=(8, 5))
        workflow.pack(fill="x", pady=(0, 4))
        workflow_top = ttk.Frame(workflow)
        workflow_top.pack(fill="x")
        workflow_bottom = ttk.Frame(workflow)
        workflow_bottom.pack(fill="x", pady=(4, 0))
        self.workflow_buttons = {}
        for key, caption, callback in (
            ("export", "① 匯出給 ChatGPT", self._export_ai),
            ("import", "② 載入 AI 建議", self._import_ai),
            ("save", "③ 儲存草稿", self._save),
            ("preview", "④ 檢視 Hermes 差異（唯讀）", self._preview_hermes),
        ):
            btn = ttk.Button(workflow_top, text=caption, command=callback,
                             style="Workflow.TButton")
            btn.pack(side="left", padx=(0, 9))
            self.workflow_buttons[key] = btn
        self.validation_button = ttk.Button(
            workflow_bottom, text="驗證 ChatGPT 推薦候選（7/30 天）",
            command=self._review_recommendations)
        self.validation_button.pack(side="left", padx=(0, 9))
        self.copy_request_path_button = ttk.Button(
            workflow_bottom, text="複製匯出 JSON 路徑",
            command=self._copy_request_path)
        self.copy_request_path_button.pack(side="left", padx=(0, 9))
        for key, caption, callback in (
            ("apply", "⑤ 套用至 Hermes", self._apply_hermes),
            ("restore", "還原上次設定", self._restore_hermes),
        ):
            btn = ttk.Button(workflow_bottom, text=caption, command=callback,
                             style="Workflow.TButton")
            btn.pack(side="left", padx=(0, 9))
            self.workflow_buttons[key] = btn
        self.draft_indicator = tk.StringVar(value="草稿：尚未儲存")
        ttk.Label(workflow_bottom, textvariable=self.draft_indicator,
                  foreground="#56616d").pack(side="right", padx=(8, 4))

        # The status area is reserved before the resizable content. A long
        # message must not be able to steal room from workflow buttons.
        footer = ttk.Frame(outer)
        footer.pack(side="bottom", fill="x", pady=(6, 0))
        self.status = tk.StringVar(
            value="先儲存草稿、檢視差異；只有按⑤並確認才會寫入 Hermes。")
        ttk.Label(footer, textvariable=self.status, width=85, anchor="w",
                  foreground="#56616d").pack(side="left", fill="x", expand=True)

        # Vertical split gives the Fallback list actual usable space while
        # preserving a searchable model directory above it.
        self.body_panes = ttk.Panedwindow(outer, orient="vertical")
        self.body_panes.pack(fill="both", expand=True)
        upper = ttk.Frame(self.body_panes)
        self.body_panes.add(upper, weight=2)

        middle = ttk.Panedwindow(upper, orient="horizontal")
        middle.pack(fill="both", expand=True)
        tablebox = ttk.LabelFrame(middle, text="Provider 免費模型與候選清單", padding=6)
        middle.add(tablebox, weight=3)
        columns = ("provider", "model", "free", "tools")
        self.tree = ttk.Treeview(tablebox, columns=columns, show="headings",
                                 selectmode="extended", height=6)
        for col, title, width in [
            ("provider", "Provider", 120), ("model", "Model", 330),
            ("free", "免費資格依據", 140), ("tools", "Tool Calling", 145)
        ]:
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, stretch=col == "model")
        scroll = ttk.Scrollbar(tablebox, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._model_selection_changed)

        detail_box = ttk.LabelFrame(middle, text="模型來源、帳戶與查核證據", padding=8)
        middle.add(detail_box, weight=2)
        self.detail = tk.Text(detail_box, wrap="word", state="disabled", height=6,
                              font=("Microsoft JhengHei UI", 10),
                              padx=7, pady=7)
        self.detail.pack(fill="both", expand=True)

        actions = ttk.Frame(upper)
        actions.pack(side="bottom", fill="x", pady=(5, 3))
        ttk.Button(actions, text="選為主模型（草稿）", command=self._set_primary).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="加入 Fallback（草稿）", command=self._add_fallback).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="複製模型 ID", command=self._copy_model).pack(side="left", padx=(0, 8))
        self.batch_button = ttk.Button(
            actions, text="選取模型批次 Tool Calling…",
            command=self._confirm_batch_tool_probe)
        self.batch_button.pack(side="left", padx=(0, 10))
        self.batch_selection_text = tk.StringVar(value="已選 0/6｜Ctrl／Shift 多選")
        ttk.Label(actions, textvariable=self.batch_selection_text).pack(side="left")

        lower = ttk.LabelFrame(self.body_panes,
                               text="主模型／Fallback 草稿｜尚未套用 Hermes", padding=(8, 5))
        self.lower_frame = lower
        self.body_panes.add(lower, weight=3)
        self.primary_label = tk.StringVar()
        ttk.Label(lower, textvariable=self.primary_label).pack(anchor="w", pady=(0, 6))

        items = ttk.Frame(lower)
        items.pack(fill="both", expand=True)
        self.fb_tree = ttk.Treeview(items, columns=("rank", "provider", "model", "kind", "reason"),
                                    show="headings", height=3, selectmode="browse")
        for col, text, width in [
            ("rank", "#", 36), ("provider", "Provider", 110),
            ("model", "Fallback Model", 300), ("kind", "免費依據", 125),
            ("reason", "ChatGPT 推薦理由", 260)
        ]:
            self.fb_tree.heading(col, text=text)
            self.fb_tree.column(col, width=width, stretch=col == "model")
        fb_scroll = ttk.Scrollbar(items, orient="vertical", command=self.fb_tree.yview)
        self.fb_tree.configure(yscrollcommand=fb_scroll.set)
        self.fb_tree.pack(side="left", fill="both", expand=True)
        fb_scroll.pack(side="left", fill="y")
        controls = ttk.Frame(items)
        controls.pack(side="right", padx=(8, 0))
        ttk.Button(controls, text="上移", width=10,
                   command=lambda: self._move(-1)).pack(fill="x", pady=2)
        ttk.Button(controls, text="下移", width=10,
                   command=lambda: self._move(1)).pack(fill="x", pady=2)
        ttk.Button(controls, text="移除", width=10,
                   command=self._remove_fallback).pack(fill="x", pady=2)
        self.fb_tree.bind("<<TreeviewSelect>>", lambda _: self._show_ai_reason())
        # Keep long AI reasons scrollable instead of allowing an unbounded
        # wrapped label to push all important controls off-screen.
        self.ai_explanation = tk.StringVar(value="選擇 Fallback 可在此閱讀 AI 的完整推薦理由。")
        explanation_frame = ttk.Frame(lower)
        explanation_frame.pack(side="bottom", fill="x", pady=(5, 1))
        self.explanation_text = tk.Text(
            explanation_frame, height=2, wrap="word", state="disabled",
            font=("Microsoft JhengHei UI", 9), padx=6, pady=4, relief="flat")
        reason_scroll = ttk.Scrollbar(
            explanation_frame, orient="vertical", command=self.explanation_text.yview)
        self.explanation_text.configure(yscrollcommand=reason_scroll.set)
        self.explanation_text.pack(side="left", fill="x", expand=True)
        reason_scroll.pack(side="right", fill="y")
        self.ai_explanation.trace_add("write", self._update_explanation)
        self._update_explanation()

    def _update_explanation(self, *_):
        self.explanation_text.configure(state="normal")
        self.explanation_text.delete("1.0", "end")
        self.explanation_text.insert("1.0", self.ai_explanation.get())
        self.explanation_text.configure(state="disabled")

    def _selection_matches_hermes(self) -> bool:
        """A configuration equality check, not a live Gateway health claim."""
        return (self.primary == self.baseline["primary"]
                and self.fallback == self.baseline["fallback"])

    def _mark_draft_modified(self):
        self.draft_indicator.set("草稿：尚有未儲存變更")
        self.lower_frame.configure(text="主模型／Fallback 草稿｜尚未套用目前修改")

    def _load(self, *, refresh_baseline=False):
        try:
            data = json.loads(self.source.read_text(encoding="utf-8"))
            catalog = build_manager_catalog(data, tool_verified=prior_tool_proofs())
            self.manual_tool_results = load_manual_results(self.manual_results_folder)
            for row in catalog["rows"]:
                pair = (row["provider"], row["model"])
                row["manual_tool_result"] = self.manual_tool_results["latest"].get(pair)
                row["manual_last_pass"] = self.manual_tool_results["last_pass"].get(pair)
            if refresh_baseline:
                try:
                    self.baseline = load_hermes_selection(self.home)
                except FileNotFoundError:
                    self.baseline = {"primary": ("", ""), "fallback": [], "config_sha256": ""}
                    self.status.set("Hermes not detected. Install Hermes to manage its configuration; model discovery remains available.")
                if not self.user_chose_primary:
                    self.primary = self.baseline["primary"]
            self.data, self.rows = catalog, catalog["rows"]
            self._populate_providers()
            self._render()
            self._show_primary()
            self._render_fallback()
        except (OSError, ValueError, KeyError) as exc:
            self.status.set("資料載入失敗：" + type(exc).__name__)
            messagebox.showerror("Manager 資料載入失敗",
                                 "請先確認 M2 模型快照存在，或執行手動更新。\n"
                                 + type(exc).__name__)

    def refresh_manual_results(self, *, report_path: str | None = None) -> int:
        """Re-read completed reports and update the model table immediately.

        Never re-query any Provider and never mutate M2 catalog / Hermes config.
        """
        self.manual_tool_results = load_manual_results(self.manual_results_folder)
        for row in self.rows:
            pair = (row["provider"], row["model"])
            row["manual_tool_result"] = self.manual_tool_results["latest"].get(pair)
            row["manual_last_pass"] = self.manual_tool_results["last_pass"].get(pair)
        self._render()
        if not report_path:
            return len(self.manual_tool_results["latest"])
        target = str(Path(report_path).resolve())
        return sum(
            record["report_path"] == target
            for record in self.manual_tool_results["latest"].values()
        )

    def _restore_draft(self):
        """Restore only an existing Manager draft if its baseline still matches."""
        if not self.draft.is_file() or not self.rows:
            return
        try:
            data = json.loads(self.draft.read_text(encoding="utf-8"))
            if (data.get("schema") != "hermes-free-manager-selection.v1"
                    or data.get("baseline_config_sha256") != self.baseline["config_sha256"]):
                self.status.set("舊草稿的 Hermes 設定基準已變更；未自動載入。")
                return
            main = data.get("primary") or {}
            selected = (main.get("provider"), main.get("model"))
            allowed = {(r["provider"], r["model"]) for r in self.rows}
            if selected not in allowed:
                self.status.set("舊草稿主模型不在本次清單；未自動載入。")
                return
            fallback = []
            for item in data.get("fallback", []):
                pair = (item.get("provider"), item.get("model"))
                if pair in allowed and pair != selected and pair not in fallback:
                    fallback.append(pair)
            self.primary = selected
            self.fallback = fallback
            self.ai_reasons = {
                (item["provider"], item["model"]): item["reason"]
                for item in data.get("fallback", [])
                if isinstance(item, dict)
                and isinstance(item.get("reason"), str)
                and (item.get("provider"), item.get("model")) in fallback
            }
            self.ai_summary = data.get("ai_summary") if isinstance(
                data.get("ai_summary"), str) else ""
            self.ai_primary_reason = data.get("ai_primary_reason") if isinstance(
                data.get("ai_primary_reason"), str) else ""
            self.user_chose_primary = True
            self._show_primary()
            self._render_fallback()
            if self.ai_summary:
                self.ai_explanation.set(
                    "ChatGPT 摘要：" + self.ai_summary[:350]
                    + "｜點選 Fallback 查看完整理由。")
            if self._selection_matches_hermes():
                self.status.set("已恢復草稿及 AI 理由；目前內容已與正式 Hermes 設定一致。")
                self.draft_indicator.set(f"已套用／設定一致（{len(self.fallback)} 筆 Fallback）")
                self.lower_frame.configure(text="主模型／Fallback｜與正式 Hermes 設定一致")
            else:
                self.status.set("已恢復上次 Manager 草稿與 AI 理由；尚未套用正式 Hermes。")
                self.draft_indicator.set(f"草稿：已儲存（{len(self.fallback)} 筆 Fallback）")
                self.lower_frame.configure(text="主模型／Fallback 草稿｜尚未套用 Hermes")
        except (OSError, ValueError, TypeError, AttributeError):
            self.status.set("舊草稿無法驗證，未載入；Hermes 保持不變。")

    def _populate_providers(self):
        providers = ["全部"] + sorted({r["provider"] for r in self.rows})
        self.provider_combo["values"] = providers
        if self.provider.get() not in providers:
            self.provider.set("全部")

    def _render(self):
        chosen = self.tree.selection()
        chosen_pairs = {
            (self.by_item[x]["provider"], self.by_item[x]["model"])
            for x in chosen if x in self.by_item
        }
        reverse_labels = {v: k for k, v in FREE_LABELS.items()}
        self.filtered = filter_models(
            self.rows, provider=self.provider.get(), keyword=self.query.get(),
            evidence=reverse_labels.get(self.kind.get(), "全部"))
        self.tree.delete(*self.tree.get_children())
        self.by_item = {}
        for i, row in enumerate(self.filtered):
            iid = str(i)
            self.by_item[iid] = row
            self.tree.insert("", "end", iid=iid, values=(
                row["provider"], row["model"],
                FREE_LABELS.get(row["free_evidence"], "未分類"),
                (row["manual_tool_result"]["label"]
                 if row.get("manual_tool_result")
                 else "歷史 PASS" if row["tool_calling"] == "tested_pass"
                 else "未測／未證實")))
            if (row["provider"], row["model"]) in chosen_pairs:
                self.tree.selection_add(iid)
        timestamp = self.data.get("source_snapshot_at_utc")
        age = catalog_age_label(timestamp)
        self.summary.set(
            f"型錄快照（台灣時間）：{local_time(timestamp)}   |   {age}"
            f"   |   Provider {len(self.data.get('provider_counts', {}))} 家"
            f"   |   候選 {len(self.rows)} 筆，篩選顯示 {len(self.filtered)} 筆"
            f"   |   手動測試 {len(self.manual_tool_results['latest'])} 型號"
        )
        self._detail()
        self._update_batch_selection()

    def _selected(self) -> dict | None:
        selected = self.tree.selection()
        return self.by_item.get(selected[0]) if selected else None

    def _detail(self):
        row = self._selected()
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        if row:
            kind = row["free_evidence"]
            warning = ("官方類型／價格資料已標示免費，但不能保證剩餘額度或推論成功"
                       if kind in STRONG_FREE else
                       "這是具有方案或使用條件的候選，不代表逐型號已免費驗證")
            complete = row.get("catalog_complete")
            scope = ("完整" if complete is True else
                     "不完整" if complete is False else "未宣稱完整")
            text = (
                f"Provider：{row['provider']}\nModel：{row['model']}\n\n"
                f"免費依據：{FREE_LABELS.get(kind, kind)}\n"
                f"判定提醒：{warning}\n"
                f"逐模型官方零費率：{'有證據' if row.get('price_marked_free') else '無零費率證據／使用其他免費方案'}\n"
                f"帳戶證據：{ACCOUNT_LABELS.get(row.get('account_ui_evidence_20261113'), '未取得')}\n"
                f"型錄可讀：{'是' if row.get('catalog_accessible') else '否'}\n"
                f"供應商型錄完整性：{scope}\n"
                f"本次型錄快照：{local_time(row.get('catalog_snapshot_utc'))}\n"
                f"免費政策查核：{local_time(row.get('policy_observed_at'))}\n"
                f"帳戶網頁查核：2026-11-13（歷史快照）\n"
                f"目前剩餘額度：未知（不進行消耗額度的探測）\n"
                f"Tool Calling：\n{tool_evidence_description(row)}\n\n"
                f"參考來源：\n{row.get('evidence_url') or '僅本機掃描'}\n"
                f"{row.get('evidence_reference') or ''}\n\n"
                f"風險條件：\n{row.get('billing_guard') or '未提供'}\n"
            )
            self.detail.insert("1.0", text)
        else:
            self.detail.insert("1.0", "請從左側清單選擇一個模型，以查看免費依據與查核時間。")
        self.detail.configure(state="disabled")

    def _show_primary(self):
        current = "/".join(self.baseline["primary"])
        draft = "/".join(self.primary)
        self.primary_label.set(f"正式 Hermes 主模型：{current}   |   Manager 草稿主模型：{draft}")

    def _set_primary(self):
        row = self._selected()
        if row is None:
            messagebox.showinfo("尚未選擇", "請先在表格選擇一個模型。")
            return
        if row["free_evidence"] not in STRONG_FREE:
            if not messagebox.askyesno(
                "條件式免費模型", "此模型具方案／額度／試用條件，不是逐型號零價格證據。"
                "\n要將它作為 Manager 草稿主模型嗎？正式 Hermes 不會被修改。"
            ):
                return
        self.primary = (row["provider"], row["model"])
        self.ai_primary_reason = ""
        self.user_chose_primary = True
        self.fallback = [p for p in self.fallback if p != self.primary]
        self._show_primary()
        self._render_fallback()
        self._mark_draft_modified()
        self.status.set("已選擇主模型草稿；尚未寫入正式 Hermes。")

    def _copy_model(self):
        row = self._selected()
        if row is None:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(row["model"])
        self.status.set("已複製模型 ID，可自行貼入 Hermes；未呼叫 API。")

    def _add_fallback(self):
        row = self._selected()
        if row is None:
            return
        pair = (row["provider"], row["model"])
        if pair == self.primary or pair in self.fallback:
            self.status.set("此模型已在 Fallback，或與主模型相同。")
            return
        if row["free_evidence"] not in STRONG_FREE and not messagebox.askyesno(
            "確認條件式候選", "此模型是條件式免費／試用候選，請留意費用與方案限制。\n"
            "確定加入 Manager 備援草稿嗎？"
        ):
            return
        self.fallback.append(pair)
        self._render_fallback()
        self._mark_draft_modified()

    def _export_ai(self):
        """Publish a credential-free AI review package, then copy task instructions."""
        try:
            baseline = load_hermes_selection(self.home)
            if baseline["config_sha256"] != self.baseline["config_sha256"]:
                messagebox.showwarning(
                    "Hermes 設定已變更", "目前正式 Hermes 設定與開啟 Manager 時不同；請重新啟動後再匯出。")
                return
            preferred = self.primary if self.user_chose_primary else None
            request = export_request(
                self.source, self.home, REQUEST_PATH, preferred_primary=preferred)
            task = (
                "請使用 Desktop Commander Local 執行 Hermes Free Model Manager 的 AI 推薦任務。\n"
                f"請先閱讀 AI 工作說明：{GUIDE_PATH}\n"
                f"再讀取完整候選與設定基準：{REQUEST_PATH}\n"
                "你負責基於已有免費帳戶／供應商證據、模型能力及現有 Hermes 工作型態，"
                "提出主模型（可保留目前選擇）與按優先順序排列的 Fallback，"
                "逐項寫明推薦理由與限制。Tool Calling 未測不能當成排除條件，"
                "禁止為了排名對所有模型執行推論。\n"
                f"依工作說明的固定 JSON 契約寫入：{RESPONSE_PATH}\n"
                "寫完後執行本機 validate；僅生成 Manager 建議，不得修改正式 Hermes 設定。"
            )
            self.root.clipboard_clear()
            self.root.clipboard_append(task)
            self.status.set(
                f"已匯出 {request['candidate_count']} 筆候選及 AI 工作指令（已複製到剪貼簿）。")
            messagebox.showinfo(
                "已準備好交給 ChatGPT",
                f"已匯出 {request['candidate_count']} 筆模型（不含金鑰）。\n"
                f"輸入：{REQUEST_PATH}\n輸出：{RESPONSE_PATH}\n"
                "ChatGPT 任務指令已複製，可直接貼到目前聊天視窗。")
        except (OSError, ValueError, TypeError, KeyError) as exc:
            messagebox.showerror("AI 資料匯出失敗", str(exc))

    def _copy_request_path(self):
        """Copy the exact path only, for direct pasting into ChatGPT."""
        if not REQUEST_PATH.is_file():
            messagebox.showinfo(
                "請先匯出", "請先按①匯出給 ChatGPT，再複製 Request JSON 路徑。")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(str(REQUEST_PATH.resolve()))
        self.status.set("已複製完整 Request JSON 路徑，可直接貼給 ChatGPT。")

    def _import_ai(self):
        """Review validated AI recommendation. Never invoke an algorithm or Hermes write."""
        try:
            validated = import_recommendation(
                self.source, self.home, REQUEST_PATH, RESPONSE_PATH)
            request = json.loads(REQUEST_PATH.read_text(encoding="utf-8"))
            preferred = request.get("preferred_primary", {})
            current_pair = (preferred.get("provider"), preferred.get("model"))
            if current_pair != self.primary:
                messagebox.showwarning(
                    "目前選擇與 AI 請求不同",
                    "在匯出 AI 請求後，主模型選擇已變更。請重新匯出請求再讓 AI 推薦。")
                return
            pair = validated["primary"]
            listed = {(r["provider"], r["model"]) for r in self.rows}
            # Existing Hermes primary can be retained even if not in the free list.
            if pair not in listed and pair != self.baseline["primary"]:
                raise ValueError("AI 建議主模型不在目前免費候選清單")
            lines = [
                "ChatGPT 建議（尚未套用）：",
                f"主模型：{pair[0]} / {pair[1]}",
                f"理由：{validated['primary_reason']}",
                f"Fallback：{len(validated['fallback'])} 筆",
            ]
            for i, row in enumerate(validated["fallback"][:10], 1):
                lines.append(f"  {i}. {row['provider']} / {row['model']}")
                lines.append(f"     {row['reason'][:75]}")
            if len(validated["fallback"]) > 10:
                lines.append(f"  …其餘 {len(validated['fallback']) - 10} 筆載入後可在介面查看")
            if validated["conditional_count"]:
                lines.append(
                    f"\n注意：{validated['conditional_count']} 筆含條件式免費／額度／試用模型，"
                    "須由你判斷是否接受。")
            lines.append("\n確認後只更新 Manager 草稿；正式 Hermes 完全不變。")
            if not messagebox.askyesno("審核 ChatGPT 的建議", "\n".join(lines)):
                return
            self.primary = pair
            self.user_chose_primary = pair in listed
            self.fallback = [(r["provider"], r["model"])
                             for r in validated["fallback"]]
            self.ai_reasons = {(r["provider"], r["model"]): r["reason"]
                               for r in validated["fallback"]}
            self.ai_summary = validated["summary"]
            self.ai_primary_reason = validated["primary_reason"]
            self._show_primary()
            self._render_fallback()
            self.ai_explanation.set(
                "主模型理由：" + validated["primary_reason"][:200]
                + "｜ChatGPT 摘要：" + validated["summary"][:350]
                + "｜點選 Fallback 可查看逐項理由。")
            self._mark_draft_modified()
            self.status.set(
                f"已讀入 ChatGPT 建議（{len(self.fallback)} 筆 Fallback），尚未儲存草稿或寫入 Hermes。")
        except (OSError, ValueError, KeyError, TypeError,
                json.JSONDecodeError) as exc:
            messagebox.showerror(
                "無法接受 ChatGPT 建議",
                str(exc) + "\n請檢查 JSON 契約、模型清單及型錄／Hermes 設定是否已變更。")

    def _render_fallback(self):
        self.fb_tree.delete(*self.fb_tree.get_children())
        index = {(r["provider"], r["model"]): r for r in self.rows}
        for i, pair in enumerate(self.fallback):
            row = index.get(pair, {})
            self.fb_tree.insert("", "end", iid=str(i),
                                values=(i + 1, pair[0], pair[1],
                                        FREE_LABELS.get(row.get("free_evidence"), "未歸類"),
                                        self.ai_reasons.get(pair, "手動挑選")[:90]))

    def _show_ai_reason(self):
        selected = self.fb_tree.selection()
        if not selected:
            return
        pos = int(selected[0])
        if pos >= len(self.fallback):
            return
        pair = self.fallback[pos]
        rationale = self.ai_reasons.get(pair, "這是使用者手動加入的模型，沒有 ChatGPT 推薦理由。")
        self.ai_explanation.set(
            f"{pair[0]} / {pair[1]}\n推薦／選擇理由：{rationale}")

    def _remove_fallback(self):
        selected = self.fb_tree.selection()
        if selected:
            self.fallback.pop(int(selected[0]))
            self._render_fallback()
            self._mark_draft_modified()

    def _move(self, direction: int):
        selected = self.fb_tree.selection()
        if not selected:
            return
        i = int(selected[0])
        j = i + direction
        if 0 <= j < len(self.fallback):
            self.fallback[i], self.fallback[j] = self.fallback[j], self.fallback[i]
            self._render_fallback()
            self.fb_tree.selection_set(str(j))
            self._mark_draft_modified()

    def _save(self):
        if not self.user_chose_primary:
            messagebox.showinfo("選擇主模型", "請先在清單中明確選擇一個主模型，才可存草稿。")
            return
        listed = {(r["provider"], r["model"]) for r in self.rows}
        if self.primary not in listed or any(pair not in listed for pair in self.fallback):
            messagebox.showerror("草稿不一致", "部分模型已不在目前候選清單，請重新選擇。")
            return
        current = load_hermes_selection(self.home)
        if current["config_sha256"] != self.baseline["config_sha256"]:
            messagebox.showwarning("Hermes 設定有變化",
                                   "Hermes 設定已從載入時變更。請重新啟動 Manager 確認基準。")
            return
        lines = [f"主模型：{self.primary[0]} / {self.primary[1]}", "Fallback："]
        lines.extend(f"  {i + 1}. {p} / {m}" for i, (p, m) in enumerate(self.fallback))
        lines.append("\n此操作只儲存 Manager 草稿 JSON，不會更改正式 Hermes。")
        if not messagebox.askyesno("確認儲存草稿", "\n".join(lines)):
            return
        try:
            save_manager_draft(
                self.draft, primary=self.primary, fallback=self.fallback,
                source_utc=self.data.get("source_snapshot_at_utc"),
                config_sha256=self.baseline["config_sha256"],
                ai_reasons=self.ai_reasons, ai_summary=self.ai_summary,
                ai_primary_reason=self.ai_primary_reason)
            if self._selection_matches_hermes():
                self.draft_indicator.set(f"已套用／設定一致（{len(self.fallback)} 筆 Fallback）")
                self.lower_frame.configure(text="主模型／Fallback｜與正式 Hermes 設定一致")
                self.status.set("草稿已保存，內容與正式 Hermes 一致；這次沒有額外寫入 Hermes。")
            else:
                self.draft_indicator.set(f"草稿：已儲存（{len(self.fallback)} 筆 Fallback）")
                self.lower_frame.configure(text="主模型／Fallback 草稿｜尚未套用 Hermes")
                self.status.set("草稿已保存：" + str(self.draft) + "；Hermes 保持不變。")
        except (OSError, ValueError) as exc:
            messagebox.showerror("儲存失敗", type(exc).__name__)

    def _preview_hermes(self):
        """Compare a previously saved Manager draft with the live Hermes baseline; no writes."""
        if not self.draft.is_file():
            messagebox.showinfo("先儲存 Manager 草稿",
                                "目前只有畫面上的選擇。請先按「預覽並儲存 Manager 草稿」。")
            return
        try:
            if self.draft.stat().st_size > 500_000:
                raise ValueError("草稿大小異常")
            saved = json.loads(self.draft.read_text(encoding="utf-8"))
            before = load_hermes_selection(self.home)
            plan = preview_from_draft(saved, before, self.data)
            if (plan["primary_after"] != self.primary
                    or plan["fallback_after"] != self.fallback):
                messagebox.showwarning(
                    "畫面與已儲存草稿不同",
                    "請先重新儲存目前畫面上的主模型／Fallback，再檢視差異。")
                return
            lines = [
                "M4 唯讀差異預覽（不套用）",
                f"主模型：{plan['primary_before'][0]} / {plan['primary_before'][1]}",
                f"   → {plan['primary_after'][0]} / {plan['primary_after'][1]}",
                f"Fallback：原 {len(plan['fallback_before'])} 筆 → 新 {len(plan['fallback_after'])} 筆",
                "",
                "套用後的順序：",
            ]
            lines.extend(
                f"  {i}. {provider} / {model}"
                for i, (provider, model) in enumerate(plan["fallback_after"], 1)
            )
            lines.extend([
                "",
                f"新增 {len(plan['fallback_added'])} 筆；移除 {len(plan['fallback_removed'])} 筆。",
                f"條件式免費模型 {len(plan['conditional_fallback'])} 筆，需留意方案與額度。",
                "",
                ("目前已儲存草稿與正式 Hermes 設定一致；這次按鈕沒有執行寫入。"
                 if not plan["fallback_reordered"] and not plan["primary_changed"]
                 else "這只是設定差異預覽；本次按鈕沒有寫入正式 Hermes。"),
            ])
            messagebox.showinfo("M4 Hermes 設定差異", "\n".join(lines))
            self.status.set("M4 差異預覽完成，沒有寫入 Hermes。")
        except (OSError, ValueError, TypeError, KeyError) as exc:
            messagebox.showerror("無法建立安全預覽", str(exc))

    def _m4_official_profile_only(self) -> bool:
        """Explicitly prevent a research/test window from writing production."""
        if (self.source.resolve() != DEFAULT_SOURCE.resolve()
                or self.draft.resolve() != DEFAULT_DRAFT.resolve()
                or self.home.resolve() != default_hermes_home().resolve()):
            messagebox.showerror(
                "M4 正式操作不可用",
                "此視窗使用自訂研究資料或隔離 Hermes Profile，不能執行正式設定套用／還原。")
            return False
        return True

    def _m4_command(self, *args: str) -> dict:
        """Run the actual official Hermes venv, not the Manager Python runtime."""
        native = (Path(os.environ["LOCALAPPDATA"]) / "hermes"
                  / "hermes-agent" / "venv" / "Scripts" / "python.exe")
        script = PROJECT / "scripts" / "configuration_transaction.py"
        if not native.is_file() or not script.is_file():
            raise RuntimeError("找不到正式 Hermes Python 環境或 M4 操作程式。")
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        proc = subprocess.run(
            [str(native), "-B", str(script), *args],
            cwd=str(PROJECT), env=env, text=True, encoding="utf-8",
            errors="replace", capture_output=True, timeout=90, check=False)
        if proc.returncode != 0:
            # Avoid exposing full environment/config dumps in the GUI.
            detail = proc.stdout.strip()[-500:] or f"exit={proc.returncode}"
            raise RuntimeError("正式操作被拒絕或驗證失敗：" + detail)
        return json.loads(proc.stdout.strip())

    def _refresh_after_m4(self):
        """Reload the committed Manager draft and official config after transaction."""
        self.user_chose_primary = False
        self.primary = ("", "")
        self.fallback = []
        self.ai_reasons = {}
        self.ai_summary = ""
        self.ai_primary_reason = ""
        self._load(refresh_baseline=True)
        self._restore_draft()

    def _apply_hermes(self):
        """Manual confirmation -> SHA-bound official native apply -> UI refresh."""
        if not self._m4_official_profile_only():
            return
        if not self.draft.is_file():
            messagebox.showinfo("請先儲存", "請先按③儲存主模型及 Fallback 草稿。")
            return
        try:
            saved_bytes = self.draft.read_bytes()
            saved = json.loads(saved_bytes.decode("utf-8-sig"))
            current = load_hermes_selection(self.home)
            plan = preview_from_draft(saved, current, self.data)
            if plan["primary_after"] != self.primary or plan["fallback_after"] != self.fallback:
                messagebox.showwarning(
                    "請先儲存變更", "畫面與已儲存草稿不同，請先按③儲存草稿。")
                return
            if not plan["primary_changed"] and not plan["fallback_reordered"]:
                messagebox.showinfo(
                    "已與 Hermes 一致",
                    "正式主模型和 Fallback 已符合 Manager 草稿，不需重複寫入或備份。")
                return
            head = "\n".join(
                f"  {i}. {p} / {m}"
                for i, (p, m) in enumerate(plan["fallback_after"][:5], 1))
            warning = (
                f"\n\n其中 {len(plan['conditional_fallback'])} 筆屬條件式免費／額度／試用候選，"
                "套用不代表實際免費或工具相容。"
                if plan["conditional_fallback"] else "")
            message = (
                "正式修改官方 Hermes 設定？\n\n"
                f"主模型：{plan['primary_before'][0]} / {plan['primary_before'][1]}"
                f"\n→ {plan['primary_after'][0]} / {plan['primary_after'][1]}\n"
                f"Fallback：{len(plan['fallback_before'])} → "
                f"{len(plan['fallback_after'])} 筆\n\n"
                "新順序前五筆：\n" + head + warning +
                "\n\n將備份原始設定、比對 SHA、使用官方 API 寫入並讀回。"
                "\n不會向模型發送測試推論；也不會自動執行排程。")
            if not messagebox.askyesno("⑤ 確認正式套用至 Hermes", message):
                return
            digest = hashlib.sha256(saved_bytes).hexdigest()
            self.status.set("正在備份並透過 Hermes 官方設定介面套用…")
            result = self._m4_command(
                "--apply-confirmed", "--expect-draft-sha256", digest)
            if result.get("status") not in (
                "PRODUCTION_MANAGER_DRAFT_APPLY_VERIFIED", "ALREADY_MATCHES_HERMES"):
                raise RuntimeError("M4 未回報正式套用成功")
            self._refresh_after_m4()
            if result.get("manager_reconcile", {}).get("status") == "NEEDS_MANUAL_RECONCILIATION":
                messagebox.showwarning(
                    "Hermes 已套用，Manager 草稿待同步",
                    "官方 Hermes 設定已核對寫入成功，但 Manager 草稿同步失敗。"
                    "\n請保留交易編號並重新載入 Manager 檢查。"
                    f"\n交易：{result.get('transaction_id')}")
                return
            messagebox.showinfo(
                "Hermes 設定已套用",
                f"正式套用完成。\n"
                f"主模型：{self.primary[0]} / {self.primary[1]}\n"
                f"Fallback：{len(self.fallback)} 筆\n"
                f"備份交易：{result.get('transaction_id') or '原設定已一致，無需備份'}\n"
                "本次未執行模型推論。")
            self.status.set("⑤ Hermes 已完成原生設定寫入、備份及讀回。")
        except (OSError, ValueError, RuntimeError, KeyError,
                subprocess.TimeoutExpired) as exc:
            messagebox.showerror("M4 正式套用未完成", str(exc)[:650])

    def _restore_hermes(self):
        """Undo only the newest hash-matching verified M4 transaction."""
        if not self._m4_official_profile_only():
            return
        try:
            data = self._m4_command("--latest-rollback-info")
            latest = data.get("candidate")
            if latest is None:
                messagebox.showinfo("沒有可還原的設定", "找不到已驗證的 M4 設定備份交易。")
                return
            if not latest["current_matches"]:
                messagebox.showwarning(
                    "目前設定已變更",
                    "Hermes 現行設定與最新備份交易不一致，為避免覆寫後續修改，拒絕自動還原。")
                return
            prior = latest.get("primary_before")
            current = latest.get("primary_after")
            if not prior:
                prior = list(load_hermes_selection(self.home)["primary"])
            question = (
                "要將 Hermes 還原成最新一次套用前的設定？\n\n"
                f"交易：{latest['transaction_id']}\n"
                f"主模型：{' / '.join(current or [])} → {' / '.join(prior)}\n"
                f"Fallback：{len(latest['fallback_after'])}"
                f" → {len(latest['fallback_before'])} 筆\n\n"
                "會先核對原始備份及目前設定 SHA-256，"
                "還原成功後同步 Manager 草稿。"
                "\n不會測試模型或啟動排程。")
            if not messagebox.askyesno("確認還原上次 Hermes 設定", question):
                return
            result = self._m4_command(
                "--rollback-confirmed", latest["transaction_id"])
            if result.get("status") != "PRODUCTION_ROLLBACK_VERIFIED":
                raise RuntimeError("還原沒有回報成功")
            self._refresh_after_m4()
            if result.get("manager_reconcile", {}).get("status") == "NEEDS_MANUAL_RECONCILIATION":
                messagebox.showwarning(
                    "Hermes 已還原，Manager 草稿待同步",
                    "Hermes 原始設定已成功還原，但 Manager 草稿尚未同步。"
                    "\n請重新載入 Manager 核對主模型與 Fallback。")
                return
            messagebox.showinfo(
                "設定已還原",
                f"原始備份已恢復並核對 SHA-256。\n"
                f"主模型：{self.baseline['primary'][0]} / {self.baseline['primary'][1]}"
                f"\nFallback：{len(self.baseline['fallback'])} 筆\n"
                "這項操作沒有發送模型請求。")
            self.status.set("Hermes 設定已依最新備份還原，Manager 已重新載入。")
        except (OSError, ValueError, RuntimeError, KeyError,
                subprocess.TimeoutExpired) as exc:
            messagebox.showerror("M4 還原未完成", str(exc)[:650])

    def _model_selection_changed(self, _event=None):
        self._detail()
        self._update_batch_selection()

    def _update_batch_selection(self):
        count = len(self.tree.selection())
        self.batch_selection_text.set(
            f"已選 {count}/{MAX_BATCH}｜Ctrl／Shift 多選")
        if not self.tool_batch_running:
            self.status.set(
                f"模型已選 {count} 個；可點選「選取模型批次 Tool Calling…」先做預檢。"
                if count else
                "尚未選取模型；在上方清單點選 1～6 個，之後再按批次 Tool Calling。")

    def _review_recommendations(self):
        open_recommendation_review(self)

    def _confirm_batch_tool_probe(self):
        self.batch_controller.open()

    def _confirm_refresh(self):
        if self.refreshing:
            return
        if self.source.resolve() != DEFAULT_SOURCE.resolve():
            messagebox.showinfo("自訂快照", "使用自訂 --source 時不覆寫該檔。請改以預設研究報告啟動再更新。")
            return
        if not messagebox.askyesno(
            "確認更新型錄", "將呼叫既有 M2 的官方 Provider metadata GET（可能數十秒）。"
            "\n不發送任何模型推論，不測試 Tool Calling，不套用 Hermes 設定。繼續？"
        ):
            return
        self.refreshing = True
        self.refresh_button.configure(state="disabled")
        self.status.set("正在查詢官方型錄與免費政策（GET-only）…")
        threading.Thread(target=self._refresh_worker, daemon=True).start()
        self.root.after(150, self._poll_refresh)

    def _refresh_worker(self):
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["HERMES_HOME"] = str(self.home)
        try:
            # Keep the immediately previous read-only snapshot; no credentials.
            if self.source.is_file():
                from shutil import copy2
                copy2(self.source, PROJECT / "reports" / "m2_p3_eligibility_before_manual_refresh.json")
            proc = subprocess.run(
                [sys.executable, "-B", str(PROJECT / "src" / "main.py"),
                 "eligibility", "--hermes-home", str(self.home)],
                cwd=str(PROJECT), env=env, capture_output=True, text=True,
                timeout=150, check=False)
            self.refresh_queue.put((proc.returncode == 0,
                                    "更新完成" if proc.returncode == 0 else "GET 更新失敗，保留舊快照"))
        except (OSError, subprocess.TimeoutExpired):
            self.refresh_queue.put((False, "更新逾時或無法啟動，保留舊快照"))

    def _poll_refresh(self):
        try:
            success, status = self.refresh_queue.get_nowait()
        except queue.Empty:
            self.root.after(150, self._poll_refresh)
            return
        self.refreshing = False
        self.refresh_button.configure(state="normal")
        if success:
            self._load()
            try:
                previous = json.loads(self.draft.read_text(encoding="utf-8"))
                latest = self.data.get("source_snapshot_at_utc")
                if previous.get("catalog_snapshot_utc") != latest:
                    self.draft_indicator.set("型錄更新：若要套用新選擇，請按③重新儲存")
                    self.status.set(
                        status + "；模型清單已更新，舊草稿保留。"
                        "若要套用新選擇，請重新儲存草稿；未發送推論。")
                    return
            except (OSError, ValueError, TypeError, KeyError):
                pass
        self.status.set(status + "；沒有向模型發送推論。")


def main() -> int:
    parser = argparse.ArgumentParser(description="Hermes Free Model Manager standalone UI")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--hermes-home", type=Path, default=default_hermes_home())
    parser.add_argument("--draft", type=Path, default=DEFAULT_DRAFT)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    root = tk.Tk()
    if args.self_test:
        root.withdraw()
    try:
        app = ManagerWindow(root, args.source, args.hermes_home, args.draft)
        root.update_idletasks()
        if args.self_test:
            if not app.rows:
                print("MANAGER_EMPTY_CATALOG_OK", flush=True)
                return 0
            app.tree.selection_set("0")
            app._detail()
            details = app.detail.get("1.0", "end")
            if "Provider：" not in details or "\n" not in details:
                return 3
            if hasattr(app, "_suggest") or hasattr(app, "include_conditional"):
                return 4  # The deterministic recommendation algorithm must be gone.
            print("MANAGER_AI_UI_SMOKE_OK", len(app.rows), "models", flush=True)
            return 0
        root.mainloop()
        return 0
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
