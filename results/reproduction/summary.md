# Reproduction check

Done after the test split, from a fresh clone of the public repository on the
same machine (Apple M5 Pro, same Python environment and model revision).

1. Unit tests pass, and `scripts/freeze_protocol.py --check` reports that the
   code, configuration, and test cases match `protocol.lock.json`.
2. `scripts/analyze.py` on `results/raw/test.jsonl` and `results/raw/dev.jsonl`
   reproduces `results/test/` and `results/dev/` byte for byte, including the
   bootstrap intervals.
3. 54 test runs were rerun with the model (`test_sales_report/i0`,
   `test_onboarding/i1`, and `test_budget_question/i1`; all variants, all
   conditions, both seeds). They include blocked calls that were recovered
   from, a run that stopped after a block, and a false claim of sending.
   The traces are in `test_sample_rerun.jsonl`.

```
python scripts/run_experiment.py --split test \
    --cases 'test/(test_sales_report/i0|test_onboarding/i1|test_budget_question/i1)/' \
    --out rerun.jsonl
python scripts/compare_traces.py results/raw/test.jsonl rerun.jsonl
```

Result: `Matched runs: 54. Identical: 54. Different: 0.` Every model output,
tool call, delivered email, and final message was the same as in the
original run.

This shows the runs are deterministic on this hardware and software. Other
hardware or library versions may produce different samples.
