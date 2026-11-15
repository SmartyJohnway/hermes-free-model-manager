"""Read-only M5 Tool Calling result projection for the Manager.

Completed M5 batch reports are the immutable source of truth. This module
does not re-write the M2 candidate catalog, issue any request, or modify Hermes.
A newer failure never erases evidence of an older successful tool call.
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re

from manager_ui_model import local_time

REPORT_NAME = re.compile(
    r"^M5_TOOL_BATCH_\d{8}T\d{6}Z-[0-9a-f]{12}\.json$")
MAX_REPORT_SIZE = 1_000_000
MAX_FILES = 2000
MAX_ROWS_PER_REPORT = 30

# Labels describe ONE request only. A 400/429 is not proof the model never works.
def result_label(record: dict) -> str:
    http = record.get("http_status")
    outcome = record.get("result")
    if http == 200 and record.get("tool_call_confirmed") is True and outcome == "pass":
        return "PASS"
    if http == 429:
        return "限流 429"
    if http == 402:
        return "額度/計費 402"
    if http in (401, 403):
        return "認證/權限"
    if http is None:
        return "連線/逾時"
    if http == 400:
        return "請求不相容 400"
    if http == 200 and outcome == "fail":
        return "單次未通過"
    if http == 200:
        return "未確認"
    if isinstance(http, int) and http >= 500:
        return "服務端異常"
    if isinstance(http, int) and 400 <= http < 500:
        return f"HTTP {http}"
    if outcome == "fail":
        return "單次未通過"
    return "未確認"


def _time_key(utc: object) -> datetime | None:
    if not isinstance(utc, str) or len(utc) > 45:
        return None
    try:
        value = datetime.fromisoformat(utc.replace("Z", "+00:00"))
        return value if value.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def _clean_row(row: object, report: Path, batch_utc: str,
               catalog_sha256: str) -> dict | None:
    if not isinstance(row, dict):
        return None
    provider, model = row.get("provider"), row.get("model")
    if not (isinstance(provider, str) and provider and len(provider) <= 100
            and isinstance(model, str) and model and len(model) <= 256
            and row.get("request_attempted") is True):
        return None
    at = row.get("attempted_at_utc")
    if _time_key(at) is None:
        return None
    http = row.get("http_status")
    if http is not None and (type(http) is not int or not 100 <= http <= 599):
        return None
    outcome = row.get("result")
    if not isinstance(outcome, str) or len(outcome) > 100:
        return None
    reason = row.get("reason")
    if not isinstance(reason, str) or len(reason) > 150:
        reason = "未提供"
    confirmed = (http == 200 and outcome == "pass"
                 and row.get("tool_call_confirmed") is True)
    usage = row.get("usage") or {}
    if not isinstance(usage, dict):
        usage = {}
    return {
        "provider": provider, "model": model,
        "attempted_at_utc": at, "batch_utc": batch_utc,
        "http_status": http, "result": outcome, "reason": reason,
        "tool_call_confirmed": confirmed,
        "label": result_label(row),
        "usage": {
            k: v for k in ("prompt_tokens", "completion_tokens", "total_tokens")
            if isinstance((v := usage.get(k)), int) and not isinstance(v, bool)
            and 0 <= v <= 10_000_000
        },
        "catalog_sha256": catalog_sha256,
        "report_path": str(report.resolve()),
        "report_name": report.name,
    }


def load_manual_results(folder: Path) -> dict:
    """Latest completed per Provider/Model, plus latest positive historical proof.

    In-progress files or partial/invalid reports do not overwrite earlier
    confirmed results. Lookup is based on each attempted_at_utc, not file mtime.
    """
    latest: dict[tuple[str, str], dict] = {}
    positives: dict[tuple[str, str], dict] = {}
    valid_files = 0
    ignored_files = 0
    incomplete = 0
    if not folder.is_dir():
        return {"latest": latest, "last_pass": positives,
                "valid_files": 0, "ignored_files": 0, "incomplete_files": 0}
    # Report files are small and only appear after a completed batch write.
    try:
        files = sorted(folder.iterdir(), key=lambda p: p.name)
    except OSError:
        files = []
    incomplete = sum(p.is_file() and p.name.endswith("_INPROGRESS.json")
                     for p in files)
    reports = [p for p in files if p.is_file()
               and REPORT_NAME.fullmatch(p.name)]
    if len(reports) > MAX_FILES:
        ignored_files += len(reports) - MAX_FILES
        reports = reports[-MAX_FILES:]
    for path in reports:
        try:
            if path.is_symlink() or path.stat().st_size > MAX_REPORT_SIZE:
                ignored_files += 1
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                ignored_files += 1
                continue
            rows = data.get("results")
            if (not isinstance(data, dict)
                    or data.get("schema") != "hfm-m5-tool-batch-result.v1"
                    or data.get("no_hermes_config_writes") is not True
                    or not isinstance(rows, list)
                    or len(rows) > MAX_ROWS_PER_REPORT
                    or data.get("attempted_count") != len(rows)
                    or _time_key(data.get("utc")) is None):
                ignored_files += 1
                continue
            clean = [
                _clean_row(r, path, data["utc"], data.get("catalog_sha256", ""))
                for r in rows
            ]
            if any(r is None for r in clean):
                ignored_files += 1
                continue
            valid_files += 1
            for row in clean:
                pair = row["provider"], row["model"]
                reference = latest.get(pair)
                key = (_time_key(row["attempted_at_utc"]), row["report_name"])
                if not reference or key > (_time_key(reference["attempted_at_utc"]),
                                           reference["report_name"]):
                    latest[pair] = row
                if row["tool_call_confirmed"]:
                    reference = positives.get(pair)
                    if not reference or key > (
                            _time_key(reference["attempted_at_utc"]),
                            reference["report_name"]):
                        positives[pair] = row
        except (OSError, ValueError, TypeError, KeyError):
            ignored_files += 1
    return {"latest": latest, "last_pass": positives,
            "valid_files": valid_files, "ignored_files": ignored_files,
            "incomplete_files": incomplete}


def tool_evidence_description(row: dict) -> str:
    """Full detail-panel description without conflating failure with unsupported."""
    record = row.get("manual_tool_result")
    historic = row.get("manual_last_pass")
    original = row.get("tool_calling") == "tested_pass"
    if not record:
        return ("歷史合成測試 PASS（舊 M2 證據）" if original else
                "未測／無正向證據")
    http = record["http_status"]
    label = record["label"]
    summary = (
        f"最近一次手動測試：{label}\n"
        f"測試時間（台灣）：{local_time(record['attempted_at_utc'])}\n"
        f"HTTP：{http if http is not None else '無回應'}\n"
        f"回應分類：{record['result']} / {record['reason']}\n"
        f"Tool Call 驗證：{'PASS' if record['tool_call_confirmed'] else '未確認'}\n"
        f"原始報告：{record['report_path']}"
    )
    if record["usage"]:
        summary += "\nTokens：" + ", ".join(
            f"{k}={v}" for k, v in record["usage"].items())
    if historic and historic["report_name"] != record["report_name"]:
        summary += (
            "\n歷史正向證據："
            + local_time(historic["attempted_at_utc"])
            + " 曾通過合成 Tool Calling"
        )
    elif original and not record["tool_call_confirmed"]:
        summary += "\n另有較早 M2 合成 Tool Calling PASS 紀錄"
    if not record["tool_call_confirmed"]:
        summary += "\n提醒：單次非 PASS 不等於模型永久不支援 Tool Calling。"
    return summary
