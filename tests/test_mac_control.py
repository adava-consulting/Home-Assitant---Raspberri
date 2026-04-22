import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.errors import UpstreamServiceError
from app.mac_control import MacControlService
from app.models import Intent


class FakeSettings:
    mac_control_enabled = True
    mac_control_ssh_host = "192.168.0.29"
    mac_control_ssh_user = "marcos"
    mac_control_ssh_port = 22
    mac_control_ssh_key_path = ""
    mac_control_remote_script_path = "/Users/marcos/ha-command-bridge/mac_tools/mac_control.sh"
    mac_control_timeout_seconds = 5.0


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


class MacControlServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_executes_open_youtube_command_over_ssh(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            key_path = Path(temp_dir) / "bridge-key"
            key_path.write_text("dummy", encoding="utf-8")

            settings = FakeSettings()
            settings.mac_control_ssh_key_path = str(key_path)
            service = MacControlService(settings)
            captured_command = {}

            async def fake_create_subprocess_exec(*args, **kwargs):
                captured_command["args"] = list(args)
                return _FakeProcess(stdout=b"action=open_youtube url=https://www.youtube.com/\n")

            with (
                patch("app.mac_control.shutil.which", return_value="/usr/bin/ssh"),
                patch("app.mac_control.asyncio.create_subprocess_exec", side_effect=fake_create_subprocess_exec),
            ):
                result = await service.execute_intent(
                    Intent(action="run_script", target="script.mac_open_youtube", parameters={})
                )

        self.assertEqual(result["service"], "remote.mac_control")
        self.assertEqual(result["response"]["command"], "open_youtube")
        self.assertEqual(captured_command["args"][-2], "marcos@192.168.0.29")
        self.assertEqual(
            captured_command["args"][-1],
            "/Users/marcos/ha-command-bridge/mac_tools/mac_control.sh open_youtube",
        )

    async def test_raises_when_key_is_missing(self):
        settings = FakeSettings()
        settings.mac_control_ssh_key_path = "/tmp/does-not-exist"
        service = MacControlService(settings)

        with patch("app.mac_control.shutil.which", return_value="/usr/bin/ssh"):
            with self.assertRaisesRegex(UpstreamServiceError, "SSH key is missing"):
                await service.execute_intent(
                    Intent(action="run_script", target="script.mac_open_spotify", parameters={})
                )

    async def test_raises_when_remote_command_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            key_path = Path(temp_dir) / "bridge-key"
            key_path.write_text("dummy", encoding="utf-8")

            settings = FakeSettings()
            settings.mac_control_ssh_key_path = str(key_path)
            service = MacControlService(settings)

            async def fake_create_subprocess_exec(*args, **kwargs):
                return _FakeProcess(returncode=1, stderr=b"operation not permitted")

            with (
                patch("app.mac_control.shutil.which", return_value="/usr/bin/ssh"),
                patch("app.mac_control.asyncio.create_subprocess_exec", side_effect=fake_create_subprocess_exec),
            ):
                with self.assertRaisesRegex(UpstreamServiceError, "operation not permitted"):
                    await service.execute_intent(
                        Intent(action="run_script", target="script.mac_open_chatgpt", parameters={})
                    )


if __name__ == "__main__":
    unittest.main()
