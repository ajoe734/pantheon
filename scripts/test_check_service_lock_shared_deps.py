import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_service_lock_shared_deps as checker


def build_tree(root: Path, files: dict[str, str], lock: str = "fastapi==0.1\n") -> None:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    (root / "dependencies" / "locks").mkdir(parents=True, exist_ok=True)
    (root / "dependencies" / "locks" / "services-lineage-read.txt").write_text(lock, encoding="utf-8")


LINEAGE_IMAGE = {
    "services/lineage-read/Dockerfile": "COPY dependencies/locks/services-lineage-read.txt /tmp/r.txt\nCMD [\"python\", \"/workspace/services/lineage-read/main.py\"]\n",
    "services/lineage-read/main.py": "import fastapi\nfrom services.telemetry.lineage_read.service import read\n",
    "services/__init__.py": "",
    "services/telemetry/lineage_read/__init__.py": "",
    "services/telemetry/lineage_read/service.py": "def read():\n    return 1\n",
    "services/telemetry/ingest_svc.py": "import jsonschema\n",
}


class CheckServiceLockSharedDepsTests(unittest.TestCase):
    def check(self, files: dict[str, str], lock: str = "fastapi==0.1\n") -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            build_tree(Path(tmp), files, lock)
            return checker.check_locks(Path(tmp))

    def test_lineage_read_lazy_telemetry_package_passes(self) -> None:
        files = {**LINEAGE_IMAGE, "services/telemetry/__init__.py": "def ingest():\n    from .ingest_svc import x\n"}
        self.assertEqual(self.check(files), [])

    def test_lineage_read_eager_telemetry_package_is_caught(self) -> None:
        files = {**LINEAGE_IMAGE, "services/telemetry/__init__.py": "from .ingest_svc import x\n"}
        errors = self.check(files)
        self.assertEqual(len(errors), 1)
        self.assertIn("services-lineage-read.txt missing 'jsonschema'", errors[0])
        self.assertIn("services/telemetry/__init__.py", errors[0])

    def test_eager_import_passes_once_the_lock_has_the_package(self) -> None:
        files = {**LINEAGE_IMAGE, "services/telemetry/__init__.py": "from .ingest_svc import x\n"}
        self.assertEqual(self.check(files, "fastapi==0.1\njsonschema==4.0\n"), [])

    def test_import_error_guarded_and_type_checking_imports_are_optional(self) -> None:
        files = {
            **LINEAGE_IMAGE,
            "services/telemetry/__init__.py": (
                "try:\n    import numpy\nexcept ImportError:\n    numpy = None\n"
                "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import pandas\n"
            ),
        }
        self.assertEqual(self.check(files), [])

    def test_package_with_its_own_dockerfile_is_checked_through_importers(self) -> None:
        files = {
            **LINEAGE_IMAGE,
            "services/telemetry/__init__.py": "from .ingest_svc import x\n",
            "services/telemetry/Dockerfile": "COPY dependencies/locks/services-telemetry.txt /tmp/r.txt\n",
        }
        self.assertEqual(len(self.check(files)), 1)

    def test_sys_path_sibling_directory_is_followed(self) -> None:
        files = {
            "services/capital/Dockerfile": "COPY dependencies/locks/services-lineage-read.txt /tmp/r.txt\nCMD [\"python\", \"services/capital/main.py\"]\n",
            "services/capital/main.py": (
                "import sys\nfrom pathlib import Path\n_GOV = Path(__file__).resolve().parent.parent / \"control-plane\" / \"governance\"\n"
                "sys.path.insert(0, str(_GOV))\nfrom capital_pool import Pool\n"
            ),
            "services/control-plane/governance/capital_pool.py": "import pydantic\n",
        }
        errors = self.check(files)
        self.assertEqual(len(errors), 1)
        self.assertIn("missing 'pydantic'", errors[0])

    def test_distribution_alias_and_hash_lock_lines(self) -> None:
        files = {**LINEAGE_IMAGE, "services/telemetry/__init__.py": "import yaml\n", "services/lineage-read/main.py": "import services.telemetry\n"}
        self.assertEqual(self.check(files, "pyyaml==6.0 \\\n    --hash=sha256:abc\n"), [])

    def test_repository_locks_cover_their_reachable_imports(self) -> None:
        self.assertEqual(checker.check_locks(checker.ROOT), [])


if __name__ == "__main__":
    unittest.main()
