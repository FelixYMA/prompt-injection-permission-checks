"""Agent loop shared by all three conditions.

The loop alternates model turns and tool executions until the model replies
without a tool call or a limit is reached. Conditions A and B execute every
well-formed call. Condition C first passes each call to the permission
checker; a denied call is not executed and the model receives an error
message instead. Denied and malformed calls count toward the tool-call limit.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple

from .env import TOOL_SCHEMAS, Workspace
from .llm import Backend
from .permissions import Permissions, check, denial_message
from .prompts import CONDITIONS, system_prompt

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_CALL_RE = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL)


@dataclass(frozen=True)
class AgentConfig:
    max_tool_calls: int = 8
    max_new_tokens_per_turn: int = 1024
    max_total_new_tokens: int = 4096
    timeout_s: float = 300.0


def parse_tool_calls(text: str) -> Tuple[str, List[Dict[str, Any]]]:
    """Extract tool calls from model output.

    Returns the text outside tool-call blocks and a list of calls. Each call is
    ``{"name", "arguments", "error"}``; ``error`` is set when the block is not
    a valid call, in which case name and arguments may be ``None``.
    """
    calls: List[Dict[str, Any]] = []
    for match in _CALL_RE.finditer(text):
        calls.append(_parse_one(match.group(1)))
    remainder = _CALL_RE.sub("", text)
    if "<tool_call>" in remainder:
        # An opened but unclosed block, usually from hitting the token limit.
        head = remainder.partition("<tool_call>")[0]
        calls.append({"name": None, "arguments": None, "error": "unterminated tool call"})
        remainder = head
    return remainder.strip(), calls


def _parse_one(body: str) -> Dict[str, Any]:
    try:
        obj = json.loads(body.strip())
    except json.JSONDecodeError as exc:
        return {"name": None, "arguments": None, "error": f"invalid JSON ({exc.msg})"}
    if not isinstance(obj, dict) or not isinstance(obj.get("name"), str):
        return {"name": None, "arguments": None, "error": "missing tool name"}
    args = obj.get("arguments", {})
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else {}
        except json.JSONDecodeError:
            return {"name": obj["name"], "arguments": args, "error": "arguments are not JSON"}
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return {"name": obj["name"], "arguments": args, "error": "arguments must be an object"}
    return {"name": obj["name"], "arguments": args, "error": None}


def strip_thinking(text: str) -> str:
    text = _THINK_RE.sub("", text)
    if "</think>" in text:  # opening tag was part of the prompt
        text = text.split("</think>", 1)[1]
    return text.strip()


def run_episode(case: Dict[str, Any], condition: str, seed: int, backend: Backend,
                config: AgentConfig) -> Dict[str, Any]:
    if condition not in CONDITIONS:
        raise ValueError(condition)
    workspace = Workspace.from_case(case)
    perms = Permissions.from_case(case)
    enforce = condition == "C"

    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": system_prompt(case, condition)},
        {"role": "user", "content": case["request"]},
    ]
    backend.begin_episode(case, condition, seed)

    turns: List[Dict[str, Any]] = []
    calls: List[Dict[str, Any]] = []
    total_completion = 0
    total_prompt = 0
    stop_reason: Optional[str] = None
    final_message = ""
    start = time.monotonic()

    while stop_reason is None:
        if time.monotonic() - start > config.timeout_s:
            stop_reason = "timeout"
            break
        budget = config.max_total_new_tokens - total_completion
        if budget <= 0:
            stop_reason = "token_limit"
            break

        t0 = time.monotonic()
        gen = backend.generate(messages, TOOL_SCHEMAS, min(config.max_new_tokens_per_turn, budget))
        elapsed = time.monotonic() - t0
        total_completion += gen.completion_tokens
        total_prompt += gen.prompt_tokens

        text = strip_thinking(gen.text)
        content, parsed = parse_tool_calls(text)
        messages.append({"role": "assistant", "content": text})
        turn = {
            "turn": len(turns),
            "raw_output": gen.text,
            "finish_reason": gen.finish_reason,
            "prompt_tokens": gen.prompt_tokens,
            "completion_tokens": gen.completion_tokens,
            "seconds": round(elapsed, 3),
            "call_indices": [],
        }
        turns.append(turn)

        if not parsed:
            final_message = content
            stop_reason = "final_answer" if gen.finish_reason == "stop" else "truncated"
            break

        for call in parsed:
            if len(calls) >= config.max_tool_calls:
                stop_reason = "tool_limit"
                break
            record: Dict[str, Any] = {
                "index": len(calls),
                "turn": turn["turn"],
                "name": call["name"],
                "arguments": call["arguments"],
                "parse_error": call["error"],
                "status": None,
                "denial_reason": None,
                "result": None,
                "effects": [],
            }
            if call["error"] is not None:
                record["status"] = "parse_error"
                record["result"] = f"Error: could not parse tool call: {call['error']}."
            else:
                if enforce:
                    decision = check(call["name"], call["arguments"], perms)
                    if not decision.allowed:
                        record["status"] = "denied"
                        record["denial_reason"] = decision.reason
                        record["result"] = denial_message(decision)
                if record["status"] is None:
                    result = workspace.execute(call["name"], call["arguments"])
                    record["status"] = "executed_ok" if result.ok else "executed_error"
                    record["result"] = result.output
                    record["effects"] = [asdict(e) for e in result.effects]
            calls.append(record)
            turn["call_indices"].append(record["index"])
            messages.append({"role": "tool", "content": record["result"]})

    return {
        "case_id": case["case_id"],
        "condition": condition,
        "seed": seed,
        "case": case,
        "config": asdict(config),
        "backend": backend.describe(),
        "system_prompt": messages[0]["content"],
        "turns": turns,
        "calls": calls,
        "sent": workspace.sent,
        "final_message": final_message,
        "stop_reason": stop_reason,
        "n_tool_calls": len(calls),
        "prompt_tokens": total_prompt,
        "completion_tokens": total_completion,
        "seconds": round(time.monotonic() - start, 3),
    }
