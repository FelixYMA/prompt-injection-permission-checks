"""Simulated document-and-email workspace.

The workspace holds a set of text files and an inbox. Tools read from it and
record their side effects. Nothing leaves the process: "sending" an email only
appends it to ``Workspace.sent``.

Resource identifiers are canonicalized here, and the permission checker reuses
these functions so that the checker and the executor always agree on which
file or address a call refers to. The scorer deliberately does not.
"""

from __future__ import annotations

import copy
import posixpath
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

ADDRESS_RE = re.compile(r"[a-z0-9._%+\-]+@[a-z0-9\-]+(?:\.[a-z0-9\-]+)*\.[a-z]{2,}")

TOOL_NAMES = ("list_files", "read_file", "list_emails", "read_email", "send_email")

TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List the paths of all files in the user's workspace.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Return the text content of a file in the user's workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path, e.g. notes/standup.txt"}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_emails",
            "description": "List the emails in the user's inbox (id, sender, subject).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_email",
            "description": "Return the full text of one email from the user's inbox.",
            "parameters": {
                "type": "object",
                "properties": {
                    "email_id": {"type": "string", "description": "Email id from list_emails"}
                },
                "required": ["email_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_email",
            "description": "Send an email from the user's account.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Recipient email addresses",
                    },
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["to", "subject", "body"],
            },
        },
    },
]


def canonical_file_id(raw: Any) -> Optional[str]:
    """Map a user- or model-supplied path to the workspace's canonical file id.

    Returns ``None`` if the value cannot denote a workspace file (empty, not a
    string, or escaping the workspace root).
    """
    if not isinstance(raw, str):
        return None
    path = raw.strip().strip("'\"`").strip().replace("\\", "/").lstrip("/")
    if not path:
        return None
    path = posixpath.normpath(path)
    if path in (".", "..") or path.startswith("../"):
        return None
    return path.lower()


def parse_recipients(raw: Any) -> Tuple[List[str], List[str]]:
    """Split a ``to`` argument into canonical addresses and unparseable entries.

    Accepts a list of strings or one string separated by commas or semicolons.
    Each entry must contain exactly one address; display names such as
    ``"Ana <ana@x.com>"`` are allowed. Addresses are lower-cased and
    de-duplicated in order of first appearance.
    """
    if isinstance(raw, str):
        entries = re.split(r"[,;]", raw)
    elif isinstance(raw, list):
        entries = []
        for item in raw:
            if not isinstance(item, str):
                entries.append(repr(item))
                continue
            entries.extend(re.split(r"[,;]", item))
    else:
        return [], [repr(raw)]

    addresses: List[str] = []
    invalid: List[str] = []
    for entry in entries:
        entry = entry.strip()
        if not entry:
            continue
        found = ADDRESS_RE.findall(entry.lower())
        if len(found) != 1:
            invalid.append(entry)
            continue
        if found[0] not in addresses:
            addresses.append(found[0])
    return addresses, invalid


@dataclass
class Email:
    email_id: str
    sender: str
    to: str
    subject: str
    body: str


@dataclass
class Effect:
    """A side effect that a tool actually produced."""

    kind: str  # "read_file" | "read_email" | "send_email" | "list_files" | "list_emails"
    target: Any  # file id, email id, list of recipients, or None


@dataclass
class ToolResult:
    output: str
    ok: bool
    effects: List[Effect] = field(default_factory=list)


class Workspace:
    def __init__(self, files: Dict[str, str], emails: List[Email], user_address: str):
        self.files = {canonical_file_id(k): v for k, v in files.items()}
        if None in self.files:
            raise ValueError("workspace contains an invalid file id")
        self.emails = {e.email_id: e for e in emails}
        self.user_address = user_address
        self.sent: List[Dict[str, Any]] = []

    @classmethod
    def from_case(cls, case: Dict[str, Any]) -> "Workspace":
        emails = [Email(**e) for e in case["emails"]]
        return cls(copy.deepcopy(case["files"]), emails, case["user"]["address"])

    def execute(self, name: str, args: Dict[str, Any]) -> ToolResult:
        handler = getattr(self, "_tool_" + name, None) if name in TOOL_NAMES else None
        if handler is None:
            return ToolResult(f"Error: unknown tool '{name}'.", ok=False)
        if not isinstance(args, dict):
            return ToolResult("Error: tool arguments must be a JSON object.", ok=False)
        return handler(args)

    def _tool_list_files(self, args: Dict[str, Any]) -> ToolResult:
        listing = "\n".join(sorted(self.files))
        return ToolResult(listing, ok=True, effects=[Effect("list_files", None)])

    def _tool_read_file(self, args: Dict[str, Any]) -> ToolResult:
        raw = args.get("path")
        file_id = canonical_file_id(raw)
        if file_id is None or file_id not in self.files:
            return ToolResult(f"Error: file not found: {raw}", ok=False)
        return ToolResult(self.files[file_id], ok=True, effects=[Effect("read_file", file_id)])

    def _tool_list_emails(self, args: Dict[str, Any]) -> ToolResult:
        lines = [f"{e.email_id} | From: {e.sender} | Subject: {e.subject}" for e in self.emails.values()]
        return ToolResult("\n".join(lines), ok=True, effects=[Effect("list_emails", None)])

    def _tool_read_email(self, args: Dict[str, Any]) -> ToolResult:
        raw = args.get("email_id")
        key = raw.strip() if isinstance(raw, str) else raw
        email = self.emails.get(key)
        if email is None:
            return ToolResult(f"Error: no email with id {raw}", ok=False)
        text = f"From: {email.sender}\nTo: {email.to}\nSubject: {email.subject}\n\n{email.body}"
        return ToolResult(text, ok=True, effects=[Effect("read_email", email.email_id)])

    def _tool_send_email(self, args: Dict[str, Any]) -> ToolResult:
        addresses, invalid = parse_recipients(args.get("to"))
        if invalid:
            return ToolResult(f"Error: invalid recipient(s): {', '.join(invalid)}", ok=False)
        if not addresses:
            return ToolResult("Error: no recipients given.", ok=False)
        subject = args.get("subject", "")
        body = args.get("body", "")
        if not isinstance(subject, str) or not isinstance(body, str):
            return ToolResult("Error: subject and body must be strings.", ok=False)
        message = {"to": addresses, "subject": subject, "body": body}
        self.sent.append(message)
        return ToolResult(
            f"Email sent to {', '.join(addresses)}.",
            ok=True,
            effects=[Effect("send_email", list(addresses))],
        )
