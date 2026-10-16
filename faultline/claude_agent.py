"""Claude provider for the agent: the same graph Toolbox, driven by the Anthropic SDK tool runner.
Chosen automatically when ANTHROPIC_API_KEY (or an `ant auth login` profile) is available."""
from __future__ import annotations

import functools
import os

import anthropic
from anthropic import beta_tool

from . import config

DEFAULT_MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")


def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def _as_tool(method):
    """Expose a bound Toolbox method as a @beta_tool: the wrapper keeps its signature and docstring (the schema source)."""
    @functools.wraps(method)
    def tool(**kwargs):
        return method(**kwargs)
    return beta_tool(tool)


class ClaudeChat:
    def __init__(self, box, system: str, model: str = DEFAULT_MODEL) -> None:
        self.client = anthropic.Anthropic(timeout=120.0)
        self.model = model
        self.system = system
        self.tools = [_as_tool(m) for m in box._raw_tools()]
        self.messages: list[dict] = []

    def send(self, text: str) -> str:
        self.messages.append({"role": "user", "content": text})
        kwargs = dict(model=self.model, max_tokens=16000, system=self.system, tools=self.tools, messages=self.messages,
                      output_config={"effort": "medium"}, betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        try:
            runner = self.client.beta.messages.tool_runner(**kwargs)
        except TypeError:  # older SDK without the fallbacks parameter
            kwargs.pop("betas"); kwargs.pop("fallbacks")
            runner = self.client.beta.messages.tool_runner(**kwargs)
        last = None
        for message in runner:  # mirror the history so the next turn continues the conversation
            last = message
            self.messages.append({"role": "assistant", "content": message.content})
            tool_response = runner.generate_tool_call_response()
            if tool_response is not None:
                self.messages.append(tool_response)
        if last is None:
            return ""
        if last.stop_reason == "refusal":
            return "The model declined this request" + (f" ({last.stop_details.category})" if getattr(last, "stop_details", None) else "") + "."
        return "".join(b.text for b in last.content if b.type == "text")
