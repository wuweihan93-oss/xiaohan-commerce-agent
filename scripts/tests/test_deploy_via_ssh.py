"""用假的 SSH 工具验证部署入口的输入校验与临时凭证清理。"""

import hashlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "deploy_via_ssh.sh"
APPLY_SCRIPT = ROOT / "deploy" / "apply-release.sh"
REVISION = "a" * 40
BACKEND_IMAGE = "registry.example.com/study/xiaohan-commerce-backend@sha256:" + "b" * 64
FRONTEND_IMAGE = "registry.example.com/study/xiaohan-commerce-frontend@sha256:" + "c" * 64


class DeployViaSshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.tmp = self.root / "tmp"
        self.tmp.mkdir()
        self.archive = self.root / "commerce-release.tar.gz"
        self.archive.write_bytes(b"release")
        digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.archive.with_name(self.archive.name + ".sha256").write_text(
            f"{digest}  {self.archive.name}\n"
        )
        self.log = self.root / "calls.log"
        self._fake("ssh-keygen", """#!/bin/sh
if [ "$1" = "-F" ]; then
  [ "${FAKE_HOST_MATCH:-yes}" = yes ]
  exit $?
fi
exit 0
""")
        self._fake("ssh", """#!/bin/sh
printf 'ssh %s\\n' "$*" >> "$CALL_LOG"
case "$*" in
  *mktemp*) printf '/tmp/commerce-release.%s.ABC123\\n' "$REVISION" ;;
esac
""")
        self._fake("scp", """#!/bin/sh
printf 'scp %s\\n' "$*" >> "$CALL_LOG"
""")

    def tearDown(self):
        self.temp.cleanup()

    def _fake(self, name, content):
        tool = self.bin / name
        tool.write_text(content)
        tool.chmod(0o755)

    def _run(self, **changes):
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{self.bin}:{env['PATH']}",
                "TMPDIR": str(self.tmp),
                "CALL_LOG": str(self.log),
                "SSH_HOST": "example.com",
                "SSH_USER": "ubuntu",
                "SSH_PRIVATE_KEY": "fake-private-key",
                "SSH_KNOWN_HOSTS": "example.com ssh-ed25519 AAAATEST",
                "SSH_PORT": "22",
                "DEPLOY_PATH": "/home/ubuntu/apps/xiaohan-commerce-agent",
                "REVISION": REVISION,
            }
        )
        env.update(changes)
        return subprocess.run(
            ["bash", str(SCRIPT), str(self.archive)],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_success_uses_host_pinned_ssh_and_cleans_key(self):
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.log.read_text()
        self.assertIn("StrictHostKeyChecking=yes", calls)
        self.assertIn("scp ", calls)
        self.assertIn(
            f"bash ./apply-release.sh {REVISION} "
            f"/home/ubuntu/apps/xiaohan-commerce-agent "
            f"/tmp/commerce-release.{REVISION}.ABC123",
            calls,
        )
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_missing_key_fails_before_network_calls(self):
        result = self._run(SSH_PRIVATE_KEY="")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("缺少配置 SSH_PRIVATE_KEY", result.stderr)
        self.assertFalse(self.log.exists())

    def test_unknown_host_fails_before_network_calls(self):
        result = self._run(FAKE_HOST_MATCH="no")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SSH_KNOWN_HOSTS 不包含", result.stderr)
        self.assertFalse(self.log.exists())
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_invalid_port_fails_before_creating_credentials(self):
        result = self._run(SSH_PORT="70000")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SSH_PORT 超出范围", result.stderr)
        self.assertEqual(list(self.tmp.iterdir()), [])


class ApplyReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.deploy = self.root / "app"
        (self.deploy / "runtime" / "data").mkdir(parents=True)
        (self.deploy / "knowledge").mkdir()
        (self.deploy / ".env").write_text("DEMO_MODE=true\n")
        self.log = self.root / "docker.log"
        docker = self.bin / "docker"
        docker.write_text("""#!/bin/sh
printf '%s\\n' "$*" >> "$DOCKER_LOG"
case "$*" in
  *'exec -T frontend'*) [ "${FAIL_FRONTEND:-no}" != yes ]; exit $? ;;
esac
exit 0
""")
        docker.chmod(0o755)
        sleep = self.bin / "sleep"
        sleep.write_text("#!/bin/sh\nexit 0\n")
        sleep.chmod(0o755)

    def tearDown(self):
        self.temp.cleanup()

    def _release(self, revision):
        source = self.root / f"release-{revision}"
        source.mkdir()
        (source / "compose.yaml").write_text((ROOT / "compose.yaml").read_text())
        (source / "REVISION").write_text(revision + "\n")
        (source / "BACKEND_IMAGE").write_text(BACKEND_IMAGE + "\n")
        (source / "FRONTEND_IMAGE").write_text(FRONTEND_IMAGE + "\n")
        return source

    def _run(self, revision, source, fail_frontend="no"):
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{self.bin}:{env['PATH']}",
                "DOCKER_LOG": str(self.log),
                "FAIL_FRONTEND": fail_frontend,
            }
        )
        return subprocess.run(
            ["bash", str(APPLY_SCRIPT), revision, str(self.deploy), str(source)],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_success_pins_images_and_records_revision(self):
        revision = "d" * 40
        result = self._run(revision, self._release(revision))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.deploy / ".deploy/current-revision").read_text().strip(), revision)
        override = self.deploy / ".deploy/releases" / revision / "compose.override.yaml"
        self.assertIn(BACKEND_IMAGE, override.read_text())
        self.assertIn(FRONTEND_IMAGE, override.read_text())

    def test_unhealthy_update_restores_previous_release(self):
        previous = "e" * 40
        current = "f" * 40
        self.assertEqual(self._run(previous, self._release(previous)).returncode, 0)
        result = self._run(current, self._release(current), fail_frontend="yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("恢复上一版本", result.stderr)
        self.assertEqual((self.deploy / ".deploy/current-revision").read_text().strip(), previous)
        self.assertFalse((self.deploy / ".deploy/releases" / current).exists())
        self.assertIn(previous, self.log.read_text())

if __name__ == "__main__":
    unittest.main()
