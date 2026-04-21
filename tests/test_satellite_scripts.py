import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import tempfile
import threading
import time
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HOOK_SCRIPT = PROJECT_ROOT / "voice_services" / "satellite_watchdog_hook.sh"
RUN_SCRIPT = PROJECT_ROOT / "voice_services" / "run_wyoming_satellite.sh"


def _run_shell_script(script_path: Path, *args: str, env: dict[str, str], timeout: int = 10) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(script_path), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
        check=False,
    )


class SatelliteWatchdogHookTests(unittest.TestCase):
    def _write_env_file(self, temp_dir: Path, extra_lines: list[str]) -> Path:
        env_file = temp_dir / "satellite.env"
        lines = [
            "SATELLITE_NO_SPEECH_TIMEOUT_SECONDS=0",
            "SATELLITE_TRANSCRIPT_TIMEOUT_SECONDS=0",
            *extra_lines,
        ]
        env_file.write_text("\n".join(lines) + "\n")
        return env_file

    def test_detection_immediately_after_transcript_triggers_restart(self):
        with tempfile.TemporaryDirectory() as temp_dir_str:
            temp_dir = Path(temp_dir_str)
            restart_file = temp_dir / "post-transcript-restart.txt"
            post_state_file = temp_dir / "post-transcript.state"
            restart_command = f"printf 'restart' > {shlex.quote(str(restart_file))}"
            env_file = self._write_env_file(
                temp_dir,
                [
                    "SATELLITE_POST_TRANSCRIPT_COOLDOWN_SECONDS=2",
                    f"SATELLITE_POST_TRANSCRIPT_STATE_FILE={shlex.quote(str(post_state_file))}",
                    f"SATELLITE_POST_TRANSCRIPT_RESTART_COMMAND={shlex.quote(restart_command)}",
                ],
            )
            env = os.environ.copy()
            env["VOICE_SATELLITE_ENV_FILE"] = str(env_file)

            transcript_result = _run_shell_script(HOOK_SCRIPT, "transcript", env=env)
            self.assertEqual(transcript_result.returncode, 0, transcript_result.stderr)

            detection_result = _run_shell_script(HOOK_SCRIPT, "detection", env=env)
            self.assertEqual(detection_result.returncode, 0, detection_result.stderr)
            self.assertTrue(restart_file.exists())
            self.assertFalse(post_state_file.exists())

    def test_detection_after_cooldown_does_not_restart(self):
        with tempfile.TemporaryDirectory() as temp_dir_str:
            temp_dir = Path(temp_dir_str)
            restart_file = temp_dir / "late-restart.txt"
            post_state_file = temp_dir / "post-transcript.state"
            restart_command = f"printf 'restart' > {shlex.quote(str(restart_file))}"
            env_file = self._write_env_file(
                temp_dir,
                [
                    "SATELLITE_POST_TRANSCRIPT_COOLDOWN_SECONDS=1",
                    f"SATELLITE_POST_TRANSCRIPT_STATE_FILE={shlex.quote(str(post_state_file))}",
                    f"SATELLITE_POST_TRANSCRIPT_RESTART_COMMAND={shlex.quote(restart_command)}",
                ],
            )
            env = os.environ.copy()
            env["VOICE_SATELLITE_ENV_FILE"] = str(env_file)

            transcript_result = _run_shell_script(HOOK_SCRIPT, "transcript", env=env)
            self.assertEqual(transcript_result.returncode, 0, transcript_result.stderr)

            time.sleep(1.2)

            detection_result = _run_shell_script(HOOK_SCRIPT, "detection", env=env)
            self.assertEqual(detection_result.returncode, 0, detection_result.stderr)
            self.assertFalse(restart_file.exists())
            self.assertFalse(post_state_file.exists())

    def test_detection_accepts_legacy_seconds_state_file(self):
        with tempfile.TemporaryDirectory() as temp_dir_str:
            temp_dir = Path(temp_dir_str)
            restart_file = temp_dir / "legacy-restart.txt"
            post_state_file = temp_dir / "post-transcript.state"
            restart_command = f"printf 'restart' > {shlex.quote(str(restart_file))}"
            env_file = self._write_env_file(
                temp_dir,
                [
                    "SATELLITE_POST_TRANSCRIPT_COOLDOWN_SECONDS=2",
                    f"SATELLITE_POST_TRANSCRIPT_STATE_FILE={shlex.quote(str(post_state_file))}",
                    f"SATELLITE_POST_TRANSCRIPT_RESTART_COMMAND={shlex.quote(restart_command)}",
                ],
            )
            post_state_file.write_text(f"{int(time.time())}\n")
            env = os.environ.copy()
            env["VOICE_SATELLITE_ENV_FILE"] = str(env_file)

            detection_result = _run_shell_script(HOOK_SCRIPT, "detection", env=env)
            self.assertEqual(detection_result.returncode, 0, detection_result.stderr)
            self.assertTrue(restart_file.exists())

    def test_detection_records_assist_guard_state(self):
        with tempfile.TemporaryDirectory() as temp_dir_str:
            temp_dir = Path(temp_dir_str)
            assist_guard_state_file = temp_dir / "assist-guard.json"
            env_file = self._write_env_file(
                temp_dir,
                [
                    f"ASSIST_GUARD_STATE_FILE={shlex.quote(str(assist_guard_state_file))}",
                ],
            )
            env = os.environ.copy()
            env["VOICE_SATELLITE_ENV_FILE"] = str(env_file)

            detection_result = _run_shell_script(HOOK_SCRIPT, "detection", env=env)
            self.assertEqual(detection_result.returncode, 0, detection_result.stderr)
            self.assertTrue(assist_guard_state_file.exists())

            payload = json.loads(assist_guard_state_file.read_text("utf-8"))
            self.assertIn("last_detection_ms", payload)
            self.assertIn("last_detection_at", payload)


class RunWyomingSatelliteTests(unittest.TestCase):
    def _write_executable(self, path: Path, contents: str) -> None:
        path.write_text(contents)
        path.chmod(0o755)

    def test_run_script_waits_for_wake_service_and_clears_stale_state(self):
        with tempfile.TemporaryDirectory() as temp_dir_str:
            temp_dir = Path(temp_dir_str)
            bin_dir = temp_dir / "bin"
            bin_dir.mkdir()

            self._write_executable(
                bin_dir / "arecord",
                """#!/usr/bin/env bash
if [[ "${1:-}" == "-L" ]]; then
  cat <<'EOF'
plughw:CARD=Lite,DEV=0
  ReSpeaker Lite USB microphone
EOF
  exit 0
fi
exit 1
""",
            )
            self._write_executable(
                bin_dir / "aplay",
                """#!/usr/bin/env bash
if [[ "${1:-}" == "-L" ]]; then
  cat <<'EOF'
plughw:CARD=Headphones,DEV=0
  Headphones output
EOF
  exit 0
fi
exit 1
""",
            )
            self._write_executable(
                bin_dir / "amixer",
                """#!/usr/bin/env bash
exit 0
""",
            )

            launcher_dir = temp_dir / "fake-wyoming" / "script"
            launcher_dir.mkdir(parents=True)
            args_file = temp_dir / "launcher-args.txt"
            self._write_executable(
                launcher_dir / "run",
                f"""#!/usr/bin/env bash
printf '%s\n' "$@" > {shlex.quote(str(args_file))}
""",
            )

            watchdog_state = temp_dir / "watchdog.state"
            no_speech_state = temp_dir / "no-speech.state"
            transcript_state = temp_dir / "transcript.state"
            post_transcript_state = temp_dir / "post-transcript.state"
            watchdog_state.write_text("stale\n")
            no_speech_state.write_text("stale\n")
            transcript_state.write_text("stale\n")
            post_transcript_state.write_text("stale\n")

            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind(("127.0.0.1", 0))
                wake_port = sock.getsockname()[1]

            ready_event = threading.Event()

            def _wake_server() -> None:
                time.sleep(0.4)
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
                    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    server.bind(("127.0.0.1", wake_port))
                    server.listen(1)
                    server.settimeout(5)
                    ready_event.set()
                    try:
                        conn, _ = server.accept()
                    except socket.timeout:
                        return
                    with conn:
                        pass

            server_thread = threading.Thread(target=_wake_server, daemon=True)
            server_thread.start()

            env_file = temp_dir / "satellite.env"
            env_file.write_text(
                "\n".join(
                    [
                        f"WYOMING_SATELLITE_DIR={shlex.quote(str(temp_dir / 'fake-wyoming'))}",
                        'MIC_DEVICE_HINT="ReSpeaker Lite"',
                        'SND_DEVICE_HINT="Headphones"',
                        f"WAKE_URI=tcp://127.0.0.1:{wake_port}",
                        "WAKE_SERVICE_READY_TIMEOUT_SECONDS=3",
                        "WAKE_SERVICE_READY_CHECK_INTERVAL_SECONDS=1",
                        f"SATELLITE_WATCHDOG_STATE_FILE={shlex.quote(str(watchdog_state))}",
                        f"SATELLITE_NO_SPEECH_STATE_FILE={shlex.quote(str(no_speech_state))}",
                        f"SATELLITE_TRANSCRIPT_TIMEOUT_STATE_FILE={shlex.quote(str(transcript_state))}",
                        f"SATELLITE_POST_TRANSCRIPT_STATE_FILE={shlex.quote(str(post_transcript_state))}",
                    ]
                )
                + "\n"
            )

            env = os.environ.copy()
            env["VOICE_SATELLITE_ENV_FILE"] = str(env_file)
            env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"

            result = _run_shell_script(RUN_SCRIPT, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(ready_event.is_set())
            self.assertIn("Confirmed wake service is ready", result.stdout)
            self.assertTrue(args_file.exists())
            self.assertIn("--wake-uri", args_file.read_text())
            self.assertFalse(watchdog_state.exists())
            self.assertFalse(no_speech_state.exists())
            self.assertFalse(transcript_state.exists())
            self.assertFalse(post_transcript_state.exists())

            server_thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
