"""Structured logging — dual output to stderr and log file."""

import sys
from datetime import datetime
from pathlib import Path


class ChaseLogger:
    def __init__(self, log_dir: Path):
        self._log_dir = log_dir

    @property
    def _log_file(self) -> Path:
        return self._log_dir / f"{datetime.now().strftime('%Y-%m-%d')}.log"

    def _emit(self, level: str, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {level}: {msg}"
        try:
            with open(self._log_file, "a") as f:
                f.write(line + "\n")
        except OSError:
            pass
        print(line, file=sys.stderr)

    def info(self, msg: str) -> None:
        self._emit("INFO", msg)

    def error(self, msg: str) -> None:
        self._emit("ERROR", msg)

    def sprint(self, sprint_id: int, phase: str, msg: str) -> None:
        line = f"[Sprint {sprint_id}/{phase}] {msg}"
        self.info(line)
        # Also write to sprint-specific log file
        sprint_log = self._log_dir / f"sprint-{sprint_id}.log"
        ts = datetime.now().strftime("%H:%M:%S")
        try:
            with open(sprint_log, "a") as f:
                f.write(f"[{ts}] INFO: {line}\n")
        except OSError:
            pass
