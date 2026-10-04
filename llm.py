#!/usr/bin/env python3
"""The one place the DM talks to a language model.

`ask_for_tool_call` sends a system prompt, a user message and one tool
definition, and returns the arguments the model passed to that tool. Swapping
providers later (a local model, say) means adding a branch here and nothing
else.

Settings:
    DM_LLM_PROVIDER     "anthropic" (default) or "stub"
    ANTHROPIC_API_KEY   key for the Claude API
    DM_MODEL            model id (default claude-sonnet-5-5)
    DM_LLM_STUB         for the stub provider: a JSON file holding the tool arguments
                        to return, or a folder of <tool name>.json files, for
                        testing without a key
"""
import json
import os
import urllib.error
import urllib.request

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-sonnet-5-5"


class LLMError(Exception):
    pass


def ask_for_tool_call(system, user, tool, max_tokens=2048):
    """Return (tool_arguments, usage) where usage has input and output token counts."""
    provider = os.environ.get("DM_LLM_PROVIDER", "anthropic").lower()
    if provider == "stub":
        return _stub(tool)
    if provider == "anthropic":
        return _anthropic(system, user, tool, max_tokens)
    raise LLMError(f"unknown DM_LLM_PROVIDER '{provider}'")


def _stub(tool):
    path = os.environ.get("DM_LLM_STUB", "")
    if os.path.isdir(path):                 # a folder holds one canned answer per tool name
        path = os.path.join(path, tool["name"] + ".json")
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle), {"input_tokens": 0, "output_tokens": 0, "model": "stub"}
    except (OSError, json.JSONDecodeError) as error:
        raise LLMError(f"stub provider could not read DM_LLM_STUB: {error}") from None


def _anthropic(system, user, tool, max_tokens):
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise LLMError("ANTHROPIC_API_KEY is not set (see env.example)")
    model = os.environ.get("DM_MODEL", DEFAULT_MODEL)
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "tools": [tool],
        # "auto" on purpose: the current Sonnet and Opus models do not accept a
        # forced tool choice, so the system prompt asks for the call instead.
        "tool_choice": {"type": "auto", "disable_parallel_tool_use": True},
        "messages": [{"role": "user", "content": user}],
    }
    request = urllib.request.Request(
        os.environ.get("DM_ANTHROPIC_URL", ANTHROPIC_URL),   # override only for a proxy or a test
        data=json.dumps(body).encode("utf-8"),
        headers={"content-type": "application/json", "x-api-key": key,
                 "anthropic-version": ANTHROPIC_VERSION},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            data = json.load(response)
    except urllib.error.HTTPError as error:
        try:
            detail = json.load(error).get("error", {}).get("message", "")
        except (json.JSONDecodeError, AttributeError):
            detail = ""
        raise LLMError(f"Claude API returned HTTP {error.code}: {detail or error.reason}") from None
    except (urllib.error.URLError, OSError) as error:
        raise LLMError(f"cannot reach the Claude API: {getattr(error, 'reason', error)}") from None

    usage = dict(data.get("usage", {}), model=data.get("model", model))
    for block in data.get("content", []):
        if block.get("type") == "tool_use" and block.get("name") == tool["name"]:
            return block.get("input", {}), usage
    said = " ".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()
    raise LLMError("the model replied without calling the tool"
                   + (f": {said[:300]}" if said else f" (stop reason: {data.get('stop_reason')})"))
