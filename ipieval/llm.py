"""Text-generation backends.

``MLXBackend`` runs a local model with MLX-LM. ``ScriptedBackend`` replays a
fixed policy and is used to test the harness without a model.

A backend receives the full chat history on every turn and returns raw text.
Tool calls are written by the model in the Hermes/Qwen format::

    <tool_call>
    {"name": "read_file", "arguments": {"path": "notes/a.txt"}}
    </tool_call>
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional


@dataclass
class Generation:
    text: str
    prompt_tokens: int
    completion_tokens: int
    finish_reason: str  # "stop" or "length"


class Backend:
    name = "base"

    def begin_episode(self, case: Dict[str, Any], condition: str, seed: int) -> None:
        raise NotImplementedError

    def generate(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]],
                 max_tokens: int) -> Generation:
        raise NotImplementedError

    def describe(self) -> Dict[str, Any]:
        return {"backend": self.name}


class MLXBackend(Backend):
    name = "mlx"

    def __init__(self, model: str, revision: Optional[str], temperature: float, top_p: float,
                 top_k: int, enable_thinking: bool):
        import mlx.core as mx
        from mlx_lm import load
        from mlx_lm.sample_utils import make_sampler

        path = model
        if revision:
            from huggingface_hub import snapshot_download

            path = snapshot_download(model, revision=revision)
        self._mx = mx
        self.model, self.tokenizer = load(path)
        self.model_id = model
        self.revision = revision
        self.temperature = temperature
        self.top_p = top_p
        self.top_k = top_k
        self.enable_thinking = enable_thinking
        self._sampler = make_sampler(temp=temperature, top_p=top_p, top_k=top_k)

    def begin_episode(self, case, condition, seed):
        self._mx.random.seed(seed)

    def render(self, messages, tools) -> str:
        return self.tokenizer.apply_chat_template(
            messages,
            tools=tools,
            add_generation_prompt=True,
            enable_thinking=self.enable_thinking,
            tokenize=False,
        )

    def generate(self, messages, tools, max_tokens):
        from mlx_lm import stream_generate

        prompt = self.tokenizer.encode(self.render(messages, tools), add_special_tokens=False)
        pieces: List[str] = []
        last = None
        for response in stream_generate(self.model, self.tokenizer, prompt,
                                        max_tokens=max_tokens, sampler=self._sampler):
            pieces.append(response.text)
            last = response
        if last is None:
            return Generation("", len(prompt), 0, "length")
        return Generation(
            text="".join(pieces),
            prompt_tokens=last.prompt_tokens,
            completion_tokens=last.generation_tokens,
            finish_reason=last.finish_reason or "length",
        )

    def describe(self):
        return {
            "backend": self.name,
            "model": self.model_id,
            "revision": self.revision,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "top_k": self.top_k,
            "enable_thinking": self.enable_thinking,
        }


def format_tool_call(name: str, arguments: Dict[str, Any]) -> str:
    return "<tool_call>\n" + json.dumps({"name": name, "arguments": arguments}) + "\n</tool_call>"


class ScriptedBackend(Backend):
    """Replays a scripted policy instead of sampling from a model.

    ``factory(case, condition)`` is called at the start of each episode and
    must return an object with a ``next(messages) -> str`` method. Token
    counts are whitespace word counts.
    """

    name = "scripted"

    def __init__(self, factory: Callable[[Dict[str, Any], str], Any], label: str = "scripted"):
        self.factory = factory
        self.label = label
        self._policy: Any = None

    def begin_episode(self, case, condition, seed):
        self._policy = self.factory(case, condition)

    def generate(self, messages, tools, max_tokens):
        if self._policy is None:
            raise RuntimeError("begin_episode was not called")
        text = self._policy.next(messages)
        prompt_words = sum(len(str(m["content"]).split()) for m in messages)
        return Generation(text, prompt_tokens=prompt_words,
                          completion_tokens=len(text.split()), finish_reason="stop")

    def describe(self):
        return {"backend": self.name, "policy": self.label}
