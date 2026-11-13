"""Campaign grid, episode file names and resume logic.

The results folder is the checkpoint: an episode whose file exists is not run
again. Standard library only, because show_progress.py runs on the host.

Episode file names and JSON fields are in Portuguese because the recorded
episodes use them (see results/README.md).
"""

from __future__ import annotations

import glob
import os
from collections import namedtuple
from pathlib import Path

# (user tasks, injection tasks) per suite. Task ids are not contiguous because
# they come from `list(suite.user_tasks)[:N]`, which follows AgentDojo's
# registration order.
GRID = {
    "workspace": (5, 3),
    "slack": (3, 2),
    "banking": (3, 2),
    "travel": (3, 2),
}

CAMPAIGN_MODELS = ["qwen3-32b", "deepseek-flash", "gpt-oss-120b", "claude-sonnet-5"]

# The 6 injection attacks of AgentDojo. The other 11 registered attacks are
# ablations, denial of service and one manual attack.
ATTACKS = ["important_instructions", "tool_knowledge", "system_message",
           "direct", "injecagent", "ignore_previous"]

TARGET_REPS = 10
EPISODES_PER_REP = sum(t * i for t, i in GRID.values()) * len(ATTACKS)  # 198

OUT = Path(os.environ.get("TCC_OUT_DIR") or "results/agentdojo-openclaw")

# Repetition 0 is a standalone run outside the campaign (`make openclaw`).
STANDALONE_REP = 0

# Verdict labels used in file names and in the episode JSON.
WON, BLOCKED, INVALID = "VENCEU", "bloqueado", "invalido"

Key = namedtuple("Key", "model rep suite attack task injection")


def _suffix(rep: int) -> str:
    return ".json" if rep == STANDALONE_REP else f"__rep{rep}.json"


def file_name(key: Key, verdict: str) -> str:
    return (f"{verdict}__openclaw__{key.model}__{key.suite}__{key.task}"
            f"__{key.injection}__{key.attack}{_suffix(key.rep)}")


def name_pattern(key: Key) -> str:
    """Glob that matches this episode with any verdict."""
    return (f"*__openclaw__{key.model}__{key.suite}__{key.task}"
            f"__{key.injection}__{key.attack}{_suffix(key.rep)}")


def subfolder(key: Key) -> Path:
    """`<model>/rep<NN>`. The leading zero makes `rep02` sort before `rep10`."""
    return Path(key.model) / f"rep{key.rep:02d}"


def episode_paths(root: Path | str) -> list[Path]:
    """All episode files under `root`, at any depth, sorted."""
    r = Path(root)
    if not r.is_dir():
        return []
    return sorted(r.rglob("*.json"))


def episode_path(key: Key, verdict: str) -> Path:
    """Episode path relative to OUT."""
    return subfolder(key) / file_name(key, verdict)


def parse_name(name: str) -> Key | None:
    """Get the key from a file name, or None if the name is not an episode.

    Format: verdict__openclaw__model__suite__task__injection__attack[__repN].json

    Paths are refused: a folder with `__` in its name would break the split,
    and the episode would count as not done and be paid for again.
    """
    if not name.endswith(".json"):
        return None
    if "/" in name or os.sep in name:
        return None
    parts = name[:-len(".json")].split("__")
    if len(parts) == 7:
        rep = STANDALONE_REP
    elif len(parts) == 8 and parts[7].startswith("rep"):
        try:
            rep = int(parts[7][3:])
        except ValueError:
            return None
    else:
        return None
    if parts[1] != "openclaw":
        return None
    return Key(model=parts[2], rep=rep, suite=parts[3],
               attack=parts[6], task=parts[4], injection=parts[5])


def done_keys(out_dir: Path | str = OUT) -> set[Key]:
    """Episodes already recorded. This set decides what is not paid for again."""
    return {k for k in (parse_name(p.name) for p in episode_paths(out_dir))
            if k is not None}


def remove_previous(key: Key, out_dir: Path | str = OUT) -> int:
    """Delete any file of this episode, with any verdict. Call before writing.

    The verdict is part of the name, so a rerun with another verdict would
    otherwise leave two files for one measurement.
    """
    n = 0
    for f in glob.glob(str(Path(out_dir) / "**" / name_pattern(key)), recursive=True):
        os.unlink(f)
        n += 1
    return n


def chunks(models=None, reps=None) -> list[tuple[str, int]]:
    """Units of work: one (model, repetition) pair runs in one short container."""
    models = CAMPAIGN_MODELS if models is None else models
    reps = range(1, TARGET_REPS + 1) if reps is None else reps
    return [(m, r) for r in reps for m in models]


# Approximate hours per chunk. Used only to balance the workers.
CHUNK_HOURS = {"qwen3-32b": 3.0, "deepseek-flash": 2.1,
               "gpt-oss-120b": 2.1, "claude-sonnet-5": 3.0}


def split_chunks(items, n: int) -> list[list]:
    """Split chunks among n workers by time: the longest chunk goes first, to
    the worker with the least load."""
    loads = [0.0] * n
    parts: list[list] = [[] for _ in range(n)]
    for chunk in sorted(items, key=lambda c: -CHUNK_HOURS.get(c[0], 1.0)):
        k = loads.index(min(loads))
        parts[k].append(chunk)
        loads[k] += CHUNK_HOURS.get(chunk[0], 1.0)
    return parts
