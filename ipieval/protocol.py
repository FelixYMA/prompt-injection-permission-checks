"""Protocol fingerprint used to freeze the test setup.

The fingerprint covers every file that can change what a run does or how it
is scored, the run configuration, and the generated test cases. Test runs
with a real model refuse to start unless ``protocol.lock.json`` matches.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict

from .tasks import build_cases

ROOT = Path(__file__).resolve().parent.parent
LOCK_PATH = ROOT / "protocol.lock.json"
FROZEN_FILES = [
    "ipieval/env.py",
    "ipieval/permissions.py",
    "ipieval/prompts.py",
    "ipieval/tasks.py",
    "ipieval/agent.py",
    "ipieval/llm.py",
    "ipieval/scorer.py",
]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fingerprint(config: Dict[str, Any]) -> Dict[str, Any]:
    files = {p: _sha((ROOT / p).read_bytes()) for p in FROZEN_FILES}
    cases = build_cases("test")
    cases_hash = _sha(json.dumps(cases, sort_keys=True).encode())
    config_hash = _sha(json.dumps(config, sort_keys=True).encode())
    combined = _sha(json.dumps([files, cases_hash, config_hash], sort_keys=True).encode())
    return {
        "sha256": combined,
        "files": files,
        "test_cases_sha256": cases_hash,
        "n_test_cases": len(cases),
        "config_sha256": config_hash,
        "config": config,
    }


def load_lock() -> Dict[str, Any]:
    return json.loads(LOCK_PATH.read_text())
