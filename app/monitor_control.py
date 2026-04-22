from __future__ import annotations

import asyncio
import logging
import os
import shlex
import shutil
from typing import Any

from app.errors import UpstreamServiceError
from app.models import Intent


logger = logging.getLogger(__name__)


class MonitorControlService:
    _TARGET_COMMANDS = {
        "script.monitor_sleep": "off",
        "script.monitor_wake": "on",
    }

    def __init__(self, settings: Any):
        self._enabled = bool(getattr(settings, "monitor_control_enabled", True))
        self._host = str(getattr(settings, "monitor_control_ssh_host", "host.docker.internal")).strip()
        self._user = str(getattr(settings, "monitor_control_ssh_user", "lucas")).strip()
        self._port = int(getattr(settings, "monitor_control_ssh_port", 22))
        self._key_path = str(
            getattr(
                settings,
                "monitor_control_ssh_key_path",
                "/home/claude-host-home/.ssh/ha-bridge-host-action",
            )
        ).strip()
        self._remote_script_path = str(
            getattr(
                settings,
                "monitor_control_remote_script_path",
                "/home/lucas/ha-command-bridge/raspberry_tools/monitor_power.sh",
            )
        ).strip()
        self._timeout = max(1.0, float(getattr(settings, "monitor_control_timeout_seconds", 12.0)))

    def can_handle(self, intent: Intent) -> bool:
        return intent.action == "run_script" and intent.target in self._TARGET_COMMANDS

    async def execute_intent(self, intent: Intent) -> dict[str, Any]:
        if not self._enabled:
            raise UpstreamServiceError("Monitor control is disabled.")

        if shutil.which("ssh") is None:
            raise UpstreamServiceError("Monitor control requires the ssh client in the bridge container.")

        if not self._host or not self._user:
            raise UpstreamServiceError("Monitor control SSH target is not configured.")

        if not self._key_path or not os.path.isfile(self._key_path):
            raise UpstreamServiceError(
                f"Monitor control SSH key is missing: {self._key_path}"
            )

        command_name = self._TARGET_COMMANDS[intent.target]
        remote_command = (
            f"sudo -n {shlex.quote(self._remote_script_path)} {shlex.quote(command_name)}"
        )
        ssh_command = [
            "ssh",
            "-i",
            self._key_path,
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "ConnectTimeout=5",
            "-p",
            str(self._port),
            f"{self._user}@{self._host}",
            remote_command,
        ]

        logger.info("Executing monitor control command '%s' through host SSH.", command_name)
        process = await asyncio.create_subprocess_exec(
            *ssh_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self._timeout)
        except TimeoutError as exc:
            process.kill()
            raise UpstreamServiceError("Monitor control timed out before completing.") from exc

        stdout_text = stdout.decode("utf-8", errors="replace").strip()
        stderr_text = stderr.decode("utf-8", errors="replace").strip()

        if process.returncode != 0:
            detail = stderr_text or stdout_text or f"exit code {process.returncode}"
            raise UpstreamServiceError(f"Monitor control failed: {detail}")

        return {
            "service": "host.monitor_control",
            "target": {"entity_id": intent.target},
            "response": {
                "command": command_name,
                "stdout": stdout_text,
            },
        }
