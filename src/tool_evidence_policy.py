"""M5 recommendation validation: exploration-first, age-aware, NO API requests.

ChatGPT chooses models and order. This module only classifies the selected
recommendations; a historical PASS never excludes a newly listed model.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

WARNING_DAYS = 7
EXPIRE_DAYS = 30


def classify(row: dict, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    last_pass = row.get("manual_last_pass")
    last = row.get("manual_tool_result")
    historical = row.get("tool_calling") == "tested_pass"
    if last_pass:
        try:
            stamp = datetime.fromisoformat(
                last_pass["attempted_at_utc"].replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                raise ValueError("missing timezone")
            age = max(0, (now - stamp).total_seconds() / 86400)
        except (ValueError, TypeError, KeyError):
            age = None
        if age is not None and age <= WARNING_DAYS:
            status, suggested = "近期 PASS", False
        elif age is not None and age <= EXPIRE_DAYS:
            status, suggested = "PASS 已達提醒期", True
        else:
            status, suggested = "PASS 已過期／時間不明", True
        return {"status": status, "suggest_test": suggested,
                "pass_age_days": round(age, 1) if age is not None else None,
                "reason": "歷史 PASS 不會因時效到期而被判定失敗"}
    if historical:
        return {"status": "舊 M2 PASS／時間未核實", "suggest_test": True,
                "pass_age_days": None,
                "reason": "舊 PASS 時間未核實，建議補測但保留歷史證據"}
    if last:
        label = last.get("label", "未確認")
        # Diagnostic prerequisites: do not blindly repeat a known provider issue.
        if last.get("http_status") in (401, 402, 403, 404, 429):
            return {"status": "先診斷：" + label, "suggest_test": False,
                    "pass_age_days": None,
                    "reason": "先確認 Provider 認證、價格、路由或限流，再手動重測"}
        return {"status": "待驗證：" + label, "suggest_test": True,
                "pass_age_days": None, "reason": "尚無已確認的 Tool Calling PASS"}
    return {"status": "新候選／未測", "suggest_test": True,
            "pass_age_days": None, "reason": "未測不是不合格；保留新模型探索機會"}


def build_validation_plan(rows: Iterable[dict], selected: Iterable[tuple[str, str]],
                          *, now: datetime | None = None) -> dict:
    index = {(r["provider"], r["model"]): r for r in rows}
    result = []
    seen = set()
    for pair in selected:
        pair = tuple(pair)
        if pair in seen:
            continue
        seen.add(pair)
        row = index.get(pair)
        if row is None:
            result.append({"provider": pair[0], "model": pair[1],
                           "status": "不在目前型錄", "suggest_test": False,
                           "reason": "先更新 GET 型錄並確認路由",
                           "pass_age_days": None})
            continue
        result.append({"provider": pair[0], "model": pair[1],
                       **classify(row, now=now)})
    return {"policy": {"warn_after_days": WARNING_DAYS,
                       "expire_after_days": EXPIRE_DAYS,
                       "automatic_post": False,
                       "recommendation_requires_pass": False},
            "entries": result,
            "suggested_count": sum(x["suggest_test"] for x in result),
            "diagnostic_count": sum(x["status"].startswith("先診斷") for x in result)}
