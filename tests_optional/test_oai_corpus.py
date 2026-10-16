"""Optional externally supplied OAI corpus; not part of core CI."""
import os
from pathlib import Path
import unittest
from onion.cfg import parse_dump


class OAICorpusTests(unittest.TestCase):
    def test_existing_oai_dumps(self):
        directory = os.environ.get("ONION_OAI_CFG_DIR")
        if not directory:
            self.skipTest("Set ONION_OAI_CFG_DIR for optional external OAI corpus tests")
        paths = sorted(Path(directory).glob("*.txt"))
        if not paths:
            self.skipTest("OAI dumps not included in this checkout")
        names = set()
        total = 0
        for path in paths:
            functions = parse_dump(path.read_text())
            self.assertGreater(len(functions), 20, path.name)
            total += len(functions)
            for function in functions:
                names.add(function["name"])
                ids = {node["id"] for node in function["nodes"]}
                self.assertEqual(len(ids), len(function["nodes"]))
                self.assertTrue(all(e["source"] in ids and e["target"] in ids
                                    for e in function["edges"]), function["name"])
                self.assertEqual(sum(n["entry"] for n in function["nodes"]), 1)
                self.assertEqual(sum(n["exit"] for n in function["nodes"]), 1)
        self.assertGreater(total, 0)
