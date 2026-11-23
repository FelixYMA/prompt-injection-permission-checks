"""Run episodes and append one JSON trace per line.

Examples:
    python scripts/run_experiment.py --split dev --out results/raw/dev.jsonl
    python scripts/run_experiment.py --split test --out results/raw/test.jsonl
    python scripts/run_experiment.py --split dev --backend scripted-obey --out /tmp/smoke.jsonl
    python scripts/run_experiment.py --split dev --attack-split test --conditions A B \
        --out results/raw/dev_test_attacks.jsonl

Runs are keyed by (case_id, condition, seed). Keys already present in the
output file are skipped, so an interrupted run can be resumed with the same
command. For each case and seed the three conditions run back to back.

--attack-split runs every attacked development case with every attack of the
given split (a pre-freeze check of attack strength on development tasks).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ipieval import protocol  # noqa: E402
from ipieval.agent import AgentConfig, run_episode  # noqa: E402
from ipieval.prompts import CONDITIONS  # noqa: E402
from ipieval.tasks import build_cases, cross_attack_cases  # noqa: E402


def load_done(path: Path):
    done = set()
    if path.exists():
        with path.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    r = json.loads(line)
                    done.add((r["case_id"], r["condition"], r["seed"]))
    return done


def make_backend(name: str, cfg: dict):
    if name == "mlx":
        from ipieval.llm import MLXBackend

        return MLXBackend(cfg["model"], cfg.get("model_revision"), cfg["temperature"],
                          cfg["top_p"], cfg["top_k"], cfg["enable_thinking"])
    if name in ("scripted-obey", "scripted-ignore"):
        from ipieval.scripted import scripted_backend

        return scripted_backend(obey_injection=name == "scripted-obey")
    raise SystemExit(f"unknown backend {name}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="configs/default.json")
    ap.add_argument("--split", choices=["dev", "test"], required=True)
    ap.add_argument("--conditions", nargs="+", default=list(CONDITIONS), choices=CONDITIONS)
    ap.add_argument("--seeds", nargs="+", type=int, help="defaults to the seeds in the config")
    ap.add_argument("--backend", default="mlx", choices=["mlx", "scripted-obey", "scripted-ignore"])
    ap.add_argument("--attack-split", choices=["dev", "test"],
                    help="development tasks only: run every attacked case with every attack of this split")
    ap.add_argument("--cases", help="regex; only run case ids that match")
    ap.add_argument("--limit", type=int, help="run at most this many cases (pilots)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--allow-unfrozen", action="store_true",
                    help="allow model runs on the test split without a matching protocol lock")
    args = ap.parse_args()

    cfg = json.loads(Path(args.config).read_text())
    seeds = args.seeds if args.seeds is not None else cfg["seeds"]

    if args.split == "test" and args.backend == "mlx" and not args.allow_unfrozen:
        if not protocol.LOCK_PATH.exists():
            raise SystemExit("No protocol.lock.json. Freeze the protocol first (scripts/freeze_protocol.py).")
        current = protocol.fingerprint(cfg)["sha256"]
        if protocol.load_lock()["sha256"] != current:
            raise SystemExit("Protocol changed since it was frozen; refusing to run the test split.")

    if args.attack_split:
        if args.split != "dev":
            raise SystemExit("--attack-split is only allowed with --split dev")
        cases = cross_attack_cases(args.split, args.attack_split)
    else:
        cases = build_cases(args.split)
    if args.cases:
        pattern = re.compile(args.cases)
        cases = [c for c in cases if pattern.search(c["case_id"])]
    if args.limit:
        cases = cases[: args.limit]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = load_done(out)
    todo = [(c, s, k) for c in cases for s in seeds for k in args.conditions
            if (c["case_id"], k, s) not in done]
    print(f"{len(cases)} cases x {len(seeds)} seeds x {len(args.conditions)} conditions; "
          f"{len(todo)} runs to do, {len(done)} already in {out}", flush=True)
    if not todo:
        return

    backend = make_backend(args.backend, cfg)
    agent_cfg = AgentConfig(
        max_tool_calls=cfg["max_tool_calls"],
        max_new_tokens_per_turn=cfg["max_new_tokens_per_turn"],
        max_total_new_tokens=cfg["max_total_new_tokens"],
        timeout_s=cfg["timeout_s"],
    )

    protocol_sha = protocol.fingerprint(cfg)["sha256"]
    start = time.monotonic()
    with out.open("a") as f:
        for i, (case, seed, condition) in enumerate(todo, 1):
            trace = run_episode(case, condition, seed, backend, agent_cfg)
            trace["protocol_sha256"] = protocol_sha
            f.write(json.dumps(trace) + "\n")
            f.flush()
            elapsed = time.monotonic() - start
            eta = elapsed / i * (len(todo) - i)
            print(f"[{i}/{len(todo)}] {case['case_id']} {condition} seed={seed} "
                  f"stop={trace['stop_reason']} calls={trace['n_tool_calls']} "
                  f"{trace['seconds']:.1f}s eta={eta / 60:.1f}min", flush=True)


if __name__ == "__main__":
    main()
