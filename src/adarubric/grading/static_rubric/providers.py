"""The three judge APIs — gemini / anthropic / openai — and how a provider gets picked.

Ported from skillgrade: same env var names, same request shapes, temperature 0, same default
models. Key lookup checks the run's env first (what ``--env-file`` loaded), then the process
environment — the same precedence skillgrade uses.

Provider choice when the task doesn't name one: whichever key is present, gemini first —
GEMINI_API_KEY, then ANTHROPIC_API_KEY, then OPENAI_API_KEY.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

#: skillgrade's defaults, verbatim. Override per grader with ``model:``.
DEFAULT_MODELS = {
    "gemini": "gemini-3-flash-preview",
    "anthropic": "claude-sonnet-4-20250514",
    "openai": "gpt-4o",
    "together": "Qwen/Qwen2.5-72B-Instruct-Turbo",  # override per grader with `model:`
}

_KEY_FOR = {
    "gemini": "GEMINI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "together": "TOGETHER_API_KEY",  # OpenAI-compatible: https://api.together.xyz/v1
}

#: The order a provider is auto-picked in when none is configured. Gemini first, like skillgrade.
PROVIDER_ORDER = ("gemini", "anthropic", "openai", "together")

#: Generous on purpose: judge calls carry whole session transcripts, and flash-class models can
#: take minutes on a big one. A timeout here costs a whole verdict, so patience is cheaper.
_TIMEOUT_S = 240


class JudgeError(Exception):
    """The judge itself couldn't run (missing key, network, HTTP error) — a grading error, not a 0."""


def _key(provider: str, env: dict[str, str] | None) -> str | None:
    # JUDGE_API_KEY is the judge's OWN key — it wins, so judging can bill a different account
    # than the agent, even on the same provider. Else the provider's normal key name.
    # Deliberately env-only, NO os.environ fallback: every key the judge uses must have been handed
    # over explicitly (the CLI builds that env from --env-file, then the shell). This is what makes
    # it impossible for a test run or a library call to silently pick up someone's shell key and
    # spend money on it.
    e = env or {}
    return e.get("JUDGE_API_KEY") or e.get(_KEY_FOR[provider])


def pick_provider(configured: str | None, env: dict[str, str] | None) -> str | None:
    """The provider that judges: explicit config > the JUDGE_LLM_PROVIDER env var > the first
    provider with a key available > None.

    JUDGE_LLM_PROVIDER (+ JUDGE_API_KEY, optional JUDGE_MODEL) selects the judge INDEPENDENTLY
    of which agent runs the task — e.g. agent on Together, judge on gemini, or the reverse."""
    e = env or {}
    chosen = configured or e.get("JUDGE_LLM_PROVIDER")
    if chosen:
        return chosen.strip().lower()
    return next((p for p in PROVIDER_ORDER if _key(p, env)), None)


def call_judge(provider: str, model: str, prompt: str, env: dict[str, str] | None) -> str:
    """Send the prompt, return the judge's raw text reply. Raises JudgeError on any failure."""
    if provider not in _KEY_FOR:
        raise JudgeError(f'Unknown grader provider: "{provider}". Supported: gemini, anthropic, openai, together')
    api_key = _key(provider, env)
    if not api_key:
        raise JudgeError(
            f"Missing {_KEY_FOR[provider]}. Set it (e.g. in your --env-file) to use the "
            f'"{provider}" judge.'
        )
    if provider == "gemini":
        return _gemini(model, prompt, api_key)
    if provider == "anthropic":
        return _anthropic(model, prompt, api_key, env)
    if provider == "together":
        return _together(model, prompt, api_key, env)
    return _openai(model, prompt, api_key, env)


def _post(url: str, headers: dict[str, str], body: dict) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:  # noqa: S310 - https APIs
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:  # noqa: BLE001
            pass
        raise JudgeError(f"judge API returned HTTP {e.code}: {detail}") from e
    except Exception as e:  # noqa: BLE001 - URLError, timeout, bad JSON
        raise JudgeError(f"judge API error: {e}") from e


def _gemini(model: str, prompt: str, api_key: str) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    data = _post(url, {}, {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0},
    })
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as e:
        raise JudgeError(f"gemini reply had no text: {json.dumps(data)[:300]}") from e


def _base_url(env: dict[str, str] | None, name: str, default: str) -> str:
    return ((env or {}).get(name) or os.environ.get(name) or default).rstrip("/")


def _anthropic(model: str, prompt: str, api_key: str, env: dict[str, str] | None) -> str:
    base = _base_url(env, "ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1")
    data = _post(f"{base}/messages",
                 {"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                 {"model": model, "max_tokens": 4096,
                  "messages": [{"role": "user", "content": prompt}]})
    try:
        return data["content"][0]["text"]
    except (KeyError, IndexError, TypeError) as e:
        raise JudgeError(f"anthropic reply had no text: {json.dumps(data)[:300]}") from e


def _together(model: str, prompt: str, api_key: str, env: dict[str, str] | None) -> str:
    """Together is OpenAI-compatible; only the base URL and key differ."""
    base = _base_url(env, "TOGETHER_BASE_URL", "https://api.together.xyz/v1")
    data = _post(f"{base}/chat/completions",
                 {"Authorization": f"Bearer {api_key}"},
                 {"model": model, "temperature": 0, "max_tokens": 4096,
                  "messages": [{"role": "user", "content": prompt}]})
    try:
        text = data["choices"][0]["message"].get("content") or ""
    except (KeyError, IndexError, TypeError) as e:
        raise JudgeError(f"together reply had no text: {json.dumps(data)[:300]}") from e
    if not text:
        raise JudgeError(f"together reply had no text: {json.dumps(data)[:300]}")
    return text


def _openai(model: str, prompt: str, api_key: str, env: dict[str, str] | None) -> str:
    base = _base_url(env, "OPENAI_BASE_URL", "https://api.openai.com/v1")
    data = _post(f"{base}/chat/completions",
                 {"Authorization": f"Bearer {api_key}"},
                 {"model": model, "temperature": 0, "max_tokens": 4096,
                  "messages": [{"role": "user", "content": prompt}]})
    try:
        msg = data["choices"][0]["message"]
        text = msg.get("content") or msg.get("reasoning_content") or ""
    except (KeyError, IndexError, TypeError) as e:
        raise JudgeError(f"openai reply had no text: {json.dumps(data)[:300]}") from e
    if not text:
        raise JudgeError(f"openai reply had no text: {json.dumps(data)[:300]}")
    return text
