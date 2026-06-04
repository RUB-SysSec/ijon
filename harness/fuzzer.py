"""Launch/stop afl-fuzz and read its telemetry (fuzzer_stats, plot_data)."""
from __future__ import annotations

import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .config import AflConfig

# fuzzer_stats keys we care about and want as numbers (rest stay strings).
_NUMERIC_KEYS = {
    "run_time", "execs_done", "execs_per_sec", "corpus_count", "corpus_favored",
    "pending_favs", "pending_total", "cycles_done", "cycles_wo_finds",
    "time_wo_finds", "saved_crashes", "saved_hangs", "edges_found",
    "total_edges", "max_depth", "last_find", "last_crash",
}


def _coerce(key: str, val: str):
    if key not in _NUMERIC_KEYS:
        return val
    try:
        return int(val)
    except ValueError:
        try:
            return float(val)
        except ValueError:
            return val


def parse_fuzzer_stats(path: Path) -> dict:
    out: dict = {}
    for line in path.read_text().splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        out[key] = _coerce(key, val)
    return out


def parse_plot_data(path: Path) -> list[dict]:
    rows: list[dict] = []
    lines = [l for l in path.read_text().splitlines() if l.strip()]
    header: Optional[list[str]] = None
    for line in lines:
        if line.lstrip().startswith("#"):
            header = [c.strip() for c in line.lstrip("# ").split(",")]
            continue
        if header is None:
            continue
        cols = [c.strip() for c in line.split(",")]
        if len(cols) != len(header):
            continue
        rows.append(dict(zip(header, cols)))
    return rows


@dataclass
class Snapshot:
    """A point-in-time read of fuzzer_stats plus derived booleans."""
    stats: dict
    crashes_dir: Path

    @property
    def run_time(self) -> int:
        return int(self.stats.get("run_time", 0))

    @property
    def edges_found(self) -> int:
        return int(self.stats.get("edges_found", 0))

    @property
    def time_wo_finds(self) -> int:
        return int(self.stats.get("time_wo_finds", 0))

    @property
    def pending_favs(self) -> int:
        return int(self.stats.get("pending_favs", 0))

    @property
    def pending_total(self) -> int:
        return int(self.stats.get("pending_total", 0))

    @property
    def corpus_count(self) -> int:
        return int(self.stats.get("corpus_count", 0))

    @property
    def saved_crashes(self) -> int:
        return int(self.stats.get("saved_crashes", 0))

    @property
    def solved(self) -> bool:
        """For the maze, reaching the exit calls abort() -> a saved crash."""
        if self.saved_crashes > 0:
            return True
        return any(self.crashes_dir.glob("id:*"))


@dataclass
class RunResult:
    reason: str               # "predicate" | "timeout" | "exited"
    snapshot: Optional[Snapshot]
    plot: list[dict] = field(default_factory=list)
    log: str = ""


class FuzzerController:
    """Runs one afl-fuzz instance against a target and reads its telemetry."""

    def __init__(self, target: Path, input_dir: Path, output_dir: Path,
                 config: AflConfig, cwd: Optional[Path] = None,
                 stop_on_crash: bool = False):
        self.target = Path(target)
        self.input_dir = Path(input_dir)
        self.output_dir = Path(output_dir)
        self.config = config
        self.cwd = Path(cwd) if cwd else self.target.parent.parent
        self.stop_on_crash = stop_on_crash
        self._proc: Optional[subprocess.Popen] = None
        self._log_path = self.output_dir.parent / f"{self.output_dir.name}_fuzz.log"

    # --- telemetry paths (afl writes under <out>/default for a single fuzzer) ---
    @property
    def _stats_path(self) -> Path:
        return self.output_dir / "default" / "fuzzer_stats"

    @property
    def _plot_path(self) -> Path:
        return self.output_dir / "default" / "plot_data"

    @property
    def _crashes_dir(self) -> Path:
        return self.output_dir / "default" / "crashes"

    def snapshot(self) -> Optional[Snapshot]:
        if not self._stats_path.exists():
            return None
        return Snapshot(parse_fuzzer_stats(self._stats_path), self._crashes_dir)

    def plot(self) -> list[dict]:
        return parse_plot_data(self._plot_path) if self._plot_path.exists() else []

    def start(self) -> None:
        if self.output_dir.exists():
            import shutil
            shutil.rmtree(self.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        cmd = [str(self.config.afl_fuzz), "-i", str(self.input_dir),
               "-o", str(self.output_dir), "--", str(self.target)]
        self._logf = open(self._log_path, "w")
        self._proc = subprocess.Popen(
            cmd, cwd=str(self.cwd), env=self.config.run_env(self.stop_on_crash),
            stdout=self._logf, stderr=subprocess.STDOUT,
        )

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.send_signal(signal.SIGINT)
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
        if getattr(self, "_logf", None):
            self._logf.close()

    def run_until(self, predicate: Callable[[Snapshot], bool],
                  timeout: float, poll: float = 2.0) -> RunResult:
        """Start fuzzing; poll snapshots until predicate(snapshot) is True,
        the process exits, or timeout elapses. Always stops the fuzzer."""
        self.start()
        deadline = time.monotonic() + timeout
        last: Optional[Snapshot] = None
        reason = "timeout"
        try:
            while time.monotonic() < deadline:
                time.sleep(poll)
                last = self.snapshot()
                if last is not None and predicate(last):
                    reason = "predicate"
                    break
                if not self.is_running():
                    reason = "exited"
                    last = self.snapshot() or last
                    break
        finally:
            self.stop()
        return RunResult(reason=reason, snapshot=last, plot=self.plot(),
                         log=self._log_path.read_text() if self._log_path.exists() else "")
