from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

from tests.python.support.paths import REPO_ROOT


WRAPPER = REPO_ROOT / "scripts" / "pilot" / "cvm-sidecar-probe.sh"
MODULE = REPO_ROOT / "scripts" / "pilot" / "cvm_sidecar_probe.py"
SOURCE_REVISION = "a" * 40
SIDECAR_ID = f"sha256:{'1' * 64}"
CLIENT_ID = f"sha256:{'2' * 64}"


class CvmSidecarProbeCliTests(unittest.TestCase):
    def test_prepare_builds_one_attested_archive_from_exact_image_ids(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cvm-sidecar-prepare-") as root_text:
            root = Path(root_text)
            repo = root / "repo"
            pilot = repo / "scripts" / "pilot"
            pilot.mkdir(parents=True)
            shutil.copy2(WRAPPER, pilot / WRAPPER.name)
            shutil.copy2(MODULE, pilot / MODULE.name)
            (repo / "AGENTS.md").write_text("test\n", encoding="utf-8")

            fake_bin = root / "bin"
            fake_bin.mkdir()
            docker = fake_bin / "docker"
            docker.write_text(
                textwrap.dedent(
                    f"""\
                    #!/usr/bin/env python3
                    import json
                    import pathlib
                    import sys

                    sidecar = {SIDECAR_ID!r}
                    client = {CLIENT_ID!r}
                    if sys.argv[1:3] == ["image", "inspect"]:
                        image = sys.argv[3]
                        entrypoint = ["node", "/opt/browser-sidecar/prototype/server.mjs"] if image == sidecar else ["node", "/opt/browser-sidecar/prototype/client.mjs"]
                        print(json.dumps([{{
                            "Id": image,
                            "Architecture": "amd64",
                            "Os": "linux",
                            "Config": {{"Entrypoint": entrypoint, "Cmd": None, "Env": ["NODE_ENV=production"]}},
                        }}]))
                    elif sys.argv[1:3] == ["image", "save"]:
                        output = pathlib.Path(sys.argv[4])
                        assert sys.argv[5:] == [sidecar, client]
                        output.write_bytes(b"exact fake docker archive\\n")
                    else:
                        raise SystemExit(f"unexpected docker argv: {{sys.argv[1:]}}")
                    """
                ),
                encoding="utf-8",
            )
            docker.chmod(0o755)
            env = dict(os.environ)
            env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

            result = subprocess.run(
                [
                    pilot / WRAPPER.name,
                    "prepare",
                    "--source-revision",
                    SOURCE_REVISION,
                    "--sidecar-image",
                    SIDECAR_ID,
                    "--client-image",
                    CLIENT_ID,
                ],
                cwd=repo,
                env=env,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            receipt = json.loads(result.stdout)
            self.assertEqual(receipt["schema"], "cvm-sidecar.prepare-receipt/1")
            self.assertEqual(receipt["status"], "prepared")
            self.assertEqual(receipt["sourceRevision"], SOURCE_REVISION)
            self.assertEqual(
                [(image["role"], image["id"], image["platform"]) for image in receipt["images"]],
                [
                    ("sidecar", SIDECAR_ID, "linux/amd64"),
                    ("client", CLIENT_ID, "linux/amd64"),
                ],
            )
            archive = repo / receipt["archive"]["relativePath"]
            self.assertEqual(archive.read_bytes(), b"exact fake docker archive\n")
            self.assertEqual(
                receipt["archive"]["sha256"],
                hashlib.sha256(archive.read_bytes()).hexdigest(),
            )
            self.assertEqual(receipt["archive"]["bytes"], archive.stat().st_size)
            self.assertTrue(all(image["configSha256"] for image in receipt["images"]))


if __name__ == "__main__":
    unittest.main()
