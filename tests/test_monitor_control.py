import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.errors import UpstreamServiceError
from app.models import Intent
from app.monitor_control import MonitorControlService


class FakeSettings:
    monitor_control_enabled = True
    monitor_control_ssh_host = "host.docker.internal"
    monitor_control_ssh_user = "lucas"
    monitor_control_ssh_port = 22
    monitor_control_ssh_key_path = ""
    monitor_control_remote_script_path = "/home/lucas/ha-command-bridge/raspberry_tools/monitor_power.sh"
    monitor_control_timeout_seconds = 5.0


class _FakeProcess:
    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self.killed = False

    async def communicate(self):
        return self._stdout, self._stderr

    def kill(self):
        self.killed = True


class MonitorControlServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_executes_sleep_command_over_ssh(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            key_path = Path(temp_dir) / "bridge-key"
            key_path.write_text("dummy", encoding="utf-8")

            settings = FakeSettings()
            settings.monitor_control_ssh_key_path = str(key_path)
            service = MonitorControlService(settings)
            captured_command = {}

            async def fake_create_subprocess_exec(*args, **kwargs):
                captured_command["args"] = list(args)
                return _FakeProcess(stdout=b"method=vcgencmd\n")

            with (
                patch("app.monitor_control.shutil.which", return_value="/usr/bin/ssh"),
                patch("app.monitor_control.asyncio.create_subprocess_exec", side_effect=fake_create_subprocess_exec),
            ):
                result = await service.execute_intent(
                    Intent(action="run_script", target="script.monitor_sleep", parameters={})
                )

        self.assertEqual(result["service"], "host.monitor_control")
        self.assertEqual(result["response"]["command"], "off")
        self.assertIn("ssh", captured_command["args"][0])
        self.assertIn("lucas@host.docker.internal", captured_command["args"])
        self.assertTrue(
            captured_command["args"][-1].endswith("monitor_power.sh off"),
            captured_command["args"][-1],
        )

    async def test_raises_when_key_is_missing(self):
        settings = FakeSettings()
        settings.monitor_control_ssh_key_path = "/tmp/does-not-exist"
        service = MonitorControlService(settings)

        with patch("app.monitor_control.shutil.which", return_value="/usr/bin/ssh"):
            with self.assertRaisesRegex(UpstreamServiceError, "SSH key is missing"):
                await service.execute_intent(
                    Intent(action="run_script", target="script.monitor_sleep", parameters={})
                )

    async def test_raises_when_remote_command_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            key_path = Path(temp_dir) / "bridge-key"
            key_path.write_text("dummy", encoding="utf-8")

            settings = FakeSettings()
            settings.monitor_control_ssh_key_path = str(key_path)
            service = MonitorControlService(settings)

            async def fake_create_subprocess_exec(*args, **kwargs):
                return _FakeProcess(returncode=1, stderr=b"permission denied")

            with (
                patch("app.monitor_control.shutil.which", return_value="/usr/bin/ssh"),
                patch("app.monitor_control.asyncio.create_subprocess_exec", side_effect=fake_create_subprocess_exec),
            ):
                with self.assertRaisesRegex(UpstreamServiceError, "permission denied"):
                    await service.execute_intent(
                        Intent(action="run_script", target="script.monitor_wake", parameters={})
                    )


if __name__ == "__main__":
    unittest.main()
