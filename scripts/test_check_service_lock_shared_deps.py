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
            (svc_dir / "workflow.py").write_text("import importlib\nimportlib.import_module(\"services.loop-control\")\n", encoding="utf-8")

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
            (svc_dir / "workflow.py").write_text("import importlib\nimportlib.import_module(\"services.loop-control\")\n", encoding="utf-8")

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
            (svc_dir / "workflow.py").write_text("import importlib\nimportlib.import_module(\"services.loop-control\")\n", encoding="utf-8")

            locks_dir = root / "dependencies" / "locks"
            locks_dir.mkdir(parents=True)
            (locks_dir / "services-consultation.txt").write_text("asyncpg==0.31.0\njsonschema==4.26.0\n", encoding="utf-8")

            count, errors = checker.check_and_fix_locks(root=root, fix=False)
            self.assertEqual(count, 0)
            self.assertEqual(errors, [])


def _repo(tmp: str, importer_source: str, *, shared_dockerfile: bool = False, lock: str = "asyncpg==0.31.0\n") -> Path:
    root = Path(tmp)
    shared = root / "services" / "shared-mod"
    shared.mkdir(parents=True)
    (shared / "requirements.txt").write_text("asyncpg\njsonschema>=4.0\n", encoding="utf-8")
    if shared_dockerfile:
        (shared / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    importer = root / "services" / "consumer"
    importer.mkdir()
    (importer / "worker.py").write_text(importer_source, encoding="utf-8")
    (root / "dependencies" / "locks").mkdir(parents=True)
    (root / "dependencies" / "locks" / "services-consumer.txt").write_text(lock, encoding="utf-8")
    return root


class ImportDetectionTests(unittest.TestCase):
    def _errors(self, source: str, **kwargs) -> list[str]:
        with tempfile.TemporaryDirectory() as tmpdir:
            return checker.check_and_fix_locks(root=_repo(tmpdir, source, **kwargs))[1]

    def test_detects_import_statements_and_literal_import_calls(self) -> None:
        for source in (
            'import importlib\nimportlib.import_module("services.shared-mod.sub")\n',
            'from importlib import import_module\nimport_module("services.shared-mod")\n',
            'mod = __import__("services.shared-mod")\n',
        ):
            with self.subTest(source=source):
                self.assertEqual(len(self._errors(source)), 1, source)

    def test_regular_import_of_package_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _repo(tmpdir, "from services.shared_pkg import thing\nimport services.shared_pkg.util\n")
            (root / "services" / "shared_pkg").mkdir()
            (root / "services" / "shared_pkg" / "requirements.txt").write_text("httpx\n", encoding="utf-8")
            errors = checker.check_and_fix_locks(root=root)[1]
            self.assertEqual(errors, ["services-consumer.txt missing shared dependency 'httpx' from services/shared_pkg"])

    def test_mentions_in_strings_and_comments_are_not_imports(self) -> None:
        source = '# services.shared-mod is documented here\nNOTE = "services.shared-mod"\nprint("importlib.import_module(services.shared-mod)")\n'
        self.assertEqual(self._errors(source), [])

    def test_similarly_named_module_is_not_a_match(self) -> None:
        self.assertEqual(self._errors('import importlib\nimportlib.import_module("services.shared-module")\n'), [])

    def test_module_that_ships_its_own_dockerfile_is_still_checked(self) -> None:
        source = 'import importlib\nimportlib.import_module("services.shared-mod")\n'
        self.assertEqual(len(self._errors(source, shared_dockerfile=True)), 1)

    def test_extras_and_markers_do_not_hide_a_missing_dependency(self) -> None:
        source = 'import importlib\nimportlib.import_module("services.shared-mod")\n'
        errors = self._errors(source, lock="asyncpg==0.31.0\njsonschema[format]==4.26.0 ; python_version >= '3.12'\n")
        self.assertEqual(errors, [])

    def test_unparseable_files_do_not_crash_the_check(self) -> None:
        self.assertEqual(self._errors("def broken(:\n"), [])


if __name__ == "__main__":
    unittest.main()
