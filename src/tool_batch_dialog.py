"""Visible, inspectable M5 manual Tool Calling preview and live progress.

Pure Tk UI, no network, credentials or Hermes changes. Only explicit Start calls
the owner-provided callback. Dialog remains visible through preflight/run/report.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class BatchToolDialog:
    def __init__(self, owner: tk.Misc, *, on_start, on_stop,
                 on_risk_change=None):
        self.on_start = on_start
        self.on_stop = on_stop
        self.on_risk_change = on_risk_change
        self.running = False
        self.finished = False
        self.report_path: str | None = None
        self.top = tk.Toplevel(owner)
        self.top.title("Hermes Manager｜手動 Tool Calling 測試")
        # Keep footer on-screen even at Windows display scaling / smaller monitors.
        sw, sh = self.top.winfo_screenwidth(), self.top.winfo_screenheight()
        width = min(900, max(640, sw - 90))
        height = min(700, max(420, sh - 170))
        x, y = max(0, (sw - width) // 2), max(0, (sh - height) // 3)
        self.top.geometry(f"{width}x{height}+{x}+{y}")
        self.top.minsize(min(680, width), min(430, height))
        self.top.transient(owner)
        self.top.protocol("WM_DELETE_WINDOW", self._request_close)
        self.top.grid_rowconfigure(0, weight=1)
        self.top.grid_columnconfigure(0, weight=1)

        outer = ttk.Frame(self.top, padding=(12, 9, 12, 9))
        outer.grid(row=0, column=0, sticky="nsew")
        outer.grid_columnconfigure(0, weight=1)
        outer.grid_rowconfigure(5, weight=1, minsize=90)
        ttk.Label(outer, text="手動 Tool Calling 測試",
                  font=("Microsoft JhengHei UI", 13, "bold")).grid(
                      row=0, column=0, sticky="w")
        self.stage = tk.StringVar(value="等待選取模型")
        ttk.Label(outer, textvariable=self.stage,
                  font=("Microsoft JhengHei UI", 10, "bold")).grid(
                      row=1, column=0, sticky="w", pady=(5, 3))
        self.detail = tk.StringVar(value="先完成預檢，未確認前不發送任何模型請求。")
        ttk.Label(outer, textvariable=self.detail,
                  wraplength=max(440, width - 64)).grid(
                      row=2, column=0, sticky="ew", pady=(0, 4))
        self.bar = ttk.Progressbar(outer, mode="determinate", maximum=1, value=0)
        self.bar.grid(row=3, column=0, sticky="ew", pady=(0, 5))

        self.agnes_cost_ack = tk.BooleanVar(value=False)
        self.risk_frame = ttk.Frame(outer)
        self.risk_frame.grid(row=4, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(
            self.risk_frame,
            text="AgnesAI 免費帳戶不代表各型號免費；Pro 可能按量計費。預設不發送。",
            foreground="#a52a2a",
        ).pack(anchor="w")
        self.risk_check = ttk.Checkbutton(
            self.risk_frame,
            text="我接受 AgnesAI 可能收費（每個模型最多 1 次）",
            variable=self.agnes_cost_ack,
            command=self._risk_changed,
        )
        self.risk_check.pack(anchor="w", pady=(2, 0))
        self.risk_frame.grid_remove()

        panel = ttk.LabelFrame(outer, text="模型及測試進度（可捲動）", padding=6)
        panel.grid(row=5, column=0, sticky="nsew")
        panel.grid_rowconfigure(0, weight=1)
        panel.grid_columnconfigure(0, weight=1)
        self.log = tk.Text(
            panel, wrap="word", font=("Microsoft JhengHei UI", 10),
            state="disabled", padx=6, pady=6, height=4, width=32,
        )
        scroller = ttk.Scrollbar(panel, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=scroller.set)
        self.log.grid(row=0, column=0, sticky="nsew")
        scroller.grid(row=0, column=1, sticky="ns")

        footer = ttk.Frame(outer)
        footer.grid(row=6, column=0, sticky="ew", pady=(8, 0))
        self.start_button = ttk.Button(
            footer, text="開始測試（需再次確認）",
            command=self.on_start, state="disabled")
        self.start_button.pack(side="left", padx=(0, 6))
        self.stop_button = ttk.Button(
            footer, text="停止後續測試", command=self.on_stop, state="disabled")
        self.stop_button.pack(side="left", padx=(0, 6))
        self.report_button = ttk.Button(
            footer, text="複製報告路徑", state="disabled",
            command=self._copy_report_path)
        self.report_button.pack(side="left", padx=(0, 6))
        self.close_button = ttk.Button(
            footer, text="關閉（不執行）", command=self._request_close)
        self.close_button.pack(side="right")
        self.top.lift()
        self.top.focus_set()

    def _risk_changed(self):
        if self.on_risk_change is not None:
            self.on_risk_change(self.agnes_cost_ack.get())

    def _append(self, value: str, *, replace: bool = False):
        self.log.configure(state="normal")
        if replace:
            self.log.delete("1.0", "end")
        self.log.insert("end", value + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def show_instruction(self, text: str):
        self.stage.set("尚未開始：請先選取模型")
        self.detail.set("點擊按鈕已收到，尚未執行任何模型請求。")
        self._append(text, replace=True)

    def show_plan(self, plan: dict):
        self.finished = False
        count = plan["selected_count"]
        eligible = plan["eligible_count"]
        blocked = plan["blocked_count"]
        self.bar.configure(maximum=max(count, 1), value=0)
        self.stage.set(f"預檢完成｜已選 {count} 個，可執行 {eligible} 個，自動略過 {blocked} 個")
        risks = plan.get("fee_risk_count", 0)
        self.detail.set(
            f"本次尚未開始。僅會測試 {eligible} 個合格模型，略過 {blocked} 個不合格模型。"
            + ("若要測試費率未明的 AgnesAI，必須另外勾選承擔費用風險。"
               if risks else "按開始測試前不會產生模型請求。")
        )
        if risks:
            self.risk_frame.grid()
        else:
            self.risk_frame.grid_remove()
            self.agnes_cost_ack.set(False)
        lines = [
            f"已選 {count} 個模型；每個最多 1 次合成請求；本批最多 {plan['maximum_remote_requests']} 次。",
            "僅對可測模型送出請求；跳過的模型不會發送。免費額度可能被消耗。",
            "",
        ]
        for i, row in enumerate(plan["entries"], 1):
            verdict = (
                "風險確認後可測（可能收費）"
                if row.get("fee_risk") and row["eligible"]
                else "可測（可能耗用免費額度）" if row["eligible"]
                else "需明確確認費用風險" if row.get("risk_override_available")
                else "略過（不發送請求）"
            )
            lines.append(
                f"{i}. {row['provider']} / {row['model']}\n"
                f"    {row['free_evidence']}｜{verdict}｜{row['reason']}"
            )
        lines += [
            "",
            "遇付款、認證、限流或網路失敗會停止，絕不自動重試。",
            "若不想消耗任何額度，直接按「關閉」。",
        ]
        self._append("\n".join(lines), replace=True)
        self.start_button.configure(state=(
            "normal" if eligible > 0 else "disabled"))
        self.start_button.configure(
            text=f"開始測試 {eligible} 個（略過 {blocked} 個）" if eligible
            else "沒有可測模型")
        self.stop_button.configure(state="disabled")
        self.close_button.configure(text="關閉（不執行）")

    def begin(self, count: int):
        self.running = True
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.bar.configure(maximum=max(1, count), value=0)
        self.stage.set(f"正在測試｜0 / {count} 個完成")
        self.detail.set("已開始執行。下面將逐項顯示送出狀態與回傳結果。")
        self._append("\n---- 已確認開始：進行中 ----")

    def show_progress(self, state: dict):
        if not self.running:
            return
        results = state.get("results") or []
        count = len(results)
        total = int(state.get("total_selected") or self.bar.cget("maximum"))
        current = state.get("reserved_model") or {}
        self.bar.configure(maximum=max(total, 1), value=count)
        if state.get("state") == "REQUEST_RESERVED_BEFORE_POST":
            self.stage.set(f"正在測試｜{count} / {total} 個完成")
            self.detail.set(
                "目前請求：" + str(current.get("provider", "?")) + " / "
                + str(current.get("model", "?"))
                + "（請求可能已計入額度）")
        elif state.get("state") == "REQUEST_RECORDED":
            self.stage.set(f"測試進度｜{count} / {total} 個完成")
            last = results[-1] if results else {}
            self.detail.set(
                f"上筆結果：{last.get('provider','?')} / {last.get('model','?')} "
                f"｜HTTP {last.get('http_status','?')}｜{last.get('result','?')}")
        lines = [f"{i}. {item['provider']} / {item['model']} "
                 f"｜HTTP {item.get('http_status','?')} "
                 f"｜{item.get('result','unknown')} "
                 f"｜Tool PASS={item.get('tool_call_confirmed',False)}"
                 for i, item in enumerate(results, 1)]
        self._append("\n".join(lines) if lines else "等待模型回應…", replace=False)

    def show_final_results(self, results: list[dict]):
        """Always show the final per-model table even if the file polling missed a fast response."""
        self._append("\n最終逐模型結果：")
        if not results:
            self._append("沒有完成可核對的模型請求。")
        for i, item in enumerate(results, 1):
            self._append(
                f"{i}. {item.get('provider','?')} / {item.get('model','?')}"
                f"｜HTTP {item.get('http_status','?')}"
                f"｜{item.get('result','unknown')}"
                f"｜Tool Calling {'PASS' if item.get('tool_call_confirmed') else '未確認'}"
            )

    def stop_queued(self):
        if not self.running:
            return
        self.stop_button.configure(state="disabled")
        self.stage.set("已要求停止後續測試")
        self.detail.set("已送出的請求不會強制中斷；目前請求完成後不再測下一個模型。")
        self._append("已要求停止。等待正在進行的請求結束…")

    def finish(self, ok: bool, message: str, *, completed: int = 0,
               total: int = 0, report_path: str | None = None,
               stopped: bool = False):
        self.running = False
        self.finished = True
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="disabled")
        self.bar.configure(maximum=max(total, 1), value=min(completed, max(total, 1)))
        title = "已依要求停止" if stopped else ("測試完成" if ok else "測試異常或中斷")
        self.stage.set(title + f"｜已嘗試 {completed} / {total}")
        self.detail.set(message)
        self._append("\n---- " + message)
        if report_path:
            self.report_path = report_path
            self.report_button.configure(state="normal")
            self._append("本機結果檔：" + report_path)
        self.close_button.configure(text="完成／關閉")

    def _copy_report_path(self):
        if not self.report_path:
            return
        self.top.clipboard_clear()
        self.top.clipboard_append(self.report_path)
        self.detail.set("已複製本機結果檔路徑，可貼到檔案總管或交給 ChatGPT。")

    def present(self):
        self.top.deiconify()
        self.top.lift()
        self.top.focus_set()

    def _request_close(self):
        if self.running:
            # Keep the run visible via the main Manager, allow dialog minimization.
            self.top.withdraw()
        else:
            self.top.destroy()
