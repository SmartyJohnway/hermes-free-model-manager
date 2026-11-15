"""Read the public Nous Free Models showcase without credentials or web sessions."""
from __future__ import annotations

from datetime import datetime, timezone
import re

import httpx

NOUS_MODELS_URL = "https://portal.nousresearch.com/models"
MAX_HTML_BYTES = 1_500_000
FREE_ID = re.compile(r"\b[a-z0-9_.~-]+/[a-z0-9_.:/~-]+:free\b", re.IGNORECASE)


def parse_nous_free_showcase(html: str) -> list[str]:
    """Constrain extraction to the public Free Models section, never paid promos."""
    start = html.find("Free Models")
    if start < 0:
        raise ValueError("missing Free Models heading")
    end = html.find("Active Promos", start)
    if end <= start or end - start > 30_000:
        raise ValueError("unknown free section boundary")
    section = html[start:end]
    if "FREE" not in section:
        raise ValueError("free label missing")
    models = sorted(set(FREE_ID.findall(section)))
    if not 1 <= len(models) <= 60:
        raise ValueError("unreasonable free model count")
    return models


def _get_public_html() -> str:
    """Fetch a single fixed official URL, without forwarding cookies or keys."""
    with httpx.Client(timeout=15, follow_redirects=False, trust_env=False) as client:
        resp = client.get(NOUS_MODELS_URL, headers={
            "Accept": "text/html",
            "User-Agent": "Hermes-Free-Model-Manager-M2-Research/0.1"})
        resp.raise_for_status()
        if len(resp.content) > MAX_HTML_BYTES:
            raise ValueError("Nous public page too large")
        if "html" not in resp.headers.get("content-type", "").lower():
            raise ValueError("Nous public models did not return HTML")
        return resp.text


def discover_nous_public(transport=None) -> dict:
    """Does not prove account entitlements, live inference or tool compatibility."""
    observed_at = datetime.now(timezone.utc).isoformat()
    try:
        html = (transport or _get_public_html)()
        models = parse_nous_free_showcase(html)
        return {
            "status": "ok",
            "observed_at": observed_at,
            "url": NOUS_MODELS_URL,
            "models": models,
            "evidence": "public Free Models heading with FREE badges",
            "account_plan_proven": False,
            "inference_verified": False,
            "auto_apply_allowed": False,
        }
    except (httpx.HTTPError, TimeoutError, OSError, ValueError, TypeError) as exc:
        return {
            "status": "failed",
            "observed_at": observed_at,
            "error_type": type(exc).__name__,
            "models": [],
            "auto_apply_allowed": False,
        }
