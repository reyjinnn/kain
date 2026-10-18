from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from scripts.release import package_release as packager
from scripts.security import secret_scan as scanner


class ReleaseTests(unittest.TestCase):
    def test_relocated_commands_work_outside_project_root(self):
        root = Path(__file__).resolve().parents[2]
        self.assertEqual(scanner.ROOT, root)
        self.assertEqual(packager.ROOT, root)
        with tempfile.TemporaryDirectory() as temporary:
            for script in ("security/secret_scan.py", "release/package_release.py"):
                with self.subTest(script=script):
                    result = subprocess.run(
                        [sys.executable, str(root / "scripts" / script), "--help"],
                        cwd=temporary, capture_output=True, text=True, timeout=30,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_private_inputs_dependencies_and_outputs_are_excluded(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("README.md", ".env.example", "backend/app.py", "frontend/app.js", ".env.local",
                         ".env.local.tmp", ".backups/local.txt", "backend/__pycache__/app.pyc",
                         "frontend/node_modules/runtime.js", "artifacts/result.pdf", "private-unlisted.txt",
                         "backend/cache.sqlite3", "backend/cache.sqlite3-wal", "backend/cache.sqlite3-shm",
                         "frontend/work.db", "frontend/work.db-wal", "frontend/localdata/session.json",
                         "backend/.sisa/session.json"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("sample", encoding="utf-8")
            included = {p.relative_to(root).as_posix() for p in scanner.release_files(root)}
            self.assertEqual(included, {"README.md", ".env.example", "backend/app.py", "frontend/app.js"})

    def test_real_credential_in_any_source_is_detected_without_being_reported(self):
        credential = "9r_" + "a" * 40
        findings = scanner.inspect_content("frontend/app.js", ("\nconst secret='" + credential + "';").encode())
        self.assertTrue(findings)
        self.assertEqual(findings[0]["line"], 2)
        self.assertNotIn(credential, str(findings))
        self.assertTrue(scanner.inspect_content(".env.local", b"empty"))

    def test_empty_credentials_do_not_consume_the_next_line(self):
        for newline in (b"\n", b"\r\n"):
            content = b"NINEROUTER_KEY=" + newline + b"NINEROUTER_IMAGE_KEY="
            self.assertEqual(scanner.inspect_content(".env.example", content), [])
        content = b"OPENAI_API_KEY\t=\t'" + b"a" * 32 + b"'"
        self.assertEqual(scanner.inspect_content("example.py", content)[0]["rule"], "assigned_credential")

    def test_archive_contains_no_secret_and_refuses_source_contamination(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "frontend").mkdir()
            (root / "frontend" / "app.js").write_text("safe", encoding="utf-8")
            secret = "local-credential-value"
            (root / ".env.local").write_text("NINEROUTER_KEY=" + secret, encoding="utf-8")
            output = root / "artifacts" / "release.zip"
            packager.package(output, root)
            with ZipFile(output) as archive:
                self.assertEqual(archive.namelist(), ["SISA/frontend/app.js"])
                self.assertNotIn(secret.encode(), archive.read(archive.namelist()[0]))
            (root / "frontend" / "app.js").write_text(secret, encoding="utf-8")
            with self.assertRaises(ValueError):
                packager.package(output, root)


if __name__ == "__main__":
    unittest.main()
