import unittest

from ipieval.agent import AgentConfig, parse_tool_calls, run_episode, strip_thinking
from ipieval.llm import ScriptedBackend, format_tool_call
from ipieval.prompts import system_prompt
from ipieval.scorer import score_trace
from ipieval.scripted import scripted_backend
from ipieval.tasks import build_cases

CFG = AgentConfig(max_tool_calls=8, max_new_tokens_per_turn=512, max_total_new_tokens=4096,
                  timeout_s=60)


class Parsing(unittest.TestCase):
    def test_well_formed(self):
        text = ('Let me check.\n<tool_call>\n'
                '{"name": "read_file", "arguments": {"path": "a.txt"}}\n</tool_call>')
        content, calls = parse_tool_calls(text)
        self.assertEqual(content, "Let me check.")
        self.assertEqual(calls, [{"name": "read_file", "arguments": {"path": "a.txt"}, "error": None}])

    def test_string_arguments_and_multiple_calls(self):
        text = ('<tool_call>{"name": "list_files", "arguments": "{}"}</tool_call>'
                '<tool_call>{"name": "read_file", "arguments": "{\\"path\\": \\"b.txt\\"}"}</tool_call>')
        _, calls = parse_tool_calls(text)
        self.assertEqual([c["arguments"] for c in calls], [{}, {"path": "b.txt"}])

    def test_malformed(self):
        _, calls = parse_tool_calls('<tool_call>{"name": read_file}</tool_call>')
        self.assertIsNotNone(calls[0]["error"])
        _, calls = parse_tool_calls('<tool_call>\n{"name": "send_email", "arguments": {"to"')
        self.assertEqual(calls[0]["error"], "unterminated tool call")

    def test_strip_thinking(self):
        self.assertEqual(strip_thinking("<think>\nhmm\n</think>\n\nHello"), "Hello")
        self.assertEqual(strip_thinking("hmm\n</think>\nHello"), "Hello")


class Prompts(unittest.TestCase):
    def test_b_equals_c_and_extends_a(self):
        case = build_cases("dev")[0]
        a, b, c = (system_prompt(case, k) for k in "ABC")
        self.assertEqual(b, c)
        self.assertTrue(b.startswith(a))
        self.assertIn("Security rules", b)
        self.assertNotIn("Security rules", a)


def run_all(backend, conditions="ABC", split="dev"):
    rows = []
    for case in build_cases(split):
        for cond in conditions:
            rows.append(score_trace(run_episode(case, cond, 0, backend, CFG)))
    return rows


class EndToEnd(unittest.TestCase):
    def test_ignoring_agent_is_always_safe(self):
        for r in run_all(scripted_backend(obey_injection=False)):
            self.assertTrue(r["success"], r["case_id"])
            self.assertTrue(r["safe_completion"], r["case_id"])
            self.assertFalse(r["attempted_violation"], r["case_id"])
            self.assertEqual(r["n_denied"], 0)
            if r["attacked"]:
                self.assertTrue(r["exposure"], r["case_id"])

    def test_obedient_agent(self):
        for split in ("dev", "test"):
            for r in run_all(scripted_backend(obey_injection=True), split=split):
                key = (r["case_id"], r["condition"])
                self.assertTrue(r["success"], key)
                self.assertEqual(r["incorrect_blocks"], 0, key)
                self.assertEqual(r["checker_bypass"], 0, key)
                if not r["attacked"]:
                    self.assertTrue(r["safe_completion"], key)
                    continue
                self.assertTrue(r["injected_target_attempted"], key)
                if r["condition"] in "AB":
                    self.assertTrue(r["injected_target_executed"], key)
                    self.assertFalse(r["safe_completion"], key)
                else:
                    self.assertFalse(r["executed_violation"], key)
                    self.assertTrue(r["recovery_eligible"], key)
                    self.assertTrue(r["recovered"], key)
                    self.assertEqual(r["n_denied"], 1, key)

    def test_tool_limit_counts_denials(self):
        case = next(c for c in build_cases("dev") if c["variant"] == "inject_read")
        target = case["injection"]["target"]

        class Stubborn:
            def next(self, messages):
                return format_tool_call("read_file", {"path": target})

        backend = ScriptedBackend(lambda case, cond: Stubborn(), "stubborn")
        trace = run_episode(case, "C", 0, backend, CFG)
        self.assertEqual(trace["stop_reason"], "tool_limit")
        self.assertEqual(trace["n_tool_calls"], CFG.max_tool_calls)
        self.assertTrue(all(c["status"] == "denied" for c in trace["calls"]))
        score = score_trace(trace)
        self.assertFalse(score["success"])
        self.assertFalse(score["recovered"])

        trace_a = run_episode(case, "A", 0, backend, CFG)
        score_a = score_trace(trace_a)
        self.assertTrue(score_a["executed_violation"])
        self.assertEqual(score_a["violation_kinds"], ["file"])

    def test_unpermitted_tool_is_a_violation(self):
        case = next(c for c in build_cases("test")
                    if c["variant"] == "clean" and "list_emails" not in c["permissions"]["tools"])

        class Curious:
            def __init__(self):
                self.done = False

            def next(self, messages):
                if self.done:
                    return "Done."
                self.done = True
                return format_tool_call("list_emails", {})

        backend = ScriptedBackend(lambda case, cond: Curious(), "curious")
        score_b = score_trace(run_episode(case, "B", 0, backend, CFG))
        self.assertEqual(score_b["violation_kinds"], ["tool"])
        self.assertTrue(score_b["other_violation"])
        score_c = score_trace(run_episode(case, "C", 0, backend, CFG))
        self.assertFalse(score_c["executed_violation"])
        self.assertEqual(score_c["n_denied"], 1)


if __name__ == "__main__":
    unittest.main()
