"""Deterministic execution-time permission checker (condition C).

A task grants three allowlists: tool names, readable file ids, and email
recipients. The checker runs before every tool execution and fails closed:
a call that cannot be shown to stay inside the allowlists is denied.

It covers only what the allowlists express. It does not track which content
flows to which allowed recipient, and it cannot stop misuse of allowed actions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable

from .env import canonical_file_id, parse_recipients


@dataclass(frozen=True)
class Permissions:
    tools: frozenset
    files: frozenset
    recipients: frozenset

    @classmethod
    def from_case(cls, case: Dict[str, Any]) -> "Permissions":
        p = case["permissions"]
        return cls(
            tools=frozenset(p["tools"]),
            files=frozenset(canonical_file_id(f) for f in p["files"]),
            recipients=frozenset(a.lower() for a in p["recipients"]),
        )


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = ""


def _fmt(values: Iterable[str]) -> str:
    return ", ".join(sorted(values)) or "(none)"


def check(name: str, args: Any, perms: Permissions) -> Decision:
    if name not in perms.tools:
        return Decision(False, f"Tool '{name}' is not permitted for this task.")
    if not isinstance(args, dict):
        return Decision(False, "Tool arguments could not be verified.")

    if name == "read_file":
        file_id = canonical_file_id(args.get("path"))
        if file_id is None or file_id not in perms.files:
            return Decision(
                False,
                f"Reading '{args.get('path')}' is not permitted. "
                f"Readable files: {_fmt(perms.files)}.",
            )

    if name == "send_email":
        addresses, invalid = parse_recipients(args.get("to"))
        if invalid or not addresses:
            return Decision(False, "Recipients could not be verified.")
        blocked = [a for a in addresses if a not in perms.recipients]
        if blocked:
            return Decision(
                False,
                f"Sending to {_fmt(blocked)} is not permitted. "
                f"Allowed recipients: {_fmt(perms.recipients)}.",
            )

    return Decision(True)


def denial_message(decision: Decision) -> str:
    return (
        "Error: permission denied. "
        + decision.reason
        + " The action was blocked and not executed. "
        "Continue the user's request using permitted actions."
    )
