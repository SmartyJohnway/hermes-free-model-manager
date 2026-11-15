"""Visible review of ChatGPT-selected fallback verification, no automatic POST."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox

from tool_evidence_policy import build_validation_plan
from tool_batch_probe import MAX_BATCH


def open_recommendation_review(manager):
    """Review current user/AI draft; send only explicit selection to M5 preflight."""
    if manager.tool_batch_running:
        messagebox.showinfo("測試進行中", "請先完成目前批次測試。", parent=manager.root)
        return
    if not manager.fallback:
        messagebox.showinfo(
            "沒有推薦候選",
            "請先載入 ChatGPT 建議，或在 Manager 草稿中加入 Fallback。",
            parent=manager.root)
        return
    # Always re-read persisted reports to reflect tests finished since last dialog.
    manager.refresh_manual_results()
    plan = build_validation_plan(manager.rows, manager.fallback)
    top = tk.Toplevel(manager.root)
    top.title("ChatGPT 推薦候選｜Tool Calling 驗證")
    width = min(1020, max(680, top.winfo_screenwidth() - 100))
    height = min(700, max(430, top.winfo_screenheight() - 100))
    top.geometry(f"{width}x{height}")
    top.minsize(640, 390)
    top.transient(manager.root)
    frame = ttk.Frame(top, padding=12)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="推薦候選驗證｜7 天提醒、30 天過期",
              font=("Microsoft JhengHei UI", 13, "bold")).pack(anchor="w")
    ttk.Label(
        frame, text=(
            f"目前草稿 {len(plan['entries'])} 個候選；"
            f"建議驗證 {plan['suggested_count']} 個，"
            f"需先診斷 {plan['diagnostic_count']} 個。"
            " 未測新模型仍可由 ChatGPT 推薦；PASS 到期不代表失效。"),
        wraplength=900).pack(anchor="w", pady=(4, 6))
    ttk.Label(
        frame, text="下表按 Ctrl／Shift 可多選；一次最多測試 6 個。"
                    "預設只選建議待測項目，且不會自動執行推論。",
        foreground="#596575").pack(anchor="w", pady=(0, 8))
    body = ttk.Frame(frame)
    body.pack(fill="both", expand=True)
    cols = ("provider", "model", "status", "age")
    tree = ttk.Treeview(body, columns=cols, show="headings",
                        selectmode="extended")
    for col, title, width in (
        ("provider", "Provider", 120), ("model", "Model", 380),
        ("status", "驗證狀態", 200), ("age", "PASS 已過天數", 110)
    ):
        tree.heading(col, text=title)
        tree.column(col, width=width, stretch=col == "model")
    scroll = ttk.Scrollbar(body, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scroll.set)
    tree.pack(side="left", fill="both", expand=True)
    scroll.pack(side="right", fill="y")
    pairs = {}
    suggested = []
    for i, entry in enumerate(plan["entries"]):
        iid = str(i)
        pair = (entry["provider"], entry["model"])
        pairs[iid] = pair
        tree.insert("", "end", iid=iid, values=(
            pair[0], pair[1], entry["status"],
            entry["pass_age_days"] if entry["pass_age_days"] is not None else "—"))
        if entry["suggest_test"] and len(suggested) < MAX_BATCH:
            suggested.append(iid)
    if suggested:
        tree.selection_set(*suggested)
        tree.see(suggested[0])
    status = tk.StringVar()
    ttk.Label(frame, textvariable=status, foreground="#56616d").pack(
        anchor="w", pady=(6, 4))
    buttons = ttk.Frame(frame)
    buttons.pack(side="bottom", fill="x", pady=(4, 0))

    def update(*_):
        chosen = tree.selection()
        status.set(
            f"已選 {len(chosen)} 個／每批最多 {MAX_BATCH} 個；"
            "按『進入測試預檢』後仍需再次確認才會 POST。"
        )
        start.configure(state="normal" if 0 < len(chosen) <= MAX_BATCH
                        else "disabled")

    def go():
        chosen = tree.selection()
        if not 0 < len(chosen) <= MAX_BATCH:
            return
        selected = [pairs[i] for i in chosen]
        top.destroy()
        manager.batch_controller.open(selected_pairs=selected)

    start = ttk.Button(buttons, text="進入測試預檢（不立即發送）", command=go)
    start.pack(side="left", padx=(0, 12))
    ttk.Button(buttons, text="關閉（不測試）", command=top.destroy).pack(
        side="right")
    tree.bind("<<TreeviewSelect>>", update)
    update()
    top.lift()
    return top
