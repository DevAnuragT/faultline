"""OpenRouter provider: the same graph Toolbox over the OpenAI-compatible chat API with a manual tool loop.
Free tier (Oct 2026): 20 requests/min, 50/day, 1000/day once $10 of credits were ever bought; failed calls count."""
from __future__ import annotations

import inspect
import json

from openai import APIStatusError, OpenAI, RateLimitError

from . import config

MAX_ROUNDS = 8


def available() -> bool:
    return bool(config.OPENROUTER_API_KEY)


def tool_schema(method) -> dict:
    """OpenAI-style function schema from a Toolbox method: every parameter is a string; the docstring is the description."""
    sig = inspect.signature(method)
    props, required = {}, []
    for name, p in sig.parameters.items():
        props[name] = {"type": "string"}
        if p.default is inspect.Parameter.empty:
            required.append(name)
    return {"type": "function", "function": {"name": method.__name__, "description": " ".join((method.__doc__ or "").split()),
                                             "parameters": {"type": "object", "properties": props, "required": required}}}


class OpenRouterChat:
    def __init__(self, box, system: str, model: str | None = None) -> None:
        self.client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=config.OPENROUTER_API_KEY, timeout=90.0, max_retries=1,
                             default_headers={"HTTP-Referer": "https://github.com/DevAnuragT/faultline", "X-Title": "Faultline"})
        self.model = model or config.OPENROUTER_MODEL
        self.tools = {m.__name__: m for m in box.tools()}
        self.schemas = [tool_schema(m) for m in box._raw_tools()]
        self.messages: list[dict] = [{"role": "system", "content": system}]

    def send(self, text: str) -> str:
        self.messages.append({"role": "user", "content": text})
        for _ in range(MAX_ROUNDS):
            resp = self.client.chat.completions.create(model=self.model, messages=self.messages, tools=self.schemas, tool_choice="auto", temperature=0)
            msg = resp.choices[0].message
            self.messages.append({"role": "assistant", "content": msg.content or "", "tool_calls": [tc.model_dump() for tc in msg.tool_calls] if msg.tool_calls else None})
            if not msg.tool_calls:
                return msg.content or ""
            for tc in msg.tool_calls:
                fn = self.tools.get(tc.function.name)
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = fn(**args) if fn else {"error": f"unknown tool {tc.function.name}"}
                self.messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result, default=str)[:60000]})
        return "I ran out of tool-call rounds before finishing; the calls made so far are shown above."


def is_quota_error(exc: Exception) -> bool:
    return isinstance(exc, RateLimitError) or (isinstance(exc, APIStatusError) and exc.status_code in (402, 429))
