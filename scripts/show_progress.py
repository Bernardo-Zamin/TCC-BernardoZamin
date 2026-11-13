#!/usr/bin/env python3
"""Show campaign progress, pace and spend. Runs on the host, standard library only."""

import datetime
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.budget import LEDGERS
from harness.campaign import (OUT, CAMPAIGN_MODELS, TARGET_REPS, EPISODES_PER_REP,
                              INVALID, episode_paths, done_keys)

LOGS = Path("results/logs")


def main() -> int:
    target = len(CAMPAIGN_MODELS) * TARGET_REPS * EPISODES_PER_REP
    done = {k for k in done_keys(OUT) if k.rep >= 1}
    print(f"CAMPAIGN: {len(done):,} of {target:,} episodes ({len(done) / target:.1%})")

    print(f"  {'rep':>4s} " + " ".join(f"{m:>16s}" for m in CAMPAIGN_MODELS))
    for rep in range(1, TARGET_REPS + 1):
        cells = []
        for m in CAMPAIGN_MODELS:
            n = sum(1 for k in done if k.model == m and k.rep == rep)
            cells.append(f"{n}/{EPISODES_PER_REP}")
        print(f"  {rep:>4d} " + " ".join(f"{c:>16s}" for c in cells))

    files = episode_paths(OUT)
    invalid = sum(1 for p in files if p.name.startswith(INVALID + "__"))
    if invalid:
        print(f"\n  invalid (instrument failure): {invalid}")

    missing = target - len(done)
    times = sorted(p.stat().st_mtime for p in files)
    now = time.time()
    if missing <= 0 and times:
        end = datetime.datetime.fromtimestamp(times[-1])
        print(f"\n  complete, last episode at {end:%Y-%m-%d %H:%M}")
    elif times:
        # Pace over the last hour: after a stop, older files would hide it.
        hour = [t for t in times if now - t <= 3600]
        if len(hour) >= 2:
            pace = (len(hour) - 1) / (hour[-1] - hour[0])
            print(f"\n  pace: {pace * 3600:.0f} episodes/h, {missing:,} missing, "
                  f"about {missing / pace / 3600:.1f} h left")
        idle = (now - times[-1]) / 60
        if idle > 20:
            print(f"  WARNING: no new episode for {idle:.0f} min")

    # The cap is in .env, which only the container loads.
    print()
    for provider, (file, var, default) in LEDGERS.items():
        path = Path("results") / file
        if not path.exists():
            continue
        spent = json.loads(path.read_text())["spent_usd"]
        cap = os.environ.get(var)
        if not cap and Path(".env").exists():
            for line in Path(".env").read_text().splitlines():
                if line.startswith(f"{var}="):
                    cap = line.split("=", 1)[1].strip()
        print(f"  spent on {provider}: ${spent:.2f} of ${float(cap or default):.2f}")

    if LOGS.is_dir():
        print()
        for log in sorted(LOGS.glob("worker*.out")):
            last = log.read_text(errors="replace").strip().splitlines()[-1:] or [""]
            pid_file = log.with_suffix(".pid")
            state = "no pid"
            if pid_file.exists():
                try:
                    os.kill(int(pid_file.read_text().strip()), 0)
                    state = "running"
                except (OSError, ValueError):
                    state = "stopped"
            print(f"  {log.name} [{state}]: {last[0][:90]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
