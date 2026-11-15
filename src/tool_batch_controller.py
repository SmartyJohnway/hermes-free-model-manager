"""Manager M5 GUI controller: visible preflight, per-model progress, safe stop.

Does not run real POST before the operator explicitly presses Start and confirms.
A stop request applies before the next request; cannot cancel an in-flight POST.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import queue
import secrets
import subprocess
import sys
import threading
from tkinter import messagebox

from manager_ai_exchange import read_catalog
from tool_batch_probe import make_plan, MAX_BATCH, REPORTS
from tool_batch_dialog import BatchToolDialog

PROJECT = Path(__file__).resolve().parents[1]


class BatchTestController:
    def __init__(self, manager):
        self.manager = manager
        self.dialog = None
        self.selection = None
        self.plan = None
        self.running = False
        self.queue: queue.Queue[tuple[bool, dict]] = queue.Queue()
        self.run_tag: str | None = None
        self.progress_path: Path | None = None
        self.cancel_path: Path | None = None
        self.progress_digest = None
        self._after_id = None

    def open(self, selected_pairs=None):
        gui = self.manager
        if self.running and self.dialog and self.dialog.top.winfo_exists():
            self.dialog.present()
            gui.status.set("批次工具測試正在執行，已開啟即時進度視窗。")
            return
        if self.dialog and self.dialog.top.winfo_exists():
            self.dialog.top.destroy()
        self.dialog = BatchToolDialog(
            gui.root, on_start=self.start, on_stop=self.stop_after_current,
            on_risk_change=self._on_risk_change)
        self.plan = None
        self.selection = None
        if selected_pairs is None:
            selected = [gui.by_item[x] for x in gui.tree.selection()
                        if x in gui.by_item]
        else:
            index = {(r["provider"], r["model"]): r for r in gui.rows}
            selected = [index[pair] for pair in selected_pairs if pair in index]
            if len(selected) != len(selected_pairs):
                self.dialog.show_instruction(
                    "部分推薦模型已不在目前型錄，請重新 GET 更新並檢查。")
                return
        count = len(selected)
        if count < 1 or count > MAX_BATCH:
            self.dialog.show_instruction(
                f"目前已選取 {count} 個模型。\n\n"
                f"請先在 Manager 上方模型清單用 Ctrl／Shift 選取 1～{MAX_BATCH} 個。"
                "\n接著再次按「選取模型批次 Tool Calling…」。"
                "\n\n目前沒有發出任何模型請求。")
            gui.status.set(
                f"已收到批次測試按鈕；已選 {count} 個，需要 1～{MAX_BATCH} 個，尚未開始。")
            return
        try:
            catalog, digest = read_catalog(gui.source)
            self.selection = {
                "schema": "hfm-m5-tool-batch.v1",
                "catalog_sha256": digest,
                "models": [
                    {"provider": r["provider"], "model": r["model"]}
                    for r in selected
                ],
            }
            self.plan = make_plan(gui.source, gui.home, self.selection)
            self.dialog.show_plan(self.plan)
            if self.plan["blocked_count"]:
                gui.status.set(
                    f"批次預檢完成：可測 {self.plan['eligible_count']} 項，"
                    f"自動略過 {self.plan['blocked_count']} 項；尚未發送請求。")
            else:
                gui.status.set(
                    f"批次預檢完成：{count} 項可手動測試；尚未發送請求。"
                    "可在獨立視窗確認開始。")
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.dialog.show_instruction(
                "預檢未通過："
                + type(exc).__name__ + "\n"
                + str(exc)[:220] + "\n尚未發出任何請求。")
            gui.status.set("批次預檢無法完成，未發出模型請求。")

    def _on_risk_change(self, accepted: bool):
        if self.running or not self.selection:
            return
        try:
            self.plan = make_plan(
                self.manager.source, self.manager.home, self.selection,
                allow_agnes_cost_risk=accepted)
            self.dialog.show_plan(self.plan)
            self.manager.status.set(
                "AgnesAI 費用風險確認已" + ("勾選" if accepted else "取消")
                + f"；可測 {self.plan['eligible_count']} 個、"
                f"仍受限 {self.plan['blocked_count']} 個。"
                "尚未發送任何請求。")
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.plan = None
            self.dialog.show_instruction(
                "AgnesAI 費用資格重新核查失敗：" + str(exc)[:180])
            self.manager.status.set("費用風險核查失敗，沒有發送請求。")

    def start(self):
        if self.running or not self.plan or not self.selection:
            return
        allow_agnes = bool(self.dialog.agnes_cost_ack.get())
        try:
            self.plan = make_plan(
                self.manager.source, self.manager.home, self.selection,
                allow_agnes_cost_risk=allow_agnes)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.dialog.show_instruction("開始前再次預檢失敗：" + str(exc)[:220])
            return
        runnable = [row for row in self.plan["entries"] if row["eligible"]]
        if not runnable:
            self.dialog.show_plan(self.plan)
            return
        skipped = [row for row in self.plan["entries"] if not row["eligible"]]
        count = len(runnable)
        if not messagebox.askyesno(
            "確認開始 Tool Calling 測試",
            f"你選取了 {self.plan['selected_count']} 個模型；"
            f"將測試 {count} 個，略過 {len(skipped)} 個。\n\n"
            + ("略過：" + ", ".join(
                f"{r['provider']}/{r['model']}" for r in skipped) + "\n\n"
               if skipped else "")
            + f"本批最多發送 {count} 次真實合成請求。"
            "\n可能消耗 Provider 免費額度；不保證所有路由無費。"
            "\n\n正式開始？",
            parent=self.dialog.top,
        ):
            self.dialog.stage.set("已取消開始｜沒有發送模型請求")
            self.manager.status.set("已取消批次測試；沒有發送請求。")
            return
        tag = (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
               + "-" + secrets.token_hex(6))
        self.run_tag = tag
        stem = "M5_TOOL_BATCH_" + tag
        self.progress_path = REPORTS / (stem + "_INPROGRESS.json")
        self.cancel_path = REPORTS / (stem + "_CANCEL")
        self.progress_digest = None
        selected_path = PROJECT / "state" / "m5_tool_batch_selection.json"
        selected_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            approved = dict(self.selection)
            approved["models"] = [
                {"provider": row["provider"], "model": row["model"]}
                for row in runnable
            ]
            if allow_agnes and any(row["provider"] == "agnesai" for row in runnable):
                approved["agnes_fee_risk_ack"] = True
            selected_path.write_text(
                json.dumps(approved, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError:
            self.dialog.stage.set("開始失敗：選取資料無法儲存")
            self.manager.status.set("批次測試未開始，無法儲存選取資料。")
            return
        self.running = True
        self.manager.tool_batch_running = True
        self.dialog.begin(count)
        self.manager.batch_button.configure(text="查看測試進度…")
        self.manager.status.set(
            f"Tool Calling 已開始：0/{count}，"
            f"略過 {len(skipped)} 項；按「查看測試進度…」可開啟即時視窗。")
        threading.Thread(
            target=self._worker,
            args=(tag, self.plan["catalog_sha256"], selected_path,
                  allow_agnes and any(r["provider"] == "agnesai" for r in runnable)),
            daemon=True,
        ).start()
        self._after_id = self.manager.root.after(180, self._poll)

    def _worker(self, tag: str, catalog_sha: str, selected_path: Path,
                allow_agnes: bool = False):
        args = [
            sys.executable, "-B", str(PROJECT / "scripts" / "tool_calling_batch.py"),
            "run", "--confirm-live", "--run-tag", tag,
            "--selection", str(selected_path),
            "--confirm-catalog-sha", catalog_sha,
        ]
        if allow_agnes:
            args.append("--ack-agnes-fee-risk")
        try:
            result = subprocess.run(
                args, cwd=str(PROJECT),
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                text=True, encoding="utf-8", errors="replace",
                capture_output=True, timeout=170, check=False,
            )
            if result.returncode:
                self.queue.put((False, {
                    "status": "run_rejected_or_failed",
                    "detail": "CLI exit " + str(result.returncode),
                }))
                return
            info = json.loads(result.stdout)
            self.queue.put((True, info))
        except (OSError, ValueError, subprocess.TimeoutExpired, KeyError) as exc:
            self.queue.put((False, {
                "status": "interrupted",
                "detail": type(exc).__name__,
            }))

    def stop_after_current(self):
        if not self.running or self.cancel_path is None:
            return
        self.cancel_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.cancel_path.open("x", encoding="utf-8") as stream:
                stream.write("Stop all future requests after in-flight request\n")
        except FileExistsError:
            pass
        except OSError:
            self.dialog.stage.set("無法設定停止旗標；請留意測試進度")
            return
        self.dialog.stop_queued()
        self.manager.status.set(
            "已要求停止後續模型；目前已發出的請求仍可能完成。")

    def _progress(self):
        if not self.progress_path or not self.progress_path.is_file():
            return
        try:
            raw = self.progress_path.read_bytes()
            digest = sha256(raw).hexdigest()
            if digest == self.progress_digest:
                return
            obj = json.loads(raw.decode("utf-8"))
            if obj.get("schema") != "hfm-m5-tool-batch-progress.v1":
                return
            self.progress_digest = digest
            self.dialog.show_progress(obj)
            total = self.plan.get("eligible_count", self.plan.get("selected_count", 0)) if self.plan else 0
            completed = len(obj.get("results", []))
            active = obj.get("reserved_model") or {}
            model = (active.get("provider", "") + " / " + active.get("model", "")
                     if obj.get("state") == "REQUEST_RESERVED_BEFORE_POST" else "")
            self.manager.status.set(
                f"Tool Calling 進行中：{completed}/{total}"
                + (f"；目前 {model}" if model else "")
                + "。按「查看測試進度…」檢視。")
        except (OSError, ValueError, TypeError, KeyError):
            return

    def _poll(self):
        # A manual test or repeated UI callback may finish before the pending
        # Tk timer fires. Cancel that timer to avoid a late call on a dead window.
        if self._after_id is not None:
            try:
                self.manager.root.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        self._progress()
        try:
            ok, info = self.queue.get_nowait()
        except queue.Empty:
            if self.running:
                self._after_id = self.manager.root.after(180, self._poll)
            return
        self._progress()
        total = self.plan.get("eligible_count", self.plan.get("selected_count", 0)) if self.plan else 0
        count = int(info.get("attempted_count") or 0)
        report = info.get("report_path")
        stopped = ok and info.get("stop_reason") == "user_stopped_before_next_request"
        message = (
            f"已完成 {count}/{total} 個；{info.get('stop_reason','完成')}。"
            if ok else
            "測試未完成（" + str(info.get("status", "錯誤")) + "）。"
            "若網路中斷，可能已消耗一次免費額度；請查看 INPROGRESS 記錄。"
        )
        self.running = False
        self.manager.tool_batch_running = False
        self.manager.batch_button.configure(text="選取模型批次 Tool Calling…")
        if self.dialog and self.dialog.top.winfo_exists():
            if ok:
                self.dialog.show_final_results(info.get("results") or [])
            self.dialog.finish(ok, message, completed=count, total=total,
                               report_path=str(report) if report else None,
                               stopped=stopped)
            self.dialog.present()
        # Completed report is the only source eligible for permanent backfill.
        # An interrupted or partially written journal never becomes a FAIL.
        backfilled = 0
        if ok and report:
            backfilled = self.manager.refresh_manual_results(report_path=str(report))
        self.manager.status.set(
            "Tool Calling " + ("已停止" if stopped else ("完成" if ok else "中斷"))
            + f"：{count}/{total}。"
            + (f" Manager 已回填 {backfilled} 筆。" if ok else "")
            + (" 報告：" + str(report) if report else ""))
