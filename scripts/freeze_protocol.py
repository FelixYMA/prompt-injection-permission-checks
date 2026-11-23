"""Write protocol.lock.json, freezing prompts, tasks, scoring, and run settings.

Run this once development is finished and before any test-split run:
    python scripts/freeze_protocol.py --config configs/default.json
Use --check to compare the current files against an existing lock.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ipieval import protocol  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="configs/default.json")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--force", action="store_true", help="overwrite an existing lock")
    args = ap.parse_args()

    cfg = json.loads(Path(args.config).read_text())
    current = protocol.fingerprint(cfg)

    if args.check:
        if not protocol.LOCK_PATH.exists():
            raise SystemExit("no lock file")
        lock = protocol.load_lock()
        if lock["sha256"] == current["sha256"]:
            print(f"OK: protocol matches lock {lock['sha256'][:12]} (frozen {lock['frozen_at']})")
            return
        changed = [p for p, h in current["files"].items() if lock["files"].get(p) != h]
        print("MISMATCH. Changed files:", ", ".join(changed) or "(none)")
        if lock["test_cases_sha256"] != current["test_cases_sha256"]:
            print("Test cases changed.")
        if lock["config_sha256"] != current["config_sha256"]:
            print("Config changed.")
        raise SystemExit(1)

    if protocol.LOCK_PATH.exists() and not args.force:
        raise SystemExit("protocol.lock.json exists; pass --force to replace it")
    if cfg.get("model_revision") is None:
        raise SystemExit("Set model_revision in the config to a fixed commit before freezing.")
    current["frozen_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    protocol.LOCK_PATH.write_text(json.dumps(current, indent=2) + "\n")
    print(f"Wrote {protocol.LOCK_PATH} ({current['sha256'][:12]})")


if __name__ == "__main__":
    main()
