"""scripts/hub_backup_docker.sh must produce the same artefacts as hub_backup.py.

`docker` is replaced by a stub on PATH that writes fake pg_dump bytes, so the test
needs no running database. verify_hub_restore.verify_manifest must accept the result.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "hub_backup_docker.sh"
sys.path.insert(0, str(ROOT / "scripts"))  # verify_hub_restore imports its sibling hub_backup
spec = importlib.util.spec_from_file_location("verify_hub_restore", ROOT / "scripts" / "verify_hub_restore.py")
verify_hub_restore = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify_hub_restore)


class DockerBackupScriptTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.out = self.root / "backups"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.calls = self.root / "docker-calls.txt"
        self.stub("PGDMP-fake-archive")

    def stub(self, payload: str, exit_code: int = 0) -> None:
        docker = self.bin / "docker"
        docker.write_text(
            "#!/usr/bin/env bash\n"
            f'echo "$@" >> "{self.calls}"\n'
            f"printf '%s' '{payload}'\n"
            f"exit {exit_code}\n"
        )
        docker.chmod(0o755)

    def run_script(self, *args: str) -> subprocess.CompletedProcess:
        env = {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}"}
        return subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True)

    def test_writes_dump_and_manifest_accepted_by_verifier(self) -> None:
        result = self.run_script("--container", "cp-postgres-1", "--output-dir", str(self.out))
        self.assertEqual(result.returncode, 0, result.stderr)
        dumps = list(self.out.glob("hub-*.dump"))
        self.assertEqual(len(dumps), 1)
        manifest = verify_hub_restore.verify_manifest(dumps[0])
        self.assertEqual(manifest["kind"], "postgresql")
        self.assertEqual(manifest["version"], 1)
        self.assertEqual(json.loads(result.stdout)["sha256"], manifest["sha256"])
        call = self.calls.read_text()
        self.assertIn("cp-postgres-1", call)
        self.assertIn("--format=custom", call)
        self.assertIn("--no-owner", call)

    def test_failed_dump_leaves_no_files(self) -> None:
        self.stub("partial", exit_code=1)
        result = self.run_script("--container", "cp-postgres-1", "--output-dir", str(self.out))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(self.out.glob("*")), [])

    def test_empty_dump_is_an_error(self) -> None:
        self.stub("")
        result = self.run_script("--container", "cp-postgres-1", "--output-dir", str(self.out))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(self.out.glob("hub-*.dump")), [])

    def test_keep_prunes_oldest_pairs(self) -> None:
        self.out.mkdir()
        for stamp in ("20260101T000000Z", "20260102T000000Z", "20260103T000000Z"):
            (self.out / f"hub-{stamp}.dump").write_text("x")
            (self.out / f"hub-{stamp}.dump.manifest.json").write_text("{}")
        result = self.run_script("--container", "cp-postgres-1", "--output-dir", str(self.out), "--keep", "2")
        self.assertEqual(result.returncode, 0, result.stderr)
        names = sorted(p.name for p in self.out.glob("hub-*.dump"))
        self.assertEqual(len(names), 2)
        self.assertNotIn("hub-20260101T000000Z.dump", names)
        self.assertFalse((self.out / "hub-20260101T000000Z.dump.manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
