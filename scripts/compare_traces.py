"""Compare two trace files run by run.

    python scripts/compare_traces.py results/raw/test.jsonl rerun.jsonl

Runs are matched on (case_id, condition, seed). For every run present in
both files, the model's raw output on each turn, the tool calls (name,
arguments, status), the delivered mail, the final message, and the stop
reason must be identical. Timing fields are ignored. Exits with status 1 if
any matched run differs or if the second file has runs missing from the first.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Tuple

Key = Tuple[str, str, int]


def load(path: str) -> Dict[Key, Dict[str, Any]]:
    out: Dict[Key, Dict[str, Any]] = {}
    with open(path) as f:
        for line in f:
            if line.strip():
                t = json.loads(line)
                out[(t["case_id"], t["condition"], t["seed"])] = t
    return out


def behaviour(trace: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "outputs": [turn["raw_output"] for turn in trace["turns"]],
        "calls": [(c["name"], c["arguments"], c["status"]) for c in trace["calls"]],
        "sent": trace["sent"],
        "final_message": trace["final_message"],
        "stop_reason": trace["stop_reason"],
    }


def first_difference(a: Dict[str, Any], b: Dict[str, Any]) -> str:
    for field in a:
        if a[field] != b[field]:
            return field
    return ""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("reference")
    ap.add_argument("rerun")
    args = ap.parse_args()

    ref, new = load(args.reference), load(args.rerun)
    missing = sorted(k for k in new if k not in ref)
    differ: List[Tuple[Key, str]] = []
    for key in sorted(k for k in new if k in ref):
        a, b = behaviour(ref[key]), behaviour(new[key])
        if a != b:
            differ.append((key, first_difference(a, b)))

    matched = len(new) - len(missing)
    print(f"Matched runs: {matched}. Identical: {matched - len(differ)}. Different: {len(differ)}. "
          f"Not in reference: {len(missing)}.")
    for key, field in differ:
        print(f"  differs ({field}): {key}")
    for key in missing:
        print(f"  not in reference: {key}")
    sys.exit(1 if differ or missing else 0)


if __name__ == "__main__":
    main()
