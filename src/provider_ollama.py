"""Isolated local Ollama tool-compatibility probe, no Hermes configuration writes."""
from __future__ import annotations

import json

import httpx

from provider_probing import PROBE_FN

BASE = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3.5:4b"


def inspect_local_ollama(model: str = DEFAULT_MODEL, *, transport=None) -> dict:
    """Fixed loopback endpoints only; one bounded request with synthetic tool."""
    if model != DEFAULT_MODEL:
        raise ValueError("only pre-approved local model supported in M2")
    if transport is None:
        def transport(path, payload):
            with httpx.Client(timeout=45.0, trust_env=False, follow_redirects=False) as client:
                if path == "/api/tags" and payload is None:
                    response = client.get(BASE + path)
                elif path == "/api/chat" and isinstance(payload, dict):
                    response = client.post(BASE + path, json=payload)
                else:
                    raise ValueError("non-allowlisted Ollama path")
                return response.status_code, response.json()
    try:
        status, catalog = transport("/api/tags", None)
        if status != 200 or not isinstance(catalog, dict):
            return {"status": "local_service_unavailable"}
        models = catalog.get("models", [])
        if model not in [r.get("name") for r in models if isinstance(r, dict)]:
            return {"status": "local_model_not_installed"}
        request = {
            "model": model, "messages": [{
                "role": "user",
                "content": "Call m2_probe_marker with marker M2. This is a tool compatibility test."}],
            "tools": [PROBE_FN],
            "stream": False,
            "think": False,
            "options": {"num_predict": 96},
        }
        code, result = transport("/api/chat", request)
        if code != 200 or not isinstance(result, dict):
            return {"status": "local_inference_failed", "http_status": code}
        message = result.get("message", {})
        calls = message.get("tool_calls", []) if isinstance(message, dict) else []
        for call in calls if isinstance(calls, list) else []:
            func = call.get("function", {}) if isinstance(call, dict) else {}
            if not isinstance(func, dict) or func.get("name") != "m2_probe_marker":
                continue
            arguments = func.get("arguments", {})
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except ValueError:
                    arguments = {}
            if isinstance(arguments, dict) and arguments.get("marker") == "M2":
                return {"status": "local_tools_verified", "model": model}
        return {"status": "local_tool_call_unconfirmed", "model": model}
    except (httpx.HTTPError, TimeoutError, OSError, ValueError) as exc:
        return {"status": "local_probe_failed", "error_type": type(exc).__name__}
