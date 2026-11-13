#!/usr/bin/env python3
"""Check the recorded episodes. Spends nothing. Exit code 0 if all is well.

Checks:
  - every file name parses, and no episode appears twice
  - the grid is complete: EPISODES_PER_REP per (model, repetition)
  - the path agrees with the content: `<model>/rep<NN>/` against the JSON summary
It also lists invalid episodes, which stay out of the metrics.
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.campaign import (EPISODES_PER_REP, INVALID, OUT,  # noqa: E402
                              episode_paths, parse_name)


def main() -> int:
    files = episode_paths(OUT)
    if not files:
        print(f"ERROR: no episodes in {OUT}")
        return 1

    problems: list[str] = []
    print(f"Checking {OUT}")
    print(f"  files: {len(files):,}")

    keys = Counter()
    bad_names = []
    for p in files:
        key = parse_name(p.name)
        if key is None:
            bad_names.append(p.name)
        else:
            keys[key] += 1
    duplicates = [k for k, n in keys.items() if n > 1]
    print(f"  unique episodes: {len(keys):,}")
    if bad_names:
        problems.append(f"{len(bad_names)} file names do not parse: {bad_names[:3]}")
    if duplicates:
        problems.append(f"{len(duplicates)} duplicated episodes: {duplicates[:2]}")

    per_cell = defaultdict(int)
    for key in keys:
        per_cell[(key.model, key.rep)] += 1
    print(f"  (model, repetition) cells: {len(per_cell)}")
    incomplete = {k: n for k, n in per_cell.items() if n != EPISODES_PER_REP}
    if incomplete:
        problems.append(f"{len(incomplete)} cells without {EPISODES_PER_REP} episodes: "
                        f"{dict(list(incomplete.items())[:3])}")

    mismatched, invalid, unreadable = [], [], []
    for p in files:
        try:
            summary = json.loads(p.read_text(encoding="utf-8")).get("resumo") or {}
        except (json.JSONDecodeError, OSError):
            unreadable.append(p.name)
            continue
        if summary.get("veredito") == INVALID or summary.get("ok") is False:
            invalid.append(p.name)
        rel = p.relative_to(OUT).parts
        if len(rel) >= 2 and rel[1].startswith("rep"):
            if rel[0] != summary.get("modelo_key") or int(rel[1][3:]) != summary.get("repeticao"):
                mismatched.append(str(p.relative_to(OUT)))
    print(f"  path agrees with content: {len(files) - len(mismatched) - len(unreadable):,}")
    if unreadable:
        problems.append(f"{len(unreadable)} unreadable files: {unreadable[:3]}")
    if mismatched:
        problems.append(f"{len(mismatched)} files in the wrong folder: {mismatched[:2]}")
    if invalid:
        print(f"  invalid (out of the metrics): {len(invalid)}")

    print()
    if problems:
        print(f"{len(problems)} PROBLEM(S):")
        for x in problems:
            print(f"  {x}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
