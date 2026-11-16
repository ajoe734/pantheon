import tempfile
import unittest
from pathlib import Path

from scripts import ci_source_ingestion_test_shards as sh


class ShardPlanTests(unittest.TestCase):
    FILES = [f"services/source_ingestion/tests/test_{c}.py" for c in "abcdefghij"]

    def test_plan_is_deterministic_and_complete(self):
        w = {self.FILES[0]: 100.0, self.FILES[1]: 90.0, self.FILES[2]: 10.0}
        a = sh.plan_shards(self.FILES, 4, w)
        self.assertEqual(a, sh.plan_shards(list(reversed(self.FILES)), 4, w))
        flat = [f for b in a for f in b]
        self.assertEqual(sorted(flat), sorted(self.FILES))
        self.assertEqual(len(flat), len(set(flat)))

    def test_heavy_files_do_not_share_a_shard(self):
        w = {self.FILES[0]: 100.0, self.FILES[1]: 90.0}
        a = sh.plan_shards(self.FILES, 4, w)
        shard_of = {f: i for i, b in enumerate(a) for f in b}
        self.assertNotEqual(shard_of[self.FILES[0]], shard_of[self.FILES[1]])

    def test_slow_test_guard_names_the_test(self):
        xml = ('<testsuites><testsuite time="9"><testcase classname="m.t" name="fast" time="1.0"/>'
               '<testcase classname="m.t" name="slow" time="400.0"/></testsuite></testsuites>')
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "j.xml"
            p.write_text(xml)
            slow = sh.slow_tests([str(p)], 300.0)
        self.assertEqual(slow, [("m.t::slow", 400.0)])


if __name__ == "__main__":
    unittest.main()
