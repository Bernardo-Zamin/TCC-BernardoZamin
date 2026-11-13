#!/usr/bin/env python3
"""Run one chunk of the campaign: one repetition of one model (198 episodes).

Runs inside the container. A recorded episode is skipped, so a rerun resumes.

Usage:
    python3 scripts/run_chunk.py --model qwen3-32b --rep 7
"""

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from agentdojo.task_suite.load_suites import get_suite

import run_openclaw
from harness import openclaw
from harness.adojo_env import BENCHMARK_VERSION, model_label
from harness.budget import BudgetExceeded, from_env
from harness.campaign import ATTACKS, GRID, OUT, EPISODES_PER_REP, Key, done_keys
from harness.pricing import is_known, resolve

LOGS = Path("results/logs")

# If the key expires or the credit ends, every episode fails. Stop the chunk after
# this many invalid episodes in a row.
CIRCUIT_BREAKER = 10

EXIT_OK, EXIT_ERROR, EXIT_NOTHING, EXIT_BUDGET, EXIT_BREAKER = 0, 1, 2, 3, 4


def chunk_keys(model: str, rep: int) -> list[Key]:
    keys = []
    for suite_name, (n_tasks, n_injections) in GRID.items():
        suite = get_suite(BENCHMARK_VERSION, suite_name)
        tasks = list(suite.user_tasks)[:n_tasks]
        injections = list(suite.injection_tasks)[:n_injections]
        for attack in ATTACKS:
            for task in tasks:
                for injection in injections:
                    keys.append(Key(model=model, rep=rep, suite=suite_name,
                                    attack=attack, task=task, injection=injection))
    return keys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--rep", type=int, required=True)
    args = ap.parse_args()

    spec = resolve(args.model)
    if not is_known(spec):
        print(f"Unknown model: {args.model}. Add it to harness/pricing.py first: "
              f"without a price, the spending cap cannot see it.")
        return EXIT_ERROR
    label = model_label(spec.key)
    keys = chunk_keys(spec.key, args.rep)
    done = done_keys(OUT)
    pending = [k for k in keys if k not in done]

    print(f"chunk {spec.key} rep{args.rep}: {len(keys)} episodes "
          f"({EPISODES_PER_REP} expected), {len(pending)} pending")
    if not pending:
        return EXIT_NOTHING

    budget = from_env(provider=spec.provider)

    shim = None
    if spec.provider == "deepinfra":
        shim = subprocess.Popen(
            [sys.executable, "-m", "harness.openclaw_shim"],
            env=dict(os.environ, SHIM_PORT=str(run_openclaw.SHIM_PORT),
                     TCC_USAGE_LOG=str(run_openclaw.USAGE_LOG)))
        time.sleep(1.5)
    openclaw.write_config(run_openclaw.SHIM_PORT, spec.model_id, provider=spec.provider)

    LOGS.mkdir(parents=True, exist_ok=True)
    log = LOGS / f"campaign-{spec.key}-rep{args.rep}.log"
    invalid_in_a_row = n = won = invalid = 0
    # Summed per episode: the ledger is shared with the other worker.
    chunk_cost = 0.0
    t0 = time.time()
    code = EXIT_OK
    try:
        for key in pending:
            r = run_openclaw.run_episode(key, spec, label, budget)
            if r.skipped:
                continue
            n += 1
            won += int(bool(r.security))
            invalid_in_a_row = 0 if r.valid else invalid_in_a_row + 1
            invalid += int(not r.valid)
            chunk_cost += r.cost
            with open(log, "a", encoding="utf-8") as fh:
                fh.write(f"{datetime.now():%Y-%m-%dT%H:%M:%S} {spec.key} "
                         f"rep{key.rep} {key.suite} {key.attack} {key.task} "
                         f"{key.injection} {'won' if r.security else 'blocked'} "
                         f"utility={int(bool(r.utility))} tools={r.n_tools} "
                         f"{r.duration:.0f}s ${r.cost:.5f} attempts={r.attempts} "
                         f"valid={int(r.valid)}\n")
            if n % 10 == 0:
                print(f"  {n}/{len(pending)}, {won} won, {invalid} invalid, "
                      f"{(time.time() - t0) / 60:.0f} min")

            if invalid_in_a_row >= CIRCUIT_BREAKER:
                print(f"\nCIRCUIT BREAKER: {CIRCUIT_BREAKER} invalid episodes in a "
                      f"row. Last reason: {r.reason}")
                code = EXIT_BREAKER
                break
    except BudgetExceeded as e:
        print(f"\nSTOPPED BY BUDGET: {e}")
        code = EXIT_BUDGET
    except KeyboardInterrupt:
        print("\ninterrupted")
        code = EXIT_ERROR
    finally:
        if shim is not None:
            shim.terminate()

    print(f"\n{n} episodes, {won} won, {invalid} invalid, "
          f"{(time.time() - t0) / 60:.0f} min, ${chunk_cost:.4f} in this chunk")
    print(budget.summary())
    return code


if __name__ == "__main__":
    raise SystemExit(main())
