"""Static configuration for driving the customized AFL++/IJON install.

The one non-obvious bit is AFL_PATH: the afl-cc wrapper force-includes
afl-ijon-min.h via find_object(), which only searches $AFL_PATH, the argv0
dir, and ../lib/afl. The header lives in include/, so AFL_PATH must point at
$AFL_ROOT/include or IJON_SET/etc. silently compile out (see memory:
aflpp-ijon-build).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_AFL_ROOT = Path(os.environ.get("AFL_ROOT", "/opt/AFLplusplus"))


@dataclass(frozen=True)
class AflConfig:
    afl_root: Path = DEFAULT_AFL_ROOT

    @property
    def afl_fuzz(self) -> Path:
        return self.afl_root / "afl-fuzz"

    @property
    def afl_clang_fast(self) -> Path:
        return self.afl_root / "afl-clang-fast"

    @property
    def header_dir(self) -> Path:
        """Goes into AFL_PATH so the IJON header is found at compile time."""
        return self.afl_root / "include"

    def check(self) -> None:
        for p in (self.afl_fuzz, self.afl_clang_fast, self.header_dir):
            if not p.exists():
                raise FileNotFoundError(f"AFL++ component missing: {p}")

    def build_env(self, ijon: bool) -> dict[str, str]:
        env = dict(os.environ)
        env["AFL_PATH"] = str(self.header_dir)
        if ijon:
            env["AFL_LLVM_IJON"] = "1"
        else:
            env.pop("AFL_LLVM_IJON", None)
        return env

    def run_env(self, stop_on_crash: bool = False) -> dict[str, str]:
        env = dict(os.environ)
        env["PATH"] = f"{self.afl_root}:{env.get('PATH', '')}"
        env["AFL_PATH"] = str(self.header_dir)
        env["AFL_SKIP_CPUFREQ"] = "1"
        env["AFL_I_DONT_CARE_ABOUT_MISSING_CRASHES"] = "1"
        env["AFL_NO_UI"] = "1"
        if stop_on_crash:
            env["AFL_BENCH_UNTIL_CRASH"] = "1"
        return env
