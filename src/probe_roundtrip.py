"""M2 P5: bounded synthetic TWO-REQUEST tool-result round trip.

Zero production Hermes changes. P4+P5 retain shared request history and
exclusive concurrency lock without artificial UTC-day request caps. Only
Groq and Cloudflare models with recorded P4 one-step PASS are eligible.
Each full synthetic tool loop uses no more than two POST requests.
No real filesystem, terminal, network tool or private user content is sent.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import secrets

import httpx

from provider_accounts import read_secret
from tool_compatibility import (
    TARGETS, _exclusive, _load_state,
    _post, _save_state, parse_response, select_probe,
)
from probe_policy import advance_p5_health, can_probe_now

P5_PROVIDERS = frozenset(("groq", "cloudflare"))
P5_FIRST_TOOL_MAX_TOKENS = 128
P5_FINAL_MAX_TOKENS = 512  # GPT-OSS may spend thinking tokens before a short receipt.
TOOL_NAME = "m2_probe_marker"
TOOL_MARKER = "M2P5"
TOOL_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
P5_FUNCTION = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": "Return a synthetic verification receipt. No external action.",
        "parameters": {
            "type": "object",
            # Match the simpler P4 tool schema that already worked with
            # Cloudflare; exact marker validation happens after the reply.
            "properties": {"marker": {"type": "string"}},
            "required": ["marker"],
            "additionalProperties": False,
        },
    },
}


def first_request(model: str) -> dict:
    return {
        "model": model,
        "messages": [{
            "role": "user",
            "content": (
                "Call m2_probe_marker with marker M2P5. After the tool response, "
                "return exactly the receipt string that the tool supplies. "
                "Never invent a receipt."
            ),
        }],
        "tools": [P5_FUNCTION],
        "tool_choice": "required",
        "max_tokens": P5_FIRST_TOOL_MAX_TOKENS,
        "stream": False,
    }


def exact_tool_request(status: int, data: object) -> tuple[str | None, str]:
    """Accept exactly one safe model tool call; reject unknown or multiple tools."""
    if status != 200:
        return None, "first_http_not_200"
    if not isinstance(data, dict):
        return None, "first_invalid_body"
    choices = data.get("choices")
    if not isinstance(choices, list) or len(choices) < 1:
        return None, "first_missing_choices"
    head = choices[0]
    if not isinstance(head, dict) or not isinstance(head.get("message"), dict):
        return None, "first_missing_message"
    calls = head["message"].get("tool_calls")
    if not isinstance(calls, list) or len(calls) != 1:
        return None, "first_not_exactly_one_tool_call"
    call = calls[0]
    if not isinstance(call, dict):
        return None, "first_malformed_tool_call"
    call_id, func = call.get("id"), call.get("function")
    if not isinstance(call_id, str) or not TOOL_ID.fullmatch(call_id):
        return None, "first_invalid_tool_call_id"
    if not isinstance(func, dict) or func.get("name") != TOOL_NAME:
        return None, "first_unrecognized_tool"
    args = func.get("arguments")
    if isinstance(args, str):
        if len(args) > 512:
            return None, "first_tool_arguments_too_large"
        try:
            args = json.loads(args)
        except (TypeError, ValueError):
            return None, "first_invalid_tool_arguments"
    if not isinstance(args, dict) or args != {"marker": TOOL_MARKER}:
        return None, "first_tool_arguments_mismatch"
    return call_id, "verified_synthetic_function_call"


def second_request(model: str, call_id: str, receipt: str) -> dict:
    if not TOOL_ID.fullmatch(call_id):
        raise ValueError("invalid tool call id")
    if not re.fullmatch(r"P5_[0-9a-f]{16}", receipt):
        raise ValueError("invalid ephemeral receipt")
    messages = first_request(model)["messages"]
    messages += [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": call_id,
                "type": "function",
                "function": {
                    "name": TOOL_NAME,
                    "arguments": json.dumps({"marker": TOOL_MARKER}),
                },
            }],
        },
        {
            "role": "tool",
            "tool_call_id": call_id,
            # Hermes ChatCompletionsTransport._sanitize_message also
            # removes "name" from tool messages before provider API POST.
            "content": json.dumps({"receipt": receipt}),
        },
    ]
    return {
        "model": model,
        "messages": messages,
        "tools": [P5_FUNCTION],
        "tool_choice": "none",
        "max_tokens": P5_FINAL_MAX_TOKENS,
        "stream": False,
    }


def exact_final_receipt(status: int, data: object, receipt: str) -> tuple[bool, str]:
    if status != 200:
        return False, "final_http_not_200"
    if not isinstance(data, dict):
        return False, "final_invalid_body"
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return False, "final_missing_choices"
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return False, "final_missing_message"
    if message.get("tool_calls"):
        return False, "final_requested_more_tools"
    answer = message.get("content")
    if not isinstance(answer, str):
        return False, "final_no_text"
    if answer.strip() != receipt:
        return False, "final_receipt_mismatch"
    return True, "tool_result_consumed_exactly"


def _record_call(state: dict, path: Path, provider: str, model: str,
                 date: str, stage: str) -> None:
    # Audit requests without confusing an internal count for vendor quota.
    state["reservations"].append({
        "utc_date": date, "provider": provider, "model": model,
        "stage": stage, "reserved_at_utc": datetime.now(timezone.utc).isoformat(),
    })
    _save_state(path, state)


def _safe_stage(status: int, payload: dict, headers: dict) -> dict:
    safe = parse_response(status, payload, headers)
    stage = {
        "http_status": safe.get("http_status"),
        "usage": safe.get("usage", {}),
        "quota_headers": safe.get("quota_headers", {}),
        "provider_error_code": safe.get("provider_error_code"),
        "finish_reason": safe.get("finish_reason"),
    }
    if status >= 400 and isinstance(payload, dict):
        # Never log or persist provider error messages. Only an allowlisted
        # diagnostic category and basic JSON schema shape are useful.
        error = payload.get("error")
        errors = payload.get("errors")
        values = []
        if isinstance(error, str):
            values.append(error)
        elif isinstance(error, dict):
            values += [str(error.get(k, "")) for k in ("message", "type")]
        if isinstance(errors, list):
            for row in errors[:2]:
                if isinstance(row, dict):
                    values.append(str(row.get("message", "")))
        if isinstance(payload.get("message"), str):
            values.append(payload["message"])
        msg = " ".join(values).lower()[:3000]
        patterns = (
            ("max_tokens", "output_token_parameter"),
            ("tool_choice", "tool_choice_parameter"),
            ("tool", "tool_schema_or_tooling"),
            ("schema", "schema_validation"),
            ("message", "message_schema"),
            ("model", "model_validation"),
            ("rate", "rate_limit_related"),
            ("neuron", "neuron_allowance_related"),
            ("quota", "quota_related"),
            ("authentication", "authentication_related"),
        )
        stage["error_category"] = next(
            (label for needle, label in patterns if needle in msg),
            "unknown_error")
        stage["error_payload_shape"] = (
            "error_object" if isinstance(error, dict) else
            "error_string" if isinstance(error, str) else
            "errors_array" if isinstance(errors, list) else
            "other_json")
    return stage


def _send(send, provider: str, home: Path, key: str, request: dict):
    try:
        code, payload, headers = send(provider, home, key, request)
        return int(code), payload if isinstance(payload, dict) else {}, headers or {}, None
    except (httpx.HTTPError, TimeoutError, OSError, ValueError) as exc:
        # Never persist exception strings: they can contain a URL with account ID.
        return 0, {}, {}, type(exc).__name__


def utc_day_matches(date: str, *, now: datetime | None = None) -> bool:
    """Prevent mis-accounted second requests across UTC midnight."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("UTC clock must have timezone")
    return current.astimezone(timezone.utc).date().isoformat() == date


def run_round_trip(home: Path, projection: dict, provider: str, state_path: Path,
                   *, sender=None, utc_date: str | None = None,
                   receipt_factory=None) -> dict:
    if provider not in P5_PROVIDERS:
        return {"provider": provider, "status": "hold", "reason": "provider_not_approved"}
    if sender is None and (utc_date is not None or receipt_factory is not None):
        raise ValueError("live API probes cannot override UTC date or receipt generator")
    choice = select_probe(provider, projection)
    if not choice.get("ready"):
        return {"provider": provider, "status": "hold",
                "reason": choice.get("reason", "not_free_qualified")}
    model = choice["model"]
    key = read_secret(home, TARGETS[provider][1])
    if not key:
        return {"provider": provider, "status": "hold", "reason": "credential_missing"}
    date = utc_date or datetime.now(timezone.utc).date().isoformat()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise ValueError("UTC date invalid")
    send = sender or _post
    with _exclusive(state_path):
        state = _load_state(state_path)
        if not isinstance(state.get("p5_results", {}), dict) or not isinstance(state.get("p5_health", {}), dict):
            raise ValueError("corrupted P5 extension: no request")
        previous = state["results"].get(provider + "::" + model)
        if not isinstance(previous, dict) or previous.get("result") != "pass" or not previous.get("tool_call_confirmed"):
            return {"provider": provider, "status": "hold",
                    "reason": "P4_first_step_real_compatibility_missing"}
        last_health = state.get("p5_health", {}).get(provider + "::" + model, {})
        allowed, reason = can_probe_now(last_health)
        if not allowed:
            return {"provider": provider, "status": "hold", "reason": reason}
        # Repeated opt-in tests are permitted while actual provider limits,
        # current Free-plan qualification and health/cooldown gates allow.
        report: dict = {
            "provider": provider, "model": model, "utc_date": date,
            "status": "unverified", "steps": 0, "stages": [],
            "tool_round_trip_confirmed": False,
            "hermes_agent_confirmed": False,
            "production_auto_apply": False,
        }
        # Reserve stage 1 BEFORE any API request. Locks are shared with M2 P4.
        _record_call(state, state_path, provider, model, date, "p5_tool_request")
        code, body, headers, err = _send(
            send, provider, home, key, first_request(model))
        report["steps"] = 1
        report["stages"].append(_safe_stage(code, body, headers))
        call_id, reason = exact_tool_request(code, body)
        if call_id is None:
            report.update(status="hold", reason=reason,
                          network_error_type=err)
        elif sender is None and not utc_day_matches(date):
            # A request that starts before UTC midnight must not reserve its
            # second POST against the previous day after midnight.
            report.update(status="hold", reason="utc_day_changed_after_first_request")
            state.setdefault("p5_results", {})[provider + "::" + model] = report
            _save_state(state_path, state)
            return report
        else:
            # The synthetic receipt is generated AFTER stage 1, not known to model.
            receipt = (receipt_factory or (lambda: "P5_" + secrets.token_hex(8)))()
            if not re.fullmatch(r"P5_[0-9a-f]{16}", receipt):
                raise ValueError("receipt generator failed")
            second = second_request(model, call_id, receipt)
            _record_call(state, state_path, provider, model, date, "p5_tool_result")
            code2, body2, headers2, err2 = _send(
                send, provider, home, key, second)
            report["steps"] = 2
            report["stages"].append(_safe_stage(code2, body2, headers2))
            confirmed, reason = exact_final_receipt(code2, body2, receipt)
            if not confirmed and report["stages"][-1].get("finish_reason") == "length":
                reason = "final_output_truncated"
            report.update(status="loop_pass" if confirmed else "hold",
                          reason=reason, tool_round_trip_confirmed=confirmed,
                          network_error_type=err2)
        # Interpret failures separately from tool compatibility. A 429 does
        # not count as the first of three model failures.
        statuses = [x.get("http_status") for x in report["stages"]]
        if report["tool_round_trip_confirmed"]:
            event = "loop_pass"
        elif 429 in statuses:
            event = "rate_limited"
        elif 402 in statuses:
            event = "payment_required"
        elif any(x in statuses for x in (401, 403)):
            event = "auth_error"
        elif 404 in statuses:
            event = "route_missing"
        elif any(x in statuses for x in (400, 422)):
            event = "tool_capability_unverified"
        elif any(isinstance(x, int) and x >= 500 for x in statuses):
            event = "server_error"
        elif 0 in statuses:
            event = "transport_failure"
        elif report.get("reason") in (
            "final_output_truncated",
            "first_not_exactly_one_tool_call", "first_tool_arguments_mismatch",
            "first_invalid_tool_call_id", "first_unrecognized_tool"):
            event = "tool_capability_unverified"
        else:
            event = "tool_result_mismatch"
        retry = next((s.get("quota_headers", {}).get("retry_after")
                      for s in report["stages"]
                      if "retry_after" in s.get("quota_headers", {})), None)
        health = advance_p5_health(last_health, event, utc_date=date,
                                   retry_after_seconds=retry)
        report["health_event"] = event
        report["health_status"] = health["status"]
        # No token, raw body, tool_call_id, prompt or receipt in persistent data.
        state.setdefault("p5_results", {})[provider + "::" + model] = report
        state.setdefault("p5_health", {})[provider + "::" + model] = health
        _save_state(state_path, state)
        return report
