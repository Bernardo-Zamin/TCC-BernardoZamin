"""MCP server over stdio that exposes the tools of one AgentDojo task.

OpenClaw starts this script as a child process. AGENTDOJO_SPEC says which
combination to serve. Each tools/call runs the tool in the simulated environment
and rewrites the state to AGENTDOJO_OUT, so the runner can read it even if
OpenClaw kills this process.

Protocol: raw JSON-RPC 2.0, one message per line, with no external dependency.
Handshake: initialize -> notifications/initialized -> tools/list -> tools/call.
"""

# Only light imports here. Importing agentdojo takes about 2 s; doing that before
# `initialize` makes OpenClaw time out and resolve the tool allowlist to nothing.
# The environment is built on the first tools/list instead.
import sys
import os
import json
import pickle

SPEC = json.load(open(os.environ["AGENTDOJO_SPEC"], encoding="utf-8"))
# Pickle, not JSON: the pydantic round trip loses emails (it deduplicates by id),
# and the exfiltration evaluators would give false negatives.
OUT = os.environ["AGENTDOJO_OUT"]

STATE = {}
TRACE: list[dict] = []


def _ensure_built():
    if STATE:
        return
    sys.path.insert(0, "/app")
    from harness.adojo_env import build_attacked, tools_for_mcp
    suite, user_task, inj_task, env, runtime = build_attacked(
        SPEC["suite"], SPEC["user_task"], SPEC["injection_task"], SPEC["attack"],
        SPEC.get("label", "Qwen"))
    STATE.update(suite=suite, env=env, runtime=runtime,
                 tools=tools_for_mcp(suite))
    _write_out()


def _write_out():
    """Write the state and the trace atomically."""
    payload = {"env_out": STATE.get("env"), "trace": TRACE}
    tmp = OUT + ".tmp"
    with open(tmp, "wb") as fh:
        pickle.dump(payload, fh)
    os.replace(tmp, OUT)


def _send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _call_tool(name, arguments):
    _ensure_built()
    result, err = STATE["runtime"].run_function(STATE["env"], name, arguments)
    text = err if err else (result if isinstance(result, str)
                            else json.dumps(result, ensure_ascii=False, default=str))
    # Keep the result: it is what the agent read, and where the injection shows
    # up. This separates "resisted the injection" from "never read it".
    TRACE.append({"function": name, "args": arguments, "result": text[:4000]})
    _write_out()
    return text, bool(err)


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        method, mid = msg.get("method"), msg.get("id")

        if method == "initialize":
            _send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": msg["params"].get("protocolVersion", "2025-11-25"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "agentdojo", "version": "0.1.0"}}})
        elif method == "notifications/initialized":
            pass
        elif method == "tools/list":
            _ensure_built()
            _send({"jsonrpc": "2.0", "id": mid, "result": {"tools": STATE["tools"]}})
        elif method == "tools/call":
            name = msg["params"].get("name")
            args = msg["params"].get("arguments") or {}
            text, is_err = _call_tool(name, args)
            _send({"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": text}], "isError": is_err}})
        elif mid is not None:
            _send({"jsonrpc": "2.0", "id": mid,
                   "error": {"code": -32601, "message": "method not found: %s" % method}})


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        log = os.environ.get("AGENTDOJO_OUT", "/tmp/mcp") + ".err"
        with open(log, "w") as fh:
            fh.write(traceback.format_exc())
        raise
