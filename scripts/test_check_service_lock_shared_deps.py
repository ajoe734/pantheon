import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_service_lock_shared_deps as checker


class CheckServiceLockSharedDepsTests(unittest.TestCase):
    def test_detects_missing_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            shared_dir = root / "services" / "loop-control"
            shared_dir.mkdir(parents=True)
            (shared_dir / "requirements.txt").write_text("asyncpg\njsonschema\n", encoding="utf-8")

            svc_dir = root / "services" / "consultation"
            svc_dir.mkdir(parents=True)
            (svc_dir / "workflow.py").write_text("import services.loop-control\n", encoding="utf-8")

            locks_dir = root / "dependencies" / "locks"
            locks_dir.mkdir(parents=True)
            (locks_dir / "services-consultation.txt").write_text("asyncpg==0.31.0\n", encoding="utf-8")

            count, errors = checker.check_and_fix_locks(root=root, fix=False)
            self.assertEqual(count, 1)
            self.assertIn("services-consultation.txt missing shared dependency 'jsonschema' from services/loop-control", errors[0])

    def test_fixes_missing_dependency_from_constraints(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            shared_dir = root / "services" / "loop-control"
            shared_dir.mkdir(parents=True)
            (shared_dir / "requirements.txt").write_text("asyncpg\njsonschema\n", encoding="utf-8")

            svc_dir = root / "services" / "consultation"
            svc_dir.mkdir(parents=True)
            (svc_dir / "workflow.py").write_text("import services.loop-control\n", encoding="utf-8")

            locks_dir = root / "dependencies" / "locks"
            locks_dir.mkdir(parents=True)
            lock_file = locks_dir / "services-consultation.txt"
            lock_file.write_text("asyncpg==0.31.0\n", encoding="utf-8")

            constraints_dir = root / "dependencies"
            (constraints_dir / "constraints-core.txt").write_text("jsonschema==4.26.0\n", encoding="utf-8")

            count, _ = checker.check_and_fix_locks(root=root, fix=True)
            self.assertEqual(count, 0)
            updated_content = lock_file.read_text(encoding="utf-8")
            self.assertIn("jsonschema==4.26.0", updated_content)

            # Re-check should pass now
            count_after, errors_after = checker.check_and_fix_locks(root=root, fix=False)
            self.assertEqual(count_after, 0)
            self.assertEqual(errors_after, [])

    def test_passes_when_all_dependencies_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            shared_dir = root / "services" / "loop-control"
            shared_dir.mkdir(parents=True)
            (shared_dir / "requirements.txt").write_text("asyncpg\njsonschema\n", encoding="utf-8")

            svc_dir = root / "services" / "consultation"
            svc_dir.mkdir(parents=True)
            (svc_dir / "workflow.py").write_text("import services.loop-control\n", encoding="utf-8")

            locks_dir = root / "dependencies" / "locks"
            locks_dir.mkdir(parents=True)
            (locks_dir / "services-consultation.txt").write_text("asyncpg==0.31.0\njsonschema==4.26.0\n", encoding="utf-8")

            count, errors = checker.check_and_fix_locks(root=root, fix=False)
            self.assertEqual(count, 0)
            self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
