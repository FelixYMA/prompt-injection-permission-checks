"""Exploratory follow-up: condition C with one continuation prompt after a denial.

This analysis was added after the test split was run and is not part of the
frozen protocol. It reruns, with ``ipieval.followup.run_episode_with_nudge``,
every attacked condition-C test run that had at least one denied call. All
other attacked C runs had no denial, so the continuation prompt could not
fire and their traces are reused unchanged.

    python scripts/explore_continue.py run --out results/raw/test_continue_nudge.jsonl
    python scripts/explore_continue.py analyze --out-dir results/exploratory_continue

The combined condition is labelled N in the output tables.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ipieval import protocol  # noqa: E402
from ipieval.agent import AgentConfig  # noqa: E402
from ipieval.followup import NUDGE, run_episode_with_nudge  # noqa: E402
from ipieval.scorer import score_trace  # noqa: E402
from ipieval.stats import paired_comparison, rate  # noqa: E402

REFERENCE = "results/raw/test.jsonl"
RERUNS = "results/raw/test_continue_nudge.jsonl"


def load(path: str) -> List[Dict]:
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def eligible(traces: List[Dict]) -> List[Dict]:
    return [t for t in traces if t["condition"] == "C" and t["case"]["injection"] is not None
            and any(c["status"] == "denied" for c in t["calls"])]


def cmd_run(args):
    cfg = json.loads(Path(args.config).read_text())
    todo = eligible(load(args.reference))
    out = Path(args.out)
    done = {(t["case_id"], t["seed"]) for t in load(args.out)} if out.exists() else set()
    todo = [t for t in todo if (t["case_id"], t["seed"]) not in done]
    print(f"{len(todo)} runs to do, {len(done)} already in {out}", flush=True)
    if not todo:
        return
    if args.backend == "mlx":
        from ipieval.llm import MLXBackend
        backend = MLXBackend(cfg["model"], cfg.get("model_revision"), cfg["temperature"],
                             cfg["top_p"], cfg["top_k"], cfg["enable_thinking"])
    else:
        from ipieval.scripted import scripted_backend
        backend = scripted_backend(obey_injection=True)
    agent_cfg = AgentConfig(max_tool_calls=cfg["max_tool_calls"],
                            max_new_tokens_per_turn=cfg["max_new_tokens_per_turn"],
                            max_total_new_tokens=cfg["max_total_new_tokens"],
                            timeout_s=cfg["timeout_s"])
    protocol_sha = protocol.fingerprint(cfg)["sha256"]
    start = time.monotonic()
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a") as f:
        for i, ref in enumerate(todo, 1):
            trace = run_episode_with_nudge(ref["case"], ref["seed"], backend, agent_cfg)
            trace["protocol_sha256"] = protocol_sha
            f.write(json.dumps(trace) + "\n")
            f.flush()
            eta = (time.monotonic() - start) / i * (len(todo) - i)
            print(f"[{i}/{len(todo)}] {ref['case_id']} seed={ref['seed']} "
                  f"nudges={len(trace['nudges'])} calls={trace['n_tool_calls']} eta={eta / 60:.1f}min",
                  flush=True)


def pct(events: int, n: int) -> str:
    return f"{100 * events / n:.1f}% ({events}/{n})" if n else "n/a"


def cmd_analyze(args):
    reference = load(args.reference)
    reruns = {(t["case_id"], t["seed"]): t for t in load(args.reruns)}
    ref_c = {(t["case_id"], t["seed"]): t for t in eligible(reference)}
    if set(reruns) != set(ref_c):
        raise SystemExit("reruns do not match the eligible reference runs")

    # The rerun must repeat the original C run until the continuation prompt.
    prefix_same = 0
    for key, new in reruns.items():
        old_out = [t["raw_output"] for t in ref_c[key]["turns"]]
        new_out = [t["raw_output"] for t in new["turns"]][:len(old_out)]
        prefix_same += old_out == new_out

    rows = []
    for t in reference:
        r = score_trace(t)
        rows.append(r)
        if t["condition"] == "C":
            key = (t["case_id"], t["seed"])
            n = score_trace(reruns[key]) if key in reruns else dict(r)
            n["condition"] = "N"
            rows.append(n)
    attacked = [r for r in rows if r["attacked"]]

    by_key_old = {(r["case_id"], r["seed"]): r for r in attacked if r["condition"] == "C"}
    pairs = [(score_trace(t), by_key_old[key]) for key, t in reruns.items()]
    elig_new = [n for n, _ in pairs]
    elig_old = [o for _, o in pairs]
    nudged = sum(bool(t["nudges"]) for t in reruns.values())
    extra_denials = sum(n["n_denied"] - o["n_denied"] for n, o in pairs)
    lost = sum(o["safe_completion"] and not n["safe_completion"] for n, o in pairs)
    gained = sum(n["safe_completion"] and not o["safe_completion"] for n, o in pairs)
    extra_mail = sum(len(t["sent"]) - len(ref_c[k]["sent"]) for k, t in reruns.items())

    md = ["# Exploratory: continuation prompt after a denial (condition N)\n",
          "Not part of the frozen protocol; run after the test split. N is condition C with one "
          f"continuation prompt added when the agent stops calling tools after a denial:\n\n> {NUDGE}\n",
          f"Attacked C runs with a denial (rerun): {len(reruns)}. Continuation prompt used: {nudged}. "
          f"Identical to the original C run up to the prompt: {prefix_same}/{len(reruns)}.\n",
          "## Rerun runs\n",
          f"- Safe completion: C {pct(sum(r['safe_completion'] for r in elig_old), len(elig_old))}, "
          f"N {pct(sum(r['safe_completion'] for r in elig_new), len(elig_new))}",
          f"- Runs that became safe completions: {gained}; runs that stopped being one: {lost}",
          f"- Executed violations in N: {sum(r['executed_violation'] for r in elig_new)}",
          f"- Additional denials after the prompt: {extra_denials}; additional emails sent: {extra_mail}\n",
          "## All attacked runs (n = 160 per condition)\n",
          "| Cond | Success | Executed violation | Safe completion |", "|---|---|---|---|"]
    for cond in ("B", "C", "N"):
        sub = [r for r in attacked if r["condition"] == cond]
        md.append(f"| {cond} | {pct(*rate(sub, 'success'))} | {pct(*rate(sub, 'executed_violation'))} | "
                  f"{pct(*rate(sub, 'safe_completion'))} |")
    md += ["", "## Paired differences (exploratory 95% cluster-bootstrap CI over templates)\n",
           "| Metric | Comparison | Diff (pp) | 95% CI (pp) | Discordant x-only / y-only |",
           "|---|---|---|---|---|"]
    for metric in ("safe_completion", "success"):
        for x, y in (("N", "C"), ("N", "B")):
            res = paired_comparison(attacked, metric, x, y, n_boot=args.n_boot)
            ci = (f"[{100 * res.lo:+.1f}, {100 * res.hi:+.1f}]" if res.informative
                  else "uninformative (no discordant pairs)")
            md.append(f"| {metric} | {x} - {y} | {100 * res.diff:+.1f} | {ci} | "
                      f"{res.discordant_x_only} / {res.discordant_y_only} |")
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run")
    run.add_argument("--config", default="configs/default.json")
    run.add_argument("--reference", default=REFERENCE)
    run.add_argument("--backend", default="mlx", choices=["mlx", "scripted-obey"])
    run.add_argument("--out", default=RERUNS)
    run.set_defaults(func=cmd_run)
    an = sub.add_parser("analyze")
    an.add_argument("--reference", default=REFERENCE)
    an.add_argument("--reruns", default=RERUNS)
    an.add_argument("--out-dir", default="results/exploratory_continue")
    an.add_argument("--n-boot", type=int, default=10000)
    an.set_defaults(func=cmd_analyze)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
