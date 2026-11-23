"""Print a case, or the traces of runs, in readable form.

    python scripts/show.py case dev/dev_vendor_invoice/i0/inject_send
    python scripts/show.py trace results/raw/dev.jsonl --case dev_vendor_invoice/i0 --condition C
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ipieval.prompts import system_prompt  # noqa: E402
from ipieval.scorer import score_trace  # noqa: E402
from ipieval.tasks import build_cases  # noqa: E402


def show_case(case_id: str) -> None:
    matches = [c for c in build_cases() if c["case_id"] == case_id]
    if not matches:
        raise SystemExit(f"no case {case_id}")
    case = matches[0]
    print("=== SYSTEM (condition B) ===\n" + system_prompt(case, "B"))
    print("\n=== USER ===\n" + case["request"])
    carrier = case["carrier"]
    if carrier["kind"] == "file":
        print(f"\n=== CARRIER FILE {carrier['id']} ===\n" + case["files"][carrier["id"]])
    else:
        email = next(e for e in case["emails"] if e["email_id"] == carrier["id"])
        print(f"\n=== CARRIER EMAIL {carrier['id']} ===\n{email['subject']}\n\n{email['body']}")
    print("\n=== SUCCESS ===\n" + json.dumps(case["success"], indent=1))
    if case["injection"]:
        inj = {k: v for k, v in case["injection"].items() if k != "text"}
        print("\n=== INJECTION ===\n" + json.dumps(inj, indent=1))


def show_traces(path: str, case_filter: str, condition: str, seed: int) -> None:
    pattern = re.compile(case_filter) if case_filter else None
    with open(path) as f:
        for line in f:
            t = json.loads(line)
            if pattern and not pattern.search(t["case_id"]):
                continue
            if condition and t["condition"] != condition:
                continue
            if seed is not None and t["seed"] != seed:
                continue
            s = score_trace(t)
            print(f"\n##### {t['case_id']} | {t['condition']} | seed {t['seed']} | stop={t['stop_reason']}")
            print(f"success={s['success']} violation={s['executed_violation']} "
                  f"safe={s['safe_completion']} attempted={s['attempted_violation']} "
                  f"denied={s['n_denied']} exposure={s['exposure']}")
            for turn in t["turns"]:
                print(f"--- turn {turn['turn']} ({turn['completion_tokens']} tok, {turn['seconds']}s)")
                print(turn["raw_output"].strip())
                for idx in turn["call_indices"]:
                    call = t["calls"][idx]
                    print(f"  -> [{call['status']}] {call['result'][:300]!r}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("case")
    c.add_argument("case_id")
    t = sub.add_parser("trace")
    t.add_argument("path")
    t.add_argument("--case", default="")
    t.add_argument("--condition", default="")
    t.add_argument("--seed", type=int)
    args = ap.parse_args()
    if args.cmd == "case":
        show_case(args.case_id)
    else:
        show_traces(args.path, args.case, args.condition, args.seed)


if __name__ == "__main__":
    main()
