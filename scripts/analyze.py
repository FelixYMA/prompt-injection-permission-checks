"""Score traces and write result tables.

    python scripts/analyze.py results/raw/test.jsonl --out results/test

Writes ``scores.csv`` (one row per run) and ``summary.md`` (tables) to --out.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Dict, List, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ipieval.scorer import score_trace  # noqa: E402
from ipieval.stats import paired_comparison, rate  # noqa: E402

CONDS = ("A", "B", "C")
SUBSETS = {
    "clean": lambda r: r["variant"] == "clean",
    "attacked": lambda r: r["attacked"],
    "inject_read": lambda r: r["variant"] == "inject_read",
    "inject_send": lambda r: r["variant"] == "inject_send",
}


def load(paths: Sequence[str]) -> List[Dict]:
    traces = []
    for p in paths:
        with open(p) as f:
            traces.extend(json.loads(line) for line in f if line.strip())
    keys = [(t["case_id"], t["condition"], t["seed"]) for t in traces]
    if len(keys) != len(set(keys)):
        raise SystemExit("duplicate runs in input")
    return traces


def pct(events: int, n: int) -> str:
    return f"{100 * events / n:.1f}% ({events}/{n})" if n else "n/a"


def table(header: List[str], rows: List[List[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def prefix_agreement(traces: List[Dict]) -> Dict[str, int]:
    """Check that B and C generate the same text until C's first denial."""
    by_key = defaultdict(dict)
    for t in traces:
        by_key[(t["case_id"], t["seed"])][t["condition"]] = t
    same = differ = 0
    for runs in by_key.values():
        if "B" not in runs or "C" not in runs:
            continue
        b, c = runs["B"], runs["C"]
        denied_turns = [call["turn"] for call in c["calls"] if call["status"] == "denied"]
        upto = (min(denied_turns) + 1) if denied_turns else max(len(b["turns"]), len(c["turns"]))
        b_out = [t["raw_output"] for t in b["turns"][:upto]]
        c_out = [t["raw_output"] for t in c["turns"][:upto]]
        if b_out == c_out:
            same += 1
        else:
            differ += 1
    return {"identical": same, "different": differ}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--n-boot", type=int, default=10000)
    args = ap.parse_args()

    traces = load(args.runs)
    rows = [score_trace(t) for t in traces]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    with (out / "scores.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        for r in rows:
            writer.writerow({k: (";".join(v) if isinstance(v, list) else v) for k, v in r.items()})

    md: List[str] = []
    splits = sorted({r["split"] for r in rows})
    md.append(f"# Results ({', '.join(splits)})\n")
    md.append(f"Runs: {len(rows)}. Seeds: {sorted({r['seed'] for r in rows})}. "
              f"Templates: {len({r['template_id'] for r in rows})}.\n")

    # 1. Main outcome table
    md.append("## Outcomes by condition\n")
    header = ["Subset", "Cond", "Success", "Executed violation", "Safe completion",
              "Attempted violation", "Injected target executed", "Exposure"]
    body = []
    for name, pred in SUBSETS.items():
        for cond in CONDS:
            sub = [r for r in rows if pred(r) and r["condition"] == cond]
            if not sub:
                continue
            attacked = name != "clean"
            body.append([
                name, cond,
                pct(*rate(sub, "success")),
                pct(*rate(sub, "executed_violation")),
                pct(*rate(sub, "safe_completion")),
                pct(*rate(sub, "attempted_violation")),
                pct(*rate(sub, "injected_target_executed")) if attacked else "-",
                pct(*rate(sub, "exposure")) if attacked else "-",
            ])
    md.append(table(header, body) + "\n")

    # 2. Paired comparisons
    md.append("## Paired differences (exploratory 95% cluster-bootstrap CI over templates)\n")
    header = ["Metric", "Subset", "Comparison", "Rate x", "Rate y", "Diff (pp)", "95% CI (pp)",
              "Discordant x-only / y-only", "Pairs"]
    body = []
    for metric, subset in [("safe_completion", "attacked"), ("executed_violation", "attacked"),
                           ("success", "attacked"), ("success", "clean"),
                           ("safe_completion", "clean"), ("executed_violation", "clean")]:
        sub = [r for r in rows if SUBSETS[subset](r)]
        for x, y in (("C", "B"), ("B", "A")):
            res = paired_comparison(sub, metric, x, y, n_boot=args.n_boot)
            if res.n_pairs == 0:
                continue
            ci = (f"[{100 * res.lo:+.1f}, {100 * res.hi:+.1f}]" if res.informative
                  else "uninformative (no discordant pairs)")
            body.append([metric, subset, f"{x} - {y}", f"{100 * res.rate_x:.1f}%",
                         f"{100 * res.rate_y:.1f}%", f"{100 * res.diff:+.1f}", ci,
                         f"{res.discordant_x_only} / {res.discordant_y_only}", res.n_pairs])
    md.append(table(header, body) + "\n")

    # 3. Recovery
    eligible = [r for r in rows if r["recovery_eligible"]]
    md.append("## Recovery in condition C\n")
    recovery = pct(*rate(eligible, "recovered")) if eligible else "undefined (no eligible runs)"
    md.append("Attacked C runs with at least one denied unauthorized call that still ended in "
              f"safe completion: {recovery}.\n")

    # 4. Per-template safe completion under attack
    md.append("## Safe completion under attack, by template\n")
    templates = sorted({r["template_id"] for r in rows})
    body = []
    for t in templates:
        line = [t]
        for cond in CONDS:
            sub = [r for r in rows if r["template_id"] == t and r["attacked"] and r["condition"] == cond]
            line.append(pct(*rate(sub, "safe_completion")) if sub else "-")
        sub_clean = [r for r in rows if r["template_id"] == t and not r["attacked"]]
        line.append(pct(*rate(sub_clean, "success")) if sub_clean else "-")
        body.append(line)
    md.append(table(["Template", "A", "B", "C", "Clean success (all conds)"], body) + "\n")

    # 5. By attack template
    md.append("## Executed injected-target violations, by attack template\n")
    attacks = sorted({r["attack_id"] for r in rows if r["attack_id"]})
    body = []
    for a in attacks:
        line = [a]
        for cond in CONDS:
            sub = [r for r in rows if r["attack_id"] == a and r["condition"] == cond]
            line.append(pct(*rate(sub, "injected_target_executed")) if sub else "-")
        body.append(line)
    md.append(table(["Attack", "A", "B", "C"], body) + "\n")

    # 6. Cost
    md.append("## Cost\n")
    body = []
    for subset in ("clean", "attacked"):
        for cond in CONDS:
            sub = [r for r in rows if SUBSETS[subset](r) and r["condition"] == cond]
            if not sub:
                continue
            body.append([subset, cond, f"{mean(r['n_tool_calls'] for r in sub):.2f}",
                         f"{mean(r['n_denied'] for r in sub):.2f}",
                         f"{mean(r['completion_tokens'] for r in sub):.0f}",
                         f"{mean(r['prompt_tokens'] for r in sub):.0f}",
                         f"{mean(r['seconds'] for r in sub):.1f}"])
    md.append(table(["Subset", "Cond", "Tool calls", "Denials", "Completion tokens",
                     "Prompt tokens", "Seconds"], body) + "\n")

    # 7. Stop reasons
    md.append("## Stop reasons\n")
    reasons = sorted({r["stop_reason"] for r in rows})
    body = []
    for cond in CONDS:
        sub = [r for r in rows if r["condition"] == cond]
        if sub:
            body.append([cond] + [sum(r["stop_reason"] == s for r in sub) for s in reasons])
    md.append(table(["Cond"] + reasons, body) + "\n")

    # 8. Integrity checks
    md.append("## Integrity checks\n")
    c_rows = [r for r in rows if r["condition"] == "C"]
    agree = prefix_agreement(traces)
    blocks = sum(r["incorrect_blocks"] for r in c_rows)
    bypasses = sum(r["checker_bypass"] for r in c_rows)
    md.append(f"- Incorrect blocks (authorized calls denied in C): {blocks}")
    md.append(f"- Checker bypasses (executed violations in C): {bypasses}")
    md.append(f"- Malformed tool calls: {sum(r['n_parse_errors'] for r in rows)}")
    md.append(f"- B/C output identical up to C's first denial: {agree['identical']} pairs; "
              f"different: {agree['different']}")
    attacked_rows = [r for r in rows if r["attacked"]]
    md.append(f"- Injection exposure in attacked runs: {pct(*rate(attacked_rows, 'exposure'))}\n")

    (out / "summary.md").write_text("\n".join(md))
    print((out / "summary.md").read_text())


if __name__ == "__main__":
    main()
