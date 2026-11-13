"""Spending cap.

An agent under injection can loop and burn credit, so each call is checked
before it spends. The spend is kept in a JSON file, so the cap survives restarts
and covers all runs. Two workers share the file, so every read and write uses
`flock`.
"""

from __future__ import annotations

import fcntl
import json
import os
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from pathlib import Path


class BudgetExceeded(RuntimeError):
    """A call would pass the cap."""


class LedgerCorrupted(RuntimeError):
    """The spend file exists but cannot be read."""


@dataclass
class Ledger:
    spent_usd: float = 0.0
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0


class Budget:
    """Spend counter with a hard cap, stored as JSON."""

    def __init__(self, path: str | Path, cap_usd: float,
                 cap_var: str = "TCC_BUDGET_CAP_USD") -> None:
        self.path = Path(path)
        self.cap_usd = float(cap_usd)
        self.cap_var = cap_var  # named in the error message
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # A separate lock file: `_save` replaces the JSON file's inode.
        self.lock_path = self.path.with_suffix(".lock")
        self.lock_path.touch(exist_ok=True)
        with self._file_lock(exclusive=False):
            self.ledger = self._load()

    @contextmanager
    def _file_lock(self, exclusive: bool = True):
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _load(self) -> Ledger:
        if not self.path.exists():
            return Ledger()
        try:
            return Ledger(**json.loads(self.path.read_text()))
        except (json.JSONDecodeError, TypeError) as e:
            # Stop: an empty ledger would erase the spend and disable the cap.
            raise LedgerCorrupted(
                f"Cannot read {self.path} ({e}). Fix or delete the file before "
                f"continuing."
            ) from e

    def _save(self) -> None:
        # Unique temp name per process, so two writers cannot publish bad JSON.
        tmp = self.path.with_name(
            f"{self.path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
        tmp.write_text(json.dumps(asdict(self.ledger), indent=2))
        os.replace(tmp, self.path)

    def check(self, projected_usd: float = 0.0) -> None:
        """Raise BudgetExceeded if the projected spend passes the cap."""
        # Read the file again to see what the other worker has spent.
        with self._lock, self._file_lock(exclusive=False):
            self.ledger = self._load()
        if self.ledger.spent_usd + projected_usd > self.cap_usd:
            raise BudgetExceeded(
                f"Cap of ${self.cap_usd:.2f} reached. "
                f"Spent ${self.ledger.spent_usd:.4f}, "
                f"projected +${projected_usd:.4f}. "
                f"Raise {self.cap_var} if you want to continue."
            )

    def record(self, usd: float, prompt_tokens: int, completion_tokens: int) -> None:
        """Add a call to the ledger, atomically across processes."""
        with self._lock, self._file_lock(exclusive=True):
            current = self._load()
            current.spent_usd += usd
            current.calls += 1
            current.prompt_tokens += prompt_tokens
            current.completion_tokens += completion_tokens
            self.ledger = current
            self._save()

    def summary(self) -> str:
        led = self.ledger
        return (
            f"spent ${led.spent_usd:.4f} of ${self.cap_usd:.2f} "
            f"({led.calls} calls, {led.prompt_tokens:,} input tokens, "
            f"{led.completion_tokens:,} output tokens)"
        )


# One ledger and one cap per provider. The Anthropic cap defaults to 0, so Claude
# spends nothing until TCC_BUDGET_CAP_ANTHROPIC_USD is set.
LEDGERS = {
    "deepinfra": ("spend-deepinfra.json", "TCC_BUDGET_CAP_USD", "5.0"),
    "anthropic": ("spend-anthropic.json", "TCC_BUDGET_CAP_ANTHROPIC_USD", "0"),
}


def from_env(results_dir: str | Path = "results",
             provider: str = "deepinfra") -> Budget:
    name, var, default = LEDGERS[provider]
    cap = float(os.environ.get(var) or default)
    return Budget(Path(results_dir) / name, cap, cap_var=var)
