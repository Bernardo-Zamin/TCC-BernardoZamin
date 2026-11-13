"""Run headless OpenClaw as the agent under test, one episode at a time.

For each episode: write the provider config, register the task's MCP server, run
`openclaw agent exec` with the user prompt, and return the JSON output. HOME is
in tmpfs because the container's root filesystem is read-only.

There are two paths:
- DeepInfra: a custom provider `di` that points to the local shim.
- Anthropic: OpenClaw's built-in `anthropic` provider, with no shim. A proxy
  `baseUrl` would change the request OpenClaw sends.
"""

from __future__ import annotations

import os
import sys
import json
import subprocess
from pathlib import Path

# The Python of this venv, which has agentdojo. A plain "python3" would resolve
# to the system Python.
PYTHON = sys.executable

HOME = Path("/tmp/oc")
CONFIG = HOME / ".openclaw" / "openclaw.json"

PROVIDER = "di"
ANTHROPIC = "anthropic"
MCP_NAME = "adojo"


def _env(provider: str = "deepinfra") -> dict:
    """OpenClaw's environment. With an Anthropic key present, OpenClaw queries
    that API's catalog, so the key is passed only for Claude."""
    env = dict(os.environ, HOME=str(HOME))
    if provider != "anthropic":
        env.pop("ANTHROPIC_API_KEY", None)
    return env


def _run(args, **kw):
    return subprocess.run(args, env=_env(), capture_output=True, text=True, **kw)


def model_ref(model_id: str, provider: str = "deepinfra") -> str:
    """The `provider/model` value for --model."""
    if provider == "anthropic":
        return f"{ANTHROPIC}/{model_id}"
    return f"{PROVIDER}/{model_id}"


def write_config(shim_port: int, model_id: str, max_tokens: int = 4096,
                 provider: str = "deepinfra"):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    cfg = {}
    if provider != "anthropic":
        cfg["models"] = {"mode": "merge", "providers": {PROVIDER: {
            "api": "openai-completions",
            "baseUrl": f"http://127.0.0.1:{shim_port}/v1",
            "apiKey": "shim",
            "models": [{"id": model_id, "name": model_id.split("/")[-1],
                        "contextWindow": 40960, "maxTokens": max_tokens,
                        "reasoning": True, "compat": {"supportsTools": True}}]}}}
    # The agent sees only the task's tools, not OpenClaw's 34 native ones. With
    # all 58 together, Qwen refused the task without calling anything.
    cfg["tools"] = {"allow": [f"{MCP_NAME}__*"]}
    CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def register_mcp(spec_path: str, out_path: str, name: str = MCP_NAME,
                 provider: str = "deepinfra") -> bool:
    """Register the task's MCP server. Return False if the registered server is
    not the one for this episode."""
    if provider == "anthropic":
        # OpenClaw refuses to save a config that shrinks below half its size.
        # Claude's config is small, so `mcp unset` would fail and the episode
        # would inherit the previous server. Rewriting the config avoids this.
        write_config(0, "", provider=ANTHROPIC)
    _run(["openclaw", "mcp", "unset", name])
    # No --no-probe: the probe caches the tool names. Without it, `agent exec`
    # resolves the allowlist before connecting and finds no tools.
    r = _run(["openclaw", "mcp", "add", name,
              "--command", PYTHON, "--arg", "/app/harness/mcp_agentdojo.py",
              "--env", f"AGENTDOJO_SPEC={spec_path}",
              "--env", f"AGENTDOJO_OUT={out_path}",
              "--connect-timeout", "40", "--timeout", "120",
              "--approval", "approve"])
    try:
        srv = json.loads(CONFIG.read_text(encoding="utf-8"))["mcp"]["servers"][name]
    except (OSError, ValueError, KeyError):
        return False
    return r.returncode == 0 and srv.get("env", {}).get("AGENTDOJO_SPEC") == spec_path


def run_exec(prompt: str, model_id: str, timeout_s: int = 300,
             provider: str = "deepinfra") -> dict:
    """Run one episode and return OpenClaw's JSON output (ok, final, usage...)."""
    p = subprocess.Popen(
        ["openclaw", "agent", "exec", "--json",
         "--model", model_ref(model_id, provider), "--code-mode", "direct",
         "--message-file", "-"],
        env=_env(provider),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        out, _ = p.communicate(prompt, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        p.kill()
        return {"ok": False, "final": "", "error": {"message": "timeout"},
                "usage": {"input": 0, "output": 0}}
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"ok": False, "final": "", "error": {"message": "unreadable output"},
                "usage": {"input": 0, "output": 0}}
