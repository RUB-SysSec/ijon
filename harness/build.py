"""Patch a target's source with an IJON annotation and (re)compile it.

This is the mechanical core of the analyst loop: in M3 the LLM produces an
Annotation; here we apply it and rebuild. For M2 we drive it by hand to prove
the strip -> patch -> build -> run pipeline end to end.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import AflConfig

# Matches `#ifdef IJON_SET ... #endif` (and _USE_IJON) so we can produce a
# "clean" unannotated source to hand the LLM.
_IFDEF_BLOCK = re.compile(
    r"^[ \t]*#if(?:def)?\s+(?:IJON_\w+|_USE_IJON).*?^[ \t]*#endif.*?$\n?",
    re.MULTILINE | re.DOTALL,
)


def strip_ijon_blocks(source: str) -> str:
    """Remove existing #ifdef IJON_* annotation blocks from source text."""
    return _IFDEF_BLOCK.sub("", source)


def redact_ijon_hints(source: str) -> str:
    """Drop any residual line that mentions 'ijon' (case-insensitive) — leaky
    comments, qemu-config examples, or stray forward declarations of ijon_*
    helpers. Run AFTER strip_ijon_blocks so the source shown to the model never
    names the IJON answer. The model still learns the IJON API legitimately
    from its own prompt reference, not from the target."""
    return "".join(l for l in source.splitlines(keepends=True)
                   if "ijon" not in l.lower())


def make_clean_source(source: str) -> str:
    """Produce the answer-free source to hand the analyst model."""
    clean = redact_ijon_hints(strip_ijon_blocks(source))
    if "ijon" in clean.lower():
        raise AssertionError("clean source still mentions IJON after redaction")
    return clean


@dataclass
class Annotation:
    """An annotation to insert. Placement by substring (preferred, robust) or
    1-based line number. `code` is the C statement(s), no indentation needed."""
    code: str
    after_substring: Optional[str] = None
    after_line: Optional[int] = None
    indent: str = "    "

    def __post_init__(self):
        if (self.after_substring is None) == (self.after_line is None):
            raise ValueError("specify exactly one of after_substring/after_line")


def apply_annotation(source: str, ann: Annotation) -> str:
    lines = source.splitlines(keepends=True)
    if ann.after_line is not None:
        idx = ann.after_line  # insert *after* this 1-based line
    else:
        idx = None
        for i, line in enumerate(lines):
            if ann.after_substring in line:
                idx = i + 1
                break
        if idx is None:
            raise ValueError(f"anchor substring not found: {ann.after_substring!r}")
    nl = "\n"
    block = "".join(f"{ann.indent}{c}{nl}" for c in ann.code.splitlines())
    lines.insert(idx, block)
    return "".join(lines)


@dataclass
class CompileResult:
    ok: bool
    binary: Path
    ijon: bool
    cmd: list[str]
    stdout: str

    @property
    def header_included(self) -> bool:
        return "Including IJON header" in self.stdout

    @property
    def header_missing(self) -> bool:
        return "IJON header not found" in self.stdout


class Builder:
    def __init__(self, config: AflConfig):
        self.config = config

    def compile(self, source: Path, out_binary: Path, ijon: bool,
                fsanitize_fuzzer: bool = True) -> CompileResult:
        out_binary.parent.mkdir(parents=True, exist_ok=True)
        cmd = [str(self.config.afl_clang_fast)]
        if fsanitize_fuzzer:
            cmd += ["-fsanitize=fuzzer"]
        cmd += ["-o", str(out_binary), str(source)]
        env = self.config.build_env(ijon=ijon)
        if ijon:
            env["AFL_DEBUG"] = "1"  # makes the wrapper print header inclusion
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
        combined = proc.stdout + proc.stderr
        return CompileResult(
            ok=(proc.returncode == 0 and out_binary.exists()),
            binary=out_binary, ijon=ijon, cmd=cmd, stdout=combined,
        )
