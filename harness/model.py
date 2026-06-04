"""Thin LiteLLM wrapper for the DeepSeek 'analyst' model.

Used at exactly the judgment points of the loop (classify why-stuck +
synthesize annotation). Everything mechanical stays in pure Python.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import litellm

# v4-pro is a reasoning model (separate reasoning_content) and stronger at the
# placement/primitive-selection judgment than v4-flash; it supports JSON mode.
# Reasoning consumes output tokens, so max_tokens must leave headroom.
DEFAULT_MODEL = "deepseek/deepseek-v4-pro"

# Where to look for the API key, in order. The sibling project already holds a
# working DeepSeek key; we read it rather than duplicating the secret.
_ENV_FALLBACKS = [
    Path(__file__).resolve().parents[1] / ".env",
    Path(__file__).resolve().parents[2] / "vuln_analysis_6step" / ".env",
]


def load_api_key() -> str:
    if os.environ.get("DEEPSEEK_API_KEY"):
        return os.environ["DEEPSEEK_API_KEY"]
    for env_path in _ENV_FALLBACKS:
        if not env_path.exists():
            continue
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("DEEPSEEK_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
                if key:
                    os.environ["DEEPSEEK_API_KEY"] = key
                    return key
    raise RuntimeError(
        "DEEPSEEK_API_KEY not found in env or any .env fallback; "
        "set it or copy ijon-llm/.env.example to .env")


@dataclass
class LLMResult:
    text: str
    obj: Optional[dict]
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_s: float
    reasoning: str = ""
    raw: object = field(repr=False, default=None)


class AnalystModel:
    def __init__(self, model: str = None, temperature: float = 0.0):
        self.model = model or os.environ.get("IJON_LLM_MODEL", DEFAULT_MODEL)
        self.temperature = temperature
        load_api_key()

    def complete_json(self, system: str, user: str, max_tokens: int = 4096,
                      retries: int = 1) -> LLMResult:
        """Call the model in JSON mode; parse the response into a dict.
        Retries once with a corrective nudge if JSON parsing fails."""
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": user}]
        last_err = None
        for attempt in range(retries + 1):
            t = time.time()
            resp = litellm.completion(
                model=self.model, messages=messages,
                temperature=self.temperature, max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
            dt = time.time() - t
            msg = resp.choices[0].message
            text = msg.content or ""
            reasoning = getattr(msg, "reasoning_content", None) or ""
            try:
                obj = json.loads(text)
            except json.JSONDecodeError as e:
                last_err = e
                obj = None
                messages.append({"role": "assistant", "content": text})
                messages.append({"role": "user", "content":
                    "That was not valid JSON. Reply with ONLY a single valid "
                    "JSON object, no prose, no markdown fences."})
                if attempt < retries:
                    continue
            return LLMResult(
                text=text, obj=obj, model=resp.model,
                prompt_tokens=resp.usage.prompt_tokens,
                completion_tokens=resp.usage.completion_tokens,
                latency_s=dt, reasoning=reasoning, raw=resp,
            )
        raise RuntimeError(f"model returned unparseable JSON: {last_err}")
