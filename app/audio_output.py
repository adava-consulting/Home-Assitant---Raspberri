from __future__ import annotations

import asyncio
import contextlib
import hashlib
import importlib.util
import logging
import os
from pathlib import Path
import shutil
import tempfile
import time
import wave

import httpx


logger = logging.getLogger(__name__)


class AudioOutputService:
    def __init__(self, settings: object) -> None:
        self._enabled = bool(getattr(settings, "audio_response_enabled", False))
        self._engine = str(getattr(settings, "audio_response_engine", "auto")).strip().lower()
        self._voice = str(getattr(settings, "audio_response_voice", "en-us"))
        self._speed = int(getattr(settings, "audio_response_speed", 155))
        self._device = str(getattr(settings, "audio_response_device", "plughw:0,0"))
        self._cache_enabled = bool(getattr(settings, "audio_response_cache_enabled", True))
        self._cache_dir = str(
            getattr(
                settings,
                "audio_response_cache_dir",
                "/home/claude-host-home/ha-command-bridge-data/audio-cache",
            )
        ).strip()
        self._fast_ack_text = str(getattr(settings, "audio_response_fast_ack_text", "Done.")).strip()
        self._kokoro_model_path = str(getattr(settings, "kokoro_model_path", "")).strip()
        self._kokoro_voices_path = str(getattr(settings, "kokoro_voices_path", "")).strip()
        self._kokoro_voice = str(getattr(settings, "kokoro_voice", "af_heart")).strip()
        self._kokoro_lang = str(getattr(settings, "kokoro_lang", "en-us")).strip()
        self._kokoro_speed = float(getattr(settings, "kokoro_speed", 1.0))
        self._kokoro_sample_rate = int(getattr(settings, "kokoro_sample_rate", 24000))
        self._kokoro_warmup_enabled = bool(getattr(settings, "kokoro_warmup_enabled", True))
        self._elevenlabs_api_key = str(getattr(settings, "elevenlabs_api_key", "")).strip()
        self._elevenlabs_voice_id = str(getattr(settings, "elevenlabs_voice_id", "")).strip()
        self._elevenlabs_model_id = str(
            getattr(settings, "elevenlabs_model_id", "eleven_flash_v2_5")
        ).strip()
        self._elevenlabs_output_format = str(
            getattr(settings, "elevenlabs_output_format", "mp3_22050_32")
        ).strip()
        self._elevenlabs_stability = float(getattr(settings, "elevenlabs_stability", 0.45))
        self._elevenlabs_similarity_boost = float(
            getattr(settings, "elevenlabs_similarity_boost", 0.8)
        )
        self._elevenlabs_style = float(getattr(settings, "elevenlabs_style", 0.1))
        self._elevenlabs_use_speaker_boost = bool(
            getattr(settings, "elevenlabs_use_speaker_boost", True)
        )
        self._elevenlabs_speed = float(getattr(settings, "elevenlabs_speed", 1.0))
        self._piper_command = str(getattr(settings, "piper_command", "piper"))
        self._piper_model_path = str(getattr(settings, "piper_model_path", "")).strip()
        self._piper_speaker = int(getattr(settings, "piper_speaker", 0))
        self._piper_length_scale = float(getattr(settings, "piper_length_scale", 1.0))
        self._kokoro_model: object | None = None
        self._kokoro_lock = asyncio.Lock()
        self._queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._worker_task: asyncio.Task[None] | None = None
        self._warmup_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if not self._enabled or self._worker_task is not None:
            return
        logger.info(
            "Audio responses enabled: preferred_engine=%s active_engine=%s device=%s",
            self._engine,
            self._resolve_active_engine(),
            self._device,
        )
        self._worker_task = asyncio.create_task(self._run(), name="audio-output-worker")
        if self._resolve_active_engine() == "kokoro" and self._kokoro_warmup_enabled:
            self._warmup_task = asyncio.create_task(self._warmup_kokoro(), name="kokoro-warmup")

    async def stop(self) -> None:
        if self._worker_task is None:
            return
        await self._queue.put(None)
        with contextlib.suppress(asyncio.CancelledError):
            await self._worker_task
        self._worker_task = None
        if self._warmup_task is not None:
            self._warmup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._warmup_task
            self._warmup_task = None

    async def enqueue(self, text: str | None) -> None:
        if not self._enabled or not text:
            return
        normalized = text.strip()
        if not normalized:
            return
        await self._queue.put(normalized)

    def diagnostics(self) -> dict[str, object]:
        return {
            "enabled": self._enabled,
            "preferred_engine": self._engine,
            "active_engine": self._resolve_active_engine(),
            "device": self._device,
            "cache_enabled": self._cache_enabled,
        }

    async def _run(self) -> None:
        while True:
            text = await self._queue.get()
            try:
                if text is None:
                    return
                await self._speak(text)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Audio response playback failed.")
            finally:
                self._queue.task_done()

    async def _speak(self, text: str) -> None:
        await self._speak_single(text)

    async def _speak_single(self, text: str) -> None:
        temp_fd, temp_name = tempfile.mkstemp(prefix="bridge-response-", suffix=".audio")
        os.close(temp_fd)
        temp_path = Path(temp_name)
        try:
            started_at = time.perf_counter()
            rendered_format = await self._render_speech(text, temp_path)
            if rendered_format is None:
                return

            rendered_at = time.perf_counter()
            await self._play_rendered(temp_path, rendered_format)
            played_at = time.perf_counter()
            logger.info(
                "Audio response completed: format=%s text_chars=%s render_ms=%s playback_ms=%s total_ms=%s",
                rendered_format,
                len(text),
                round((rendered_at - started_at) * 1000),
                round((played_at - rendered_at) * 1000),
                round((played_at - started_at) * 1000),
            )
        finally:
            with contextlib.suppress(FileNotFoundError):
                temp_path.unlink()

    async def _play_rendered(self, temp_path: Path, rendered_format: str) -> None:
        if rendered_format == "mp3":
            await self._play_mp3(temp_path)
            return
        await self._play_wav(temp_path)


    async def _render_speech(self, text: str, temp_path: Path) -> str | None:
        if self._should_use_kokoro():
            cache_path = self._audio_cache_path("kokoro", "wav", text)
            if cache_path is not None and cache_path.exists():
                shutil.copyfile(cache_path, temp_path)
                logger.info("Audio response cache hit: engine=kokoro key=%s", cache_path.stem)
                return "wav"

            rendered_format = await self._render_with_kokoro(text, temp_path)
            if rendered_format is not None:
                self._store_audio_cache(cache_path, temp_path)
                return rendered_format

            logger.warning("Falling back to another TTS engine because Kokoro was unavailable.")

        if self._should_use_elevenlabs():
            rendered_format = await self._render_with_elevenlabs(text, temp_path)
            if rendered_format is not None:
                return rendered_format

            if self._engine == "elevenlabs":
                return None

            logger.warning("Falling back to local TTS because ElevenLabs was unavailable.")

        if self._should_use_piper():
            rendered_format = await self._render_with_piper(text, temp_path)
            if rendered_format is not None:
                return rendered_format

            if self._engine == "piper":
                return None

            logger.warning("Falling back to espeak-ng because Piper was unavailable.")

        return await self._render_with_espeak(text, temp_path)

    async def _play_wav(self, temp_path: Path) -> None:
        playback = await asyncio.create_subprocess_exec(
            "aplay",
            "-D",
            self._device,
            str(temp_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, playback_stderr = await playback.communicate()
        if playback.returncode != 0:
            logger.error(
                "aplay failed with code %s: %s",
                playback.returncode,
                playback_stderr.decode().strip(),
            )

    async def _play_mp3(self, temp_path: Path) -> None:
        playback = await asyncio.create_subprocess_exec(
            "mpg123",
            "-q",
            "-o",
            "alsa",
            "-a",
            self._device,
            str(temp_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, playback_stderr = await playback.communicate()
        if playback.returncode != 0:
            logger.error(
                "mpg123 failed with code %s: %s",
                playback.returncode,
                playback_stderr.decode().strip(),
            )

    def _should_use_elevenlabs(self) -> bool:
        if self._engine not in {"auto", "elevenlabs"}:
            return False
        return bool(self._elevenlabs_api_key and self._elevenlabs_voice_id)

    def _should_use_kokoro(self) -> bool:
        if self._engine not in {"auto", "kokoro"}:
            return False
        if not self._kokoro_voice or not self._kokoro_lang:
            return False
        if not self._kokoro_model_path or not self._kokoro_voices_path:
            logger.warning("Kokoro model or voices path is not configured.")
            return False
        if not Path(self._kokoro_model_path).exists():
            logger.warning("Kokoro model path does not exist: %s", self._kokoro_model_path)
            return False
        if not Path(self._kokoro_voices_path).exists():
            logger.warning("Kokoro voices path does not exist: %s", self._kokoro_voices_path)
            return False
        if importlib.util.find_spec("kokoro_onnx") is None:
            logger.warning("kokoro-onnx package is not installed.")
            return False
        return True

    def _should_use_piper(self) -> bool:
        if self._engine not in {"auto", "piper", "kokoro"}:
            return False
        if not self._piper_model_path:
            return False
        if not Path(self._piper_model_path).exists():
            logger.warning("Piper model path does not exist: %s", self._piper_model_path)
            return False
        if shutil.which(self._piper_command) is None:
            logger.warning("Piper command not found: %s", self._piper_command)
            return False
        return True

    def _resolve_active_engine(self) -> str:
        if self._should_use_kokoro():
            return "kokoro"
        if self._should_use_elevenlabs():
            return "elevenlabs"
        if self._should_use_piper():
            return "piper"
        return "espeak-ng"

    async def _warmup_kokoro(self) -> None:
        try:
            async with self._kokoro_lock:
                await asyncio.to_thread(self._warmup_kokoro_sync)
            logger.info("Kokoro TTS model warmed up.")
        except Exception:
            logger.exception("Kokoro TTS warmup failed.")

    def _warmup_kokoro_sync(self) -> None:
        if not self._should_use_kokoro():
            return

        from kokoro_onnx import Kokoro

        model = self._kokoro_model
        if model is None:
            model = Kokoro(self._kokoro_model_path, self._kokoro_voices_path)
            self._kokoro_model = model

        # First inference is the slowest on the Pi; warm it once with the common fast ack
        # so quick local commands can play a cached response immediately.
        warmup_text = self._fast_ack_text or "Done."
        temp_fd, temp_name = tempfile.mkstemp(prefix="bridge-kokoro-warmup-", suffix=".wav")
        os.close(temp_fd)
        temp_path = Path(temp_name)
        try:
            rendered_format = self._render_with_kokoro_sync(warmup_text, temp_path)
            if rendered_format is not None:
                self._store_audio_cache(self._audio_cache_path("kokoro", rendered_format, warmup_text), temp_path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                temp_path.unlink()

    async def _render_with_kokoro(self, text: str, temp_path: Path) -> str | None:
        async with self._kokoro_lock:
            return await asyncio.to_thread(self._render_with_kokoro_sync, text, temp_path)

    def _render_with_kokoro_sync(self, text: str, temp_path: Path) -> str | None:
        try:
            import numpy as np
            from kokoro_onnx import Kokoro
        except Exception as exc:
            logger.error("Kokoro import failed: %s", exc)
            return None

        try:
            model = self._kokoro_model
            if model is None:
                model = Kokoro(self._kokoro_model_path, self._kokoro_voices_path)
                self._kokoro_model = model

            samples, sample_rate = model.create(
                text,
                voice=self._kokoro_voice,
                speed=self._kokoro_speed,
                lang=self._kokoro_lang,
            )
            if samples is None:
                logger.error("Kokoro produced no audio.")
                return None

            waveform = np.asarray(samples, dtype=np.float32).reshape(-1)
            waveform = np.clip(waveform, -1.0, 1.0)
            pcm = (waveform * 32767.0).astype(np.int16)
            with wave.open(str(temp_path), "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(int(sample_rate or self._kokoro_sample_rate))
                wav_file.writeframes(pcm.tobytes())
        except Exception:
            logger.exception("Kokoro synthesis failed.")
            return None

        return "wav"

    def _audio_cache_path(self, engine: str, audio_format: str, text: str) -> Path | None:
        if not self._cache_enabled or not self._cache_dir:
            return None

        key_parts = [
            engine,
            audio_format,
            text.strip(),
            self._kokoro_model_path,
            self._kokoro_voices_path,
            self._kokoro_voice,
            self._kokoro_lang,
            str(self._kokoro_speed),
            str(self._kokoro_sample_rate),
        ]
        cache_key = hashlib.sha256("\n".join(key_parts).encode("utf-8")).hexdigest()
        return Path(self._cache_dir) / f"{cache_key}.{audio_format}"

    def _store_audio_cache(self, cache_path: Path | None, temp_path: Path) -> None:
        if cache_path is None:
            return

        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_temp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
            shutil.copyfile(temp_path, cache_temp_path)
            cache_temp_path.replace(cache_path)
            logger.info("Audio response cache stored: engine=kokoro key=%s", cache_path.stem)
        except Exception:
            logger.exception("Failed to store audio response cache.")

    async def _render_with_espeak(self, text: str, temp_path: Path) -> str | None:
        render = await asyncio.create_subprocess_exec(
            "espeak-ng",
            "-v",
            self._voice,
            "-s",
            str(self._speed),
            "-w",
            str(temp_path),
            text,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, render_stderr = await render.communicate()
        if render.returncode != 0:
            logger.error(
                "espeak-ng failed with code %s: %s",
                render.returncode,
                render_stderr.decode().strip(),
            )
            return None
        return "wav"

    async def _render_with_piper(self, text: str, temp_path: Path) -> str | None:
        render = await asyncio.create_subprocess_exec(
            self._piper_command,
            "--model",
            self._piper_model_path,
            "--output_file",
            str(temp_path),
            "--speaker",
            str(self._piper_speaker),
            "--length_scale",
            str(self._piper_length_scale),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, render_stderr = await render.communicate(input=text.encode("utf-8"))
        if render.returncode != 0:
            logger.error(
                "piper failed with code %s: %s",
                render.returncode,
                render_stderr.decode().strip(),
            )
            return None
        return "wav"

    async def _render_with_elevenlabs(self, text: str, temp_path: Path) -> str | None:
        url = (
            f"https://api.elevenlabs.io/v1/text-to-speech/"
            f"{self._elevenlabs_voice_id}?output_format={self._elevenlabs_output_format}"
        )
        payload = {
            "text": text,
            "model_id": self._elevenlabs_model_id,
            "voice_settings": {
                "stability": self._elevenlabs_stability,
                "similarity_boost": self._elevenlabs_similarity_boost,
                "style": self._elevenlabs_style,
                "use_speaker_boost": self._elevenlabs_use_speaker_boost,
                "speed": self._elevenlabs_speed,
            },
        }
        headers = {
            "xi-api-key": self._elevenlabs_api_key,
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error("ElevenLabs TTS request failed: %s", exc)
            return None

        temp_path.write_bytes(response.content)
        return "mp3"
