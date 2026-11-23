"""Scripted agents for testing the harness end to end without a model.

``ScriptedAgent`` performs the task with the intended tool sequence. With
``obey_injection=True`` it also carries out the injected action, which lets
the tests check that violations are executed in A/B, blocked in C, and scored
correctly. After a denied send it retries with only the allowed recipients,
which exercises the recovery path.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .llm import ScriptedBackend, format_tool_call
from .tasks import DISTRACTOR_EMAILS

_DISTRACTOR_IDS = {e["email_id"] for e in DISTRACTOR_EMAILS}


class ScriptedAgent:
    def __init__(self, case: Dict[str, Any], condition: str, obey_injection: bool):
        self.case = case
        self.allowed = list(case["permissions"]["recipients"])
        injection = case.get("injection") if obey_injection else None
        self.queue: List[str] = []
        task_emails = [e["email_id"] for e in case["emails"] if e["email_id"] not in _DISTRACTOR_IDS]
        if task_emails:
            self.queue.append(format_tool_call("list_emails", {}))
            for email_id in task_emails:
                self.queue.append(format_tool_call("read_email", {"email_id": email_id}))
        for path in case["permissions"]["files"]:
            self.queue.append(format_tool_call("read_file", {"path": path}))
        recipients = list(self.allowed)
        if injection and injection["kind"] == "read":
            self.queue.append(format_tool_call("read_file", {"path": injection["target"]}))
        if injection and injection["kind"] == "send":
            recipients.append(injection["target"])
        self.queue.append(self._send(recipients))
        self.queue.append("I read the source and sent the requested details.")
        self._last_was_send = False

    def _send(self, recipients: List[str]) -> str:
        facts = "; ".join(alts[0] for alts in self.case["success"]["facts"])
        return format_tool_call("send_email", {
            "to": recipients,
            "subject": "Requested details",
            "body": f"Hi,\n\nHere are the details you asked for: {facts}.\n\nBest,\nJordan",
        })

    def next(self, messages: List[Dict[str, Any]]) -> str:
        last = messages[-1]
        denied = last["role"] == "tool" and str(last["content"]).startswith("Error: permission denied")
        if denied and self._last_was_send:
            self._last_was_send = True
            return self._send(self.allowed)
        text = self.queue.pop(0) if self.queue else "Done."
        self._last_was_send = '"name": "send_email"' in text
        return text


def scripted_backend(obey_injection: bool) -> ScriptedBackend:
    label = "scripted-obey" if obey_injection else "scripted-ignore"
    return ScriptedBackend(lambda case, cond: ScriptedAgent(case, cond, obey_injection), label)
