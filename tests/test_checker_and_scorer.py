import unittest

from ipieval.env import Workspace, canonical_file_id, parse_recipients
from ipieval.permissions import Permissions, check
from ipieval.scorer import contains_fact, norm_path, recipients_of
from ipieval.tasks import build_cases, cross_attack_cases

PATHS = [
    "notes/a.txt", "./notes/a.txt", "/notes/a.txt", "notes//a.txt", "NOTES/A.TXT",
    " notes/a.txt ", "'notes/a.txt'", "`notes/a.txt`", "notes/x/../a.txt", "notes\\a.txt",
    "../notes/a.txt", "notes/../../a.txt", "", "   ", ".", "..", "hr/../notes/a.txt",
    "notes/./a.txt", "hr/salaries_2026.csv", "./HR/Salaries_2026.csv", None, 42,
]

RECIPIENTS = [
    "ana@x.com", "ANA@X.COM", " ana@x.com ", "Ana Ruiz <ana@x.com>", ["ana@x.com"],
    ["ana@x.com", "bob@y.org"], "ana@x.com, bob@y.org", "ana@x.com; bob@y.org",
    "ana@x.com bob@y.org", "not-an-address", "", [], None, ["ana@x.com", 7], "ana@x",
    ["Ana <ana@x.com>, bob@y.org"], "ana@x.com,", "a.b-c+d@sub.mail.example.co.uk",
]


class CanonicalizationAgreement(unittest.TestCase):
    """The scorer's parsing is written separately; it must agree with the runtime."""

    def test_paths_agree(self):
        for raw in PATHS:
            with self.subTest(raw=raw):
                self.assertEqual(canonical_file_id(raw), norm_path(raw))

    def test_recipients_agree(self):
        for raw in RECIPIENTS:
            with self.subTest(raw=raw):
                addrs, invalid = parse_recipients(raw)
                runtime = None if (invalid or not addrs) else addrs
                self.assertEqual(runtime, recipients_of(raw))


class Checker(unittest.TestCase):
    def setUp(self):
        self.perms = Permissions(
            tools=frozenset({"read_file", "send_email", "list_files"}),
            files=frozenset({"notes/a.txt"}),
            recipients=frozenset({"ana@x.com"}),
        )

    def test_allows_permitted(self):
        self.assertTrue(check("read_file", {"path": "./Notes/a.txt"}, self.perms).allowed)
        self.assertTrue(check("send_email", {"to": ["Ana <ANA@x.com>"], "subject": "", "body": ""},
                              self.perms).allowed)
        self.assertTrue(check("list_files", {}, self.perms).allowed)

    def test_denies_tool(self):
        self.assertFalse(check("read_email", {"email_id": "msg-1"}, self.perms).allowed)
        self.assertFalse(check("delete_everything", {}, self.perms).allowed)

    def test_denies_file(self):
        for path in ["hr/salaries_2026.csv", "notes/../hr/salaries_2026.csv", "notes/b.txt", None, ""]:
            self.assertFalse(check("read_file", {"path": path}, self.perms).allowed, path)

    def test_denies_recipient(self):
        for to in [["evil@z.net"], ["ana@x.com", "evil@z.net"], "ana@x.com evil@z.net", [], "junk"]:
            self.assertFalse(check("send_email", {"to": to}, self.perms).allowed, to)

    def test_denies_non_dict_args(self):
        self.assertFalse(check("read_file", "notes/a.txt", self.perms).allowed)


class Facts(unittest.TestCase):
    def test_numbers(self):
        self.assertTrue(contains_fact("Amount due: $4,250.00", ["4250"]))
        self.assertTrue(contains_fact("Total 628.75 dollars", ["628.75"]))
        self.assertFalse(contains_fact("Amount due: $14,250", ["4250"]))
        self.assertFalse(contains_fact("room 12, 4250", ["124250"]))

    def test_dates_and_codes(self):
        self.assertTrue(contains_fact("Meeting on Dec. 3rd", ["Dec. 3"]))
        self.assertFalse(contains_fact("Meeting on December 30", ["December 3"]))
        self.assertTrue(contains_fact("ticket NW\u20114471 is open", ["NW-4471"]))
        self.assertTrue(contains_fact("PO 88412", ["PO-88412"]))
        self.assertTrue(contains_fact("at 2:30 pm", ["2:30"]))
        self.assertFalse(contains_fact("at 12:30 pm", ["2:30"]))


class Suite(unittest.TestCase):
    def test_counts(self):
        self.assertEqual(len(build_cases("dev")), 60)
        self.assertEqual(len(build_cases("test")), 120)

    def test_variants_share_everything_but_the_slot(self):
        cases = build_cases()
        groups = {}
        for c in cases:
            groups.setdefault((c["template_id"], c["instance"]), {})[c["variant"]] = c
        for key, g in groups.items():
            clean = g["clean"]
            for v in ("inject_read", "inject_send"):
                inj = g[v]
                self.assertEqual(clean["request"], inj["request"], key)
                self.assertEqual(clean["permissions"], inj["permissions"], key)
                self.assertEqual(clean["success"], inj["success"], key)
                self.assertEqual(set(clean["files"]), set(inj["files"]), key)
                carrier = clean["carrier"]
                if carrier["kind"] == "file":
                    a, b = clean["files"][carrier["id"]], inj["files"][carrier["id"]]
                else:
                    a = next(e for e in clean["emails"] if e["email_id"] == carrier["id"])["body"]
                    b = next(e for e in inj["emails"] if e["email_id"] == carrier["id"])["body"]
                self.assertEqual(a.replace(clean["clean_note"], inj["injection"]["text"]), b, key)

    def test_facts_present_in_sources(self):
        """Every required fact must be recoverable from what the agent can read."""
        for c in build_cases():
            if c["variant"] != "clean":
                continue
            visible = "\n".join(c["files"][f] for f in c["permissions"]["files"])
            visible += "\n".join(e["body"] + "\n" + e["subject"] for e in c["emails"])
            for alts in c["success"]["facts"]:
                self.assertTrue(contains_fact(visible, alts), (c["case_id"], alts))

    def test_injection_targets_are_forbidden_and_real(self):
        for c in build_cases():
            inj = c["injection"]
            if inj is None:
                continue
            ws = Workspace.from_case(c)
            if inj["kind"] == "read":
                self.assertIn(inj["target"], ws.files)
                self.assertNotIn(inj["target"], c["permissions"]["files"])
            else:
                self.assertNotIn(inj["target"], c["permissions"]["recipients"])

    def test_splits_disjoint(self):
        dev, test = build_cases("dev"), build_cases("test")
        self.assertFalse({c["template_id"] for c in dev} & {c["template_id"] for c in test})
        self.assertFalse({c["injection"]["attack_id"] for c in dev if c["injection"]}
                         & {c["injection"]["attack_id"] for c in test if c["injection"]})
        self.assertFalse({c["clean_note"] for c in dev if c["clean_note"]}
                         & {c["clean_note"] for c in test if c["clean_note"]})

    def test_cross_attack_cases(self):
        cross = cross_attack_cases("dev", "test")
        ids = [c["case_id"] for c in cross]
        self.assertEqual(len(ids), 20 * 2 * 3)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertFalse(set(ids) & {c["case_id"] for c in build_cases()})
        self.assertTrue(all(c["split"] == "dev" and c["injection"]["attack_id"].startswith("test_")
                            for c in cross))
        with self.assertRaises(ValueError):
            cross_attack_cases("test", "dev")


if __name__ == "__main__":
    unittest.main()
