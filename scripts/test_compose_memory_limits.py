"""Contract: every dev root-profile compose service has an env-overridable mem_limit."""
import json
import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/deployment/evidence/DEV-COMPOSE-MEMORY-LIMITS-20261117/evidence.json"
PATTERN = re.compile(r"^\$\{([A-Z0-9_]+):-(\d+)([mg])\}$")
HOST_MIB = 32086


def _mib(value: str) -> int:
    m = PATTERN.match(value)
    assert m, value
    return int(m.group(2)) * (1024 if m.group(3) == "g" else 1)


class ComposeMemoryLimits(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
        cls.root = {
            n: s
            for n, s in compose["services"].items()
            if "root" in (s.get("profiles") or [])
        }
        cls.evidence = json.loads(EVIDENCE.read_text())

    def test_every_root_service_has_env_overridable_limit(self):
        missing = [n for n, s in self.root.items() if not PATTERN.match(str(s.get("mem_limit", "")))]
        self.assertEqual(missing, [], "root services without ${VAR:-default} mem_limit")

    def test_limits_match_evidence_and_floor(self):
        ev = {r["service"]: r for r in self.evidence["services"]}
        self.assertEqual(set(ev), set(self.root))
        exceptions = set(self.evidence["floor_exceptions"])
        for name, svc in self.root.items():
            limit = _mib(svc["mem_limit"])
            self.assertEqual(limit, ev[name]["limit_mib"], name)
            self.assertGreaterEqual(limit, 128, name)
            rss = ev[name]["measured_rss_mib"] or ev[name]["rss_upper_bound_mib"]
            if rss and name not in exceptions:
                self.assertGreaterEqual(limit, 2 * rss, name)

    def test_search_svc_above_measured_peak(self):
        self.assertGreater(_mib(self.root["search-svc"]["mem_limit"]), int(11.5 * 1024))

    def test_overcommit_is_documented(self):
        total = sum(_mib(s["mem_limit"]) for s in self.root.values())
        self.assertEqual(total, self.evidence["sum_limits_mib"])
        doc = (ROOT / "docs/deployment/dev-container-memory-budget.md").read_text()
        self.assertIn(f"{total / HOST_MIB:.2f}x", doc)

    def test_staging_production_untouched(self):
        for f in ROOT.glob("docker-compose.*.yml"):
            if re.search(r"stag|prod", f.name):
                self.assertNotIn("DEV_MEM_LIMIT_", f.read_text())


if __name__ == "__main__":
    unittest.main()
