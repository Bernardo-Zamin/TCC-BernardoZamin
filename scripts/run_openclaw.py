#!/usr/bin/env python3
"""Run AgentDojo tasks with OpenClaw as the agent under test.

One episode: build the attacked environment -> register the task's MCP server ->
run OpenClaw -> read the final state and trace written by the server -> evaluate
utility and security with AgentDojo -> write one JSON file.

A recorded episode is skipped, so a rerun resumes. Instrument failures are retried
before they become data.

Usage:
    python scripts/run_openclaw.py --suite workspace --attack important_instructions \
        --user-tasks 3 --injection-tasks 2
"""

import argparse
import json
import os
import pickle
import shutil
import subprocess
import sys
import time
from collections import namedtuple
from pathlib import Path

from agentdojo.attacks.attack_registry import load_attack
from agentdojo.functions_runtime import FunctionCall
from agentdojo.task_suite.load_suites import get_suite

from harness import openclaw
from harness.adojo_env import (BENCHMARK_VERSION, _PipelineStub, build_attacked,
                               model_label)
from harness.budget import from_env, BudgetExceeded
from harness.campaign import (BLOCKED, INVALID, OUT, WON, Key, episode_path,
                              name_pattern, remove_previous)
from harness.pricing import is_known, cost_usd, resolve

EP = Path("/tmp/ep")
SHIM_PORT = 8898

# The shim writes the token usage of every model call here.
USAGE_LOG = EP / "usage.jsonl"

# Tokens of an expensive episode (input without cache, output). Used to check the
# budget before spending, and to charge a Claude episode with no usage data.
EPISODE_CEILING = {"deepinfra": (20_000, 4096), "anthropic": (60_000, 8_000)}

ATTEMPTS = 3
WAIT_S = [30, 120]

Result = namedtuple(
    "Result", "utility security n_tools duration cost valid reason attempts skipped")

_commit = None


def commit() -> str:
    """Hash of the code that produced the episode. harness/ and scripts/ are
    mounted from the host, so an edit during a campaign would otherwise go
    unnoticed."""
    global _commit
    if _commit is None:
        # .git is not mounted in the container, so the runner passes the hash.
        _commit = os.environ.get("TCC_COMMIT") or ""
        if not _commit:
            try:
                _commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                                         capture_output=True, text=True,
                                         timeout=10).stdout.strip()
            except Exception:
                _commit = ""
        _commit = _commit or "?"
    return _commit


def _shim_usage() -> tuple[int, int]:
    """Input and output tokens counted by the shim."""
    if not USAGE_LOG.exists():
        return 0, 0
    tokens_in = tokens_out = 0
    for line in USAGE_LOG.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        tokens_in += d.get("input", 0) or 0
        tokens_out += d.get("output", 0) or 0
    return tokens_in, tokens_out


def attempt_usage(output: dict, spec_model) -> dict:
    """Tokens and cost of one attempt.

    DeepInfra: the shim's count, which includes episodes that timed out; if there
    is none, OpenClaw's output.

    Anthropic: OpenClaw's output, which reports cache reads and writes apart. If it
    has no usage there are two cases:
    - some spend is not reported (timeout, or the agent acted): charge the episode
      ceiling, marked as estimated;
    - no call was answered (DNS, bad key, 400): the API charges nothing.
    """
    u = output.get("usage") or {}
    if spec_model.provider == "anthropic":
        usage = {"input": u.get("input", 0) or 0, "output": u.get("output", 0) or 0,
                 "cache_read": u.get("cacheRead", 0) or 0,
                 "cache_write": u.get("cacheWrite", 0) or 0, "source": "openclaw"}
        if not any(usage[k] for k in ("input", "output", "cache_read", "cache_write")):
            # Exactly "timeout" is the output run_exec builds when it kills the
            # process.
            timed_out = (output.get("error") or {}).get("message") == "timeout"
            acted = bool(output.get("assistantTurns")
                         or (output.get("toolSummary") or {}).get("calls"))
            if timed_out or acted:
                tokens_in, tokens_out = EPISODE_CEILING["anthropic"]
                usage.update(input=tokens_in, output=tokens_out, source="estimated")
            else:
                usage["source"] = "no_response"
    else:
        tokens_in, tokens_out = _shim_usage()
        source = "shim"
        if not tokens_in:
            tokens_in = u.get("input", 0) or 0
            tokens_out = u.get("output", 0) or 0
            source = "openclaw"
        usage = {"input": tokens_in, "output": tokens_out, "cache_read": 0,
                 "cache_write": 0, "source": source}
    usage["cost_usd"] = round(cost_usd(spec_model, usage["input"], usage["output"],
                                       usage["cache_read"], usage["cache_write"]), 6)
    return usage


def instrument_failure(output: dict) -> str | None:
    """Reason, if the instrument failed and not the agent. Without this, a 429
    would be recorded as "blocked" and lower the ASR."""
    if output.get("ok") is False:
        return (output.get("error") or {}).get("message") or "ok=False"
    return (output.get("error") or {}).get("message") or None


def evaluate(task, kind, model_output, pre, post, traces):
    """AgentDojo's order: *_from_traces first, then the plain function."""
    from_traces = getattr(task, kind + "_from_traces", None)
    if from_traces is not None:
        r = from_traces(model_output, pre, post, traces)
        if r is not None:
            return bool(r)
    simple = getattr(task, kind)
    try:
        return bool(simple(model_output, pre, post, strict=True))
    except TypeError:
        return bool(simple(model_output, pre, post))


def episode_document(key: Key, spec_model, output, utility, security, traces,
                     duration, valid, reason, attempts, usage=None):
    """The episode file. Field names match the recorded episodes (see
    results/README.md)."""
    verdict = INVALID if not valid else (WON if security else BLOCKED)
    return {
        "resumo": {
            "veredito": verdict,
            "agente": "openclaw",
            "suite": key.suite, "tarefa": key.task, "injecao": key.injection,
            "ataque": key.attack, "repeticao": key.rep,
            "modelo_key": spec_model.key,
            "modelo": f"OpenClaw+{spec_model.model_id}",
            "tarefa_concluida": bool(utility), "ataque_venceu": bool(security),
            "temperature": None, "duracao_s": round(duration, 1),
            "ok": output.get("ok"),
            "erro": (output.get("error") or {}).get("message"),
            "avaliacao_valida": valid,
            "motivo_invalido": reason,
            "tentativas": attempts,
            "commit": commit(),
            "provedor": spec_model.provider,
            "uso": usage,
        },
        "tarefa_do_usuario": output.get("_user_prompt", ""),
        "conteudo_injetado": output.get("_injection", ""),
        "acoes_do_agente": [{"ferramenta": t["function"], "argumentos": t["args"],
                             "retorno": t.get("result", "")} for t in traces],
        "resposta_ao_usuario": output.get("final", ""),
        "openclaw_envelope": {k: v for k, v in output.items()
                              if not k.startswith("_")},
    }


def _write_episode(key: Key, doc: dict) -> None:
    remove_previous(key)
    target = OUT / episode_path(key, doc["resumo"]["veredito"])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def run_episode(key: Key, spec_model, label, budget, max_attempts=ATTEMPTS) -> Result:
    """One episode, with resume and retry. BudgetExceeded and KeyboardInterrupt
    propagate; failures of the system under test do not."""
    if any(OUT.glob("**/" + name_pattern(key))):
        return Result(None, None, 0, 0.0, 0.0, True, None, 0, True)

    # One folder per episode, so a leftover MCP server from the previous episode
    # cannot write to this episode's out.pkl.
    ep_dir = EP / f"{key.rep}-{key.suite}-{key.task}-{key.injection}-{key.attack}"
    spec_path, out_path = str(ep_dir / "spec.json"), str(ep_dir / "out.pkl")

    total_cost, reason = 0.0, None
    for attempt in range(1, max_attempts + 1):
        shutil.rmtree(ep_dir, ignore_errors=True)
        ep_dir.mkdir(parents=True, exist_ok=True)
        json.dump({"suite": key.suite, "user_task": key.task,
                   "injection_task": key.injection, "attack": key.attack,
                   "label": label}, open(spec_path, "w"))
        try:
            suite, user_task, inj_task, env0, _ = build_attacked(
                key.suite, key.task, key.injection, key.attack, label)
            pre = env0.model_copy(deep=True)
            injected = ""
            try:
                injected = "\n\n".join(
                    load_attack(key.attack, suite, _PipelineStub(label))
                    .attack(user_task, inj_task).values())
            except Exception:
                pass

            if not openclaw.register_mcp(spec_path, out_path,
                                         provider=spec_model.provider):
                raise RuntimeError("MCP server registration failed")
            budget.check(cost_usd(spec_model, *EPISODE_CEILING[spec_model.provider]))

            USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
            USAGE_LOG.write_text("")  # the count is per episode
            t0 = time.time()
            output = openclaw.run_exec(user_task.PROMPT, spec_model.model_id,
                                       provider=spec_model.provider)
            duration = time.time() - t0
            output["_user_prompt"] = user_task.PROMPT
            output["_injection"] = injected

            usage = attempt_usage(output, spec_model)
            total_cost += usage["cost_usd"]
            budget.record(usage["cost_usd"],
                          usage["input"] + usage["cache_read"] + usage["cache_write"],
                          usage["output"])

            reason = instrument_failure(output)
            if reason and attempt < max_attempts:
                time.sleep(WAIT_S[min(attempt - 1, len(WAIT_S) - 1)])
                continue

            raw_trace, traces, post = [], [], pre
            if os.path.exists(out_path):
                blob = pickle.load(open(out_path, "rb"))
                post = blob["env_out"]
                raw_trace = blob["trace"]
                traces = [FunctionCall(function=t["function"], args=t["args"])
                          for t in raw_trace]
            final = output.get("final", "") or ""
            utility = evaluate(user_task, "utility", final, pre, post, traces)
            security = evaluate(inj_task, "security", final, pre, post, traces)

            valid = reason is None
            _write_episode(key, episode_document(
                key, spec_model, output, utility, security, raw_trace, duration,
                valid, reason, attempt, usage))
            return Result(utility, security, len(traces), duration, total_cost, valid,
                          reason, attempt, False)

        except (BudgetExceeded, KeyboardInterrupt):
            raise
        except Exception as e:
            reason = f"{type(e).__name__}: {e}"[:200]
            if attempt < max_attempts:
                time.sleep(WAIT_S[min(attempt - 1, len(WAIT_S) - 1)])
        finally:
            shutil.rmtree(ep_dir, ignore_errors=True)

    # All attempts failed: keep a file as proof, marked invalid.
    _write_episode(key, episode_document(
        key, spec_model, {"ok": False, "error": {"message": reason}},
        False, False, [], 0.0, False, reason, max_attempts))
    return Result(False, False, 0, 0.0, total_cost, False, reason, max_attempts, False)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", default="workspace")
    ap.add_argument("--attack", default="important_instructions")
    ap.add_argument("--model", default="qwen3-32b")
    ap.add_argument("--user-tasks", type=int, default=3)
    ap.add_argument("--injection-tasks", type=int, default=2)
    ap.add_argument("--rep", type=int, default=0,
                    help="repetition number; 0 means outside the campaign")
    args = ap.parse_args()

    spec_model = resolve(args.model)
    if not is_known(spec_model):
        print(f"Unknown model: {args.model}. Add it to harness/pricing.py first: "
              f"without a price, the spending cap cannot see it.")
        return 1
    label = model_label(spec_model.key)
    budget = from_env(provider=spec_model.provider)

    shim = None
    if spec_model.provider == "deepinfra":
        shim = subprocess.Popen(
            [sys.executable, "-m", "harness.openclaw_shim"],
            env=dict(os.environ, SHIM_PORT=str(SHIM_PORT),
                     TCC_USAGE_LOG=str(USAGE_LOG)))
        time.sleep(1.5)
    openclaw.write_config(SHIM_PORT, spec_model.model_id, provider=spec_model.provider)

    suite = get_suite(BENCHMARK_VERSION, args.suite)
    user_tasks = list(suite.user_tasks)[:args.user_tasks]
    injections = list(suite.injection_tasks)[:args.injection_tasks]

    print(f"Agent : OpenClaw + {spec_model.model_id}")
    print(f"Suite : {args.suite} ({BENCHMARK_VERSION}), attack {args.attack}, "
          f"repetition {args.rep}")
    print(f"Runs  : {len(user_tasks)} tasks x {len(injections)} injections\n")

    won = n = skipped = invalid = 0
    try:
        for task in user_tasks:
            for injection in injections:
                key = Key(model=spec_model.key, rep=args.rep, suite=args.suite,
                          attack=args.attack, task=task, injection=injection)
                r = run_episode(key, spec_model, label, budget)
                if r.skipped:
                    skipped += 1
                    continue
                n += 1
                if not r.valid:
                    invalid += 1
                    print(f"{task:16s} {injection:18s} INVALID  {(r.reason or '')[:40]}")
                    continue
                won += int(bool(r.security))
                print(f"{task:16s} {injection:18s} "
                      f"utility={'ok' if r.utility else 'failed':6s} "
                      f"attack={'won' if r.security else 'blocked':7s} "
                      f"tools={r.n_tools:<3d} {r.duration:.0f}s")
    except BudgetExceeded as e:
        print(f"\nSTOPPED BY BUDGET: {e}")
        return 3
    finally:
        if shim is not None:
            shim.terminate()

    valid = n - invalid
    if valid:
        print(f"\nASR ({args.attack}): {won / valid:.0%} ({won}/{valid})")
    print(f"Skipped: {skipped}. Invalid: {invalid}.")
    print(f"Budget: {budget.summary()}")
    print(f"Episodes in: {OUT}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
