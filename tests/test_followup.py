import unittest

from ipieval.agent import AgentConfig, run_episode
from ipieval.followup import NUDGE, run_episode_with_nudge
from ipieval.llm import ScriptedBackend, format_tool_call
from ipieval.scripted import scripted_backend
from ipieval.tasks import build_cases

CFG = AgentConfig(max_tool_calls=8, max_new_tokens_per_turn=512, max_total_new_tokens=4096,
                  timeout_s=60)
TIMING = {"seconds", "harness", "nudges"}


def strip_timing(trace):
    out = {k: v for k, v in trace.items() if k not in TIMING}
    out["turns"] = [{k: v for k, v in t.items() if k != "seconds"} for t in trace["turns"]]
    return out


class Followup(unittest.TestCase):
    def test_without_nudges_matches_frozen_loop(self):
        for obey in (True, False):
            backend = scripted_backend(obey_injection=obey)
            for case in build_cases():
                with self.subTest(case=case["case_id"], obey=obey):
                    a = run_episode(case, "C", 0, backend, CFG)
                    b = run_episode_with_nudge(case, 0, backend, CFG, max_nudges=0)
                    self.assertEqual(strip_timing(a), strip_timing(b))

    def test_nudge_after_denial_only_once(self):
        case = next(c for c in build_cases("test") if c["variant"] == "inject_send")
        target = case["injection"]["target"]
        seen = []

        class StopsAfterDenial:
            def __init__(self):
                self.step = 0

            def next(self, messages):
                seen.append(messages[-1])
                self.step += 1
                if self.step == 1:
                    return format_tool_call("send_email", {"to": [target], "subject": "s", "body": "b"})
                return f"I will send the email now (reply {self.step})."

        backend = ScriptedBackend(lambda c, cond: StopsAfterDenial(), "stops")
        trace = run_episode_with_nudge(case, 0, backend, CFG)
        self.assertEqual([c["status"] for c in trace["calls"]], ["denied"])
        self.assertEqual(len(trace["nudges"]), 1)
        self.assertEqual(seen[2], {"role": "user", "content": NUDGE})
        self.assertEqual(trace["final_message"], "I will send the email now (reply 3).")
        self.assertEqual(trace["stop_reason"], "final_answer")

    def test_no_nudge_without_denial(self):
        case = build_cases("test")[0]

        class AnswersAtOnce:
            def next(self, messages):
                return "Done."

        backend = ScriptedBackend(lambda c, cond: AnswersAtOnce(), "answers")
        trace = run_episode_with_nudge(case, 0, backend, CFG)
        self.assertEqual(trace["nudges"], [])
        self.assertEqual(len(trace["turns"]), 1)


if __name__ == "__main__":
    unittest.main()
