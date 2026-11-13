"""Proxy between OpenClaw and DeepInfra that fixes the tool call format.

With OpenClaw's large request (long system prompt, many tools), the DeepInfra
models write the tool call as text (`<tool_call>{...}</tool_call>`) instead of
using `message.tool_calls`. OpenClaw does not parse that text, so the tool never
runs. The same model returns a structured call for a small request.

This proxy forwards the request and moves any text tool call to
`message.tool_calls`. It does not touch the prompt, the tools or the reasoning.
It also holds the real API key.

Usage: set DEEPINFRA_API_KEY, and optionally SHIM_PORT (default 8898).
"""

import json
import os
import re
import time
import uuid
import httpx
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from harness.pricing import DEEPINFRA_BASE_URL as UPSTREAM

KEY = os.environ["DEEPINFRA_API_KEY"]
PORT = int(os.environ.get("SHIM_PORT", "8898"))

_TOOL_CALL = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)
_THINK = re.compile(r"<think>.*?</think>", re.S)

# Transient errors are retried here, because an error that reaches OpenClaw ends
# the whole episode. The total wait is short to stay within the 300 s timeout.
RETRYABLE = {429, 500, 502, 503, 504}
WAIT_S = [2, 5, 10]

# Token usage of each call. OpenClaw reports zero usage when an episode times
# out, so the spend is counted here.
USAGE_LOG = os.environ.get("TCC_USAGE_LOG") or ""


def should_retry(status: int) -> bool:
    return status in RETRYABLE


def without_stream(body: dict) -> dict:
    """Turn streaming off. gpt-oss-120b rejects `stream_options` in this case."""
    body["stream"] = False
    body.pop("stream_options", None)
    return body


def _log_usage(data: dict) -> None:
    u = (data or {}).get("usage") or {}
    if not USAGE_LOG or not u:
        return
    try:
        with open(USAGE_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"input": u.get("prompt_tokens", 0) or 0,
                                 "output": u.get("completion_tokens", 0) or 0}) + "\n")
    except OSError:
        pass


def _hoist(msg):
    """Move text tool calls from `content` to the structured field."""
    if msg.get("tool_calls"):
        return msg
    content = msg.get("content") or ""
    calls = []
    for blob in _TOOL_CALL.findall(content):
        try:
            obj = json.loads(blob)
        except json.JSONDecodeError:
            continue
        if not obj.get("name"):
            continue
        calls.append({
            "id": "call_" + uuid.uuid4().hex[:20], "type": "function",
            "function": {"name": obj["name"],
                         "arguments": json.dumps(obj.get("arguments", {}),
                                                 ensure_ascii=False)}})
    if not calls:
        return msg
    msg["content"] = _THINK.sub("", _TOOL_CALL.sub("", content)).strip() or None
    msg["tool_calls"] = calls
    return msg


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = without_stream(json.loads(self.rfile.read(n) or b"{}"))
        target = UPSTREAM + self.path.replace("/v1", "")
        headers = {"Authorization": "Bearer " + KEY, "Content-Type": "application/json"}

        up = None
        for i, wait in enumerate(WAIT_S):
            try:
                up = httpx.post(target, json=body, headers=headers, timeout=180)
            except httpx.HTTPError as e:
                if i == len(WAIT_S) - 1:
                    return self._error(503, f"upstream unreachable: {e}")
                time.sleep(wait)
                continue
            if not should_retry(up.status_code) or i == len(WAIT_S) - 1:
                break
            # Follow Retry-After, but never wait longer than our own limit.
            try:
                wait = min(float(up.headers.get("Retry-After", wait)), wait)
            except ValueError:
                pass
            time.sleep(wait)

        try:
            data = up.json()
        except ValueError:
            # A 502 in HTML, for example. Report it as an error, not a dropped
            # connection.
            return self._error(up.status_code or 502,
                               "upstream did not return JSON")

        _log_usage(data)
        for ch in data.get("choices", []):
            if "message" in ch:
                ch["message"] = _hoist(ch["message"])
                if ch["message"].get("tool_calls") and ch.get("finish_reason") == "stop":
                    ch["finish_reason"] = "tool_calls"
        self._reply(up.status_code, json.dumps(data).encode())

    def _error(self, code, msg):
        self._reply(code, json.dumps({"error": {"message": msg,
                                                "type": "shim"}}).encode())

    def do_GET(self):
        up = httpx.get(UPSTREAM + self.path.replace("/v1", ""),
                       headers={"Authorization": "Bearer " + KEY}, timeout=60)
        self._reply(up.status_code, up.content)

    def _reply(self, code, body):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), _Handler).serve_forever()
