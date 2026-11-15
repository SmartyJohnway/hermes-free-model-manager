"""Pure Manager UI state/preview helpers. No Hermes writes or inference."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile

import yaml

TAIPEI = timezone(timedelta(hours=8))
FREE_LABELS = {
    "published_zero_price": "官方零價格",
    "published_free_list": "官方免費名單",
    "free_plan_candidate": "免費方案候選",
    "free_allowance_candidate": "免費額度候選",
    "prototype_free_only": "開發試用免費",
}
STRONG_FREE = {"published_zero_price", "published_free_list"}
CONDITIONAL_FREE = {"free_plan_candidate", "free_allowance_candidate",
                    "prototype_free_only"}
ACCOUNT_LABELS = {
    "free_plan_seen": "2026-11-13 登入 Free Plan",
    "free_request_quota_seen_paid_wallet_separate": "2026-11-13 免費請求額度（與付費餘額分開）",
    "zero_wallet_free_catalog_separate": "2026-11-13 帳戶零餘額；公開免費路由獨立",
    "daily_free_neurons_seen": "2026-11-13 已見免費 Neurons 配額",
    "no_paid_subscription_seen_free_pool_not_confirmed": "2026-11-13 未訂閱；免費 API 池待確認",
    "api_free_price_confirmed_in_followup": "2026-11-13 官方 Flash 價目為 Free",
    "free_prototype_policy_and_rate_limit_seen": "2026-11-13 官方原型試用及限流",
}


def local_time(iso_utc: str | None) -> str:
    if not iso_utc or not isinstance(iso_utc, str):
        return "未取得"
    try:
        moment = datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
        if moment.tzinfo is None:
            return "無時區標記"
        return moment.astimezone(TAIPEI).strftime("%Y-%m-%d %H:%M")
    except (ValueError, OverflowError):
        return "日期無效"


def catalog_age_label(iso_utc: str | None, *, now: datetime | None = None) -> str:
    try:
        timestamp = datetime.fromisoformat(str(iso_utc).replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            return "查核時間未知"
        reference = now or datetime.now(timezone.utc)
        delta = (reference - timestamp).total_seconds()
        if delta < -300:
            return "查核時間異常"
        if delta > 24 * 3600:
            return "超過24小時（請手動更新）"
        return "近期型錄快照（不是即時額度）"
    except (ValueError, OverflowError):
        return "查核時間未知"


def filter_models(rows: list[dict], *, provider: str = "全部",
                  keyword: str = "", evidence: str = "全部") -> list[dict]:
    q = keyword.casefold().strip()
    return [
        r for r in rows
        if (provider == "全部" or r["provider"] == provider)
        and (evidence == "全部" or r["free_evidence"] == evidence)
        and (not q or q in (r["provider"] + " " + r["model"]).casefold())
    ]


def load_hermes_selection(home: Path) -> dict:
    """Only project provider/model/fallback; don't copy config credentials."""
    config_path = home / "config.yaml"
    data = yaml.safe_load(config_path.read_text(encoding="utf-8-sig")) or {}
    if not isinstance(data, dict):
        raise ValueError("invalid Hermes configuration")
    main = data.get("model") or {}
    if not isinstance(main, dict):
        main = {}
    fallback = data.get("fallback_providers") or []
    return {
        "primary": (str(main.get("provider") or ""), str(main.get("default") or "")),
        "fallback": [(str(x.get("provider")), str(x.get("model")))
                     for x in fallback if isinstance(x, dict)
                     and isinstance(x.get("provider"), str)
                     and isinstance(x.get("model"), str)],
        "config_sha256": sha256(config_path.read_bytes()).hexdigest(),
    }


def save_manager_draft(path: Path, *, primary: tuple[str, str],
                       fallback: list[tuple[str, str]], source_utc: str | None,
                       config_sha256: str,
                       ai_reasons: dict[tuple[str, str], str] | None = None,
                       ai_summary: str = "",
                       ai_primary_reason: str = "") -> None:
    """Only Manager-side selection, never writes Hermes config or keys."""
    if not all(isinstance(x, str) and x for x in primary):
        raise ValueError("primary model must be selected")
    ai_reasons = ai_reasons or {}
    unique = set()
    clean = []
    for p, m in fallback:
        if not p or not m or (p, m) == primary or (p, m) in unique:
            continue
        unique.add((p, m))
        entry = {"provider": p, "model": m}
        explanation = ai_reasons.get((p, m))
        if isinstance(explanation, str) and explanation.strip():
            entry["reason"] = explanation[:1200]
        clean.append(entry)
    payload = {
        "schema": "hermes-free-manager-selection.v1",
        "status": "DRAFT_ONLY_NO_HERMES_WRITES",
        "catalog_snapshot_utc": source_utc,
        "baseline_config_sha256": config_sha256,
        "primary": {"provider": primary[0], "model": primary[1]},
        "fallback": clean,
        "ai_summary": ai_summary[:1200] if isinstance(ai_summary, str) else "",
        "ai_primary_reason": (ai_primary_reason[:1200]
                              if isinstance(ai_primary_reason, str) else ""),
        "saved_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".manager-", suffix=".tmp",
                                     dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def count_by_kind(rows: list[dict]) -> dict:
    return dict(sorted(Counter(r["free_evidence"] for r in rows).items()))
