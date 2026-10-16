"""Contract tests plus acceptance of the extracted OAI 2026.w07 CFGs."""

import unittest
from pathlib import Path

from onion.cfg import build_model, parse_dump


SAMPLE = """static void process_job(int x)
 [B4 (ENTRY)]
   Succs (1): B3

 [B3]
   1: observe
   2: S_WORK_BEGIN
   3: x
   T: if [B3.3]
   Preds (1): B4
   Succs (2): B2 B1

 [B2]
   1: observe
   2: S_BRANCH_SLOW
   3: S_RETRY_OK
   Preds (1): B3
   Succs (1): B1

 [B1]
   1: observe
   2: S_WORK_END
   Preds (2): B2 B3
   Succs (1): B0

 [B0 (EXIT)]
   Preds (1): B1
"""


def site(site_id, symbol, order, anchor=False, kind="candidate"):
    return {"id": site_id, "symbol": symbol, "label": site_id,
            "kind": kind, "anchor": anchor, "estimated_cost": 1,
            "order": order}


SITES = [site("work.begin", "S_WORK_BEGIN", 0, True, "anchor"),
         site("retry.ok", "S_RETRY_OK", 1),
         site("branch.slow", "S_BRANCH_SLOW", 2),
         site("work.end", "S_WORK_END", 3, True, "anchor")]


class CFGTest(unittest.TestCase):
    def model(self, text=SAMPLE, sites=SITES):
        return build_model(text, "process_job", sites, "fixture:test", {
            "source_sha256": "test-only", "compiler_version": "Clang 18 fixture"})

    def test_retains_cfg_structure_and_terminator(self):
        parsed = parse_dump(SAMPLE)[0]
        self.assertEqual(parsed["name"], "process_job")
        self.assertEqual(len(parsed["nodes"]), 5)
        self.assertEqual(len(parsed["edges"]), 5)
        b3 = next(n for n in parsed["nodes"] if n["clang_id"] == "B3")
        self.assertEqual(b3["terminator"], "if [B3.3]")
        self.assertIn("S_WORK_BEGIN", b3["text"])
        self.assertTrue(parsed["nodes"][0]["entry"])
        self.assertTrue(parsed["nodes"][-1]["exit"])

    def test_marker_order_comes_from_cfg_not_catalog(self):
        model = self.model()
        b2 = next(n for n in model["nodes"] if n["clang_id"] == "B2")
        self.assertEqual(b2["sites"], ["branch.slow", "retry.ok"])
        self.assertTrue(model["region"]["entry"].endswith(":B3"))
        self.assertTrue(model["region"]["exit"].endswith(":B1"))
        self.assertEqual(model["provenance"]["adapter"], "clang-textual-dumpcfg-v1")

    def test_missing_and_duplicate_anchor_rejected(self):
        with self.assertRaisesRegex(ValueError, "exactly once; found 0"):
            self.model(SAMPLE.replace("S_WORK_BEGIN", "NOT_THE_MARKER"))
        with self.assertRaisesRegex(ValueError, "exactly once; found 2"):
            self.model(SAMPLE.replace("   3: x", "   3: S_WORK_BEGIN"))

    def test_marker_in_string_or_longer_identifier_does_not_map(self):
        for replacement in ('"S_WORK_BEGIN"', "S_WORK_BEGIN_EXTRA"):
            with self.assertRaisesRegex(ValueError, "exactly once; found 0"):
                self.model(SAMPLE.replace("S_WORK_BEGIN", replacement))

    def test_lifecycle_catalog_excluded(self):
        model = self.model(sites=SITES + [site("main.begin", "S_MAIN_BEGIN", 9,
                                             kind="lifecycle")])
        self.assertEqual(len(model["sites"]), len(SITES))

    def test_function_ids_do_not_alias(self):
        items = parse_dump(SAMPLE + SAMPLE.replace("process_job", "other_job"))
        self.assertNotEqual(items[0]["nodes"][0]["id"], items[1]["nodes"][0]["id"])
        self.assertEqual(items[0]["id"], parse_dump(SAMPLE)[0]["id"])
        with self.assertRaisesRegex(ValueError, "found 2"):
            self.model(SAMPLE + SAMPLE)

    def test_null_and_unreachable_are_not_normal_edges(self):
        text = SAMPLE.replace("Succs (2): B2 B1", "Succs (3): NULL B2(Unreachable) B1")
        parsed = parse_dump(text)[0]
        b3 = next(n for n in parsed["nodes"] if n["clang_id"] == "B3")
        edges = [e for e in parsed["edges"] if e["source"] == b3["id"]]
        self.assertEqual(len(edges), 1)
        self.assertTrue(edges[0]["target"].endswith(":B1"))
        self.assertEqual([r["raw"] for r in b3["successors"]],
                         ["NULL", "B2(Unreachable)", "B1"])
        self.assertEqual([(u["kind"], u["affects_completeness"])
                          for u in parsed["uncertainties"]],
                         [("unknown_successor", True), ("unreachable_successor", False)])

    def test_absent_target_and_malformed_relation_preserved_unknown(self):
        for replacement in ("Succs (2): B55 B1", "Succs (2): OPAQUE B1"):
            parsed = parse_dump(SAMPLE.replace("Succs (2): B2 B1", replacement))[0]
            self.assertTrue(any(u["affects_completeness"] for u in parsed["uncertainties"]))
            self.assertTrue(all(e["target"] is not None for e in parsed["edges"]))

    def test_diagnostics_do_not_enter_last_node(self):
        parsed = parse_dump(SAMPLE + "/tmp/foo.c:42:3: warning: something happened\n  42 | bad();\n1 warning generated.\n")[0]
        self.assertNotIn("warning", parsed["nodes"][-1]["text"])



if __name__ == "__main__":
    unittest.main()
