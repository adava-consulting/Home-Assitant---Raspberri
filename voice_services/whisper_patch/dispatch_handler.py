"""Event handler for clients of the server."""

import asyncio
import logging
import os
import tempfile
import wave
from typing import Optional

from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioChunk, AudioChunkConverter, AudioStop
from wyoming.event import Event
from wyoming.info import Describe, Info
from wyoming.server import AsyncEventHandler

from .const import Transcriber
from .models import ModelLoader
from .transcript_safety import sanitize_transcript_text

_LOGGER = logging.getLogger(__name__)


class DispatchEventHandler(AsyncEventHandler):
    """Dispatches to appropriate transcriber."""

    def __init__(
        self,
        wyoming_info: Info,
        loader: ModelLoader,
        *args,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.wyoming_info_event = wyoming_info.event()
        self._loader = loader
        self._transcriber: Optional[Transcriber] = None
        self._transcriber_future: Optional[asyncio.Future] = None
        self._language: Optional[str] = None

        self._wav_dir = tempfile.TemporaryDirectory()
        self._wav_path = os.path.join(self._wav_dir.name, "speech.wav")
        self._wav_file: Optional[wave.Wave_write] = None
        self._audio_converter = AudioChunkConverter(rate=16000, width=2, channels=1)

    def _reset_request_state(self) -> None:
        """Reset per-request state so an empty/broken request doesn't poison later ones."""
        self._language = None
        self._transcriber = None
        self._transcriber_future = None

        if self._wav_file is not None:
            self._wav_file.close()
            self._wav_file = None

    async def _safe_write_event(self, event: Event, context: str) -> bool:
        """Write an event without crashing the handler if the client disconnects."""
        try:
            await self.write_event(event)
            return True
        except (BrokenPipeError, ConnectionResetError, OSError) as err:
            _LOGGER.warning("%s delivery failed because the client disconnected: %s", context, err)
            return False

    async def _write_empty_transcript(self) -> bool:
        _LOGGER.warning("Received AudioStop without prior audio; returning empty transcript")
        return await self._safe_write_event(Transcript(text="").event(), "Empty transcript")

    async def handle_event(self, event: Event) -> bool:
        if AudioChunk.is_type(event.type):
            # Audio is saved to a WAV file for transcription later.
            # None of the underlying models support streaming.
            chunk = self._audio_converter.convert(AudioChunk.from_event(event))

            if self._wav_file is None:
                self._wav_file = wave.open(self._wav_path, "wb")
                self._wav_file.setframerate(chunk.rate)
                self._wav_file.setsampwidth(chunk.width)
                self._wav_file.setnchannels(chunk.channels)

            self._wav_file.writeframes(chunk.audio)

            if (self._transcriber is None) and (self._transcriber_future is None):
                # Load the transcriber in the background.
                # Hopefully it's ready by the time the audio stops.
                self._transcriber_future = asyncio.create_task(
                    self._loader.load_transcriber(self._language)
                )

            return True

        if AudioStop.is_type(event.type):
            _LOGGER.debug("Audio stoppped")

            if self._transcriber is None:
                # Upstream currently asserts here, but Home Assistant can send
                # AudioStop without any prior AudioChunk on aborted/empty turns.
                if self._transcriber_future is None:
                    await self._write_empty_transcript()
                    self._reset_request_state()
                    return False

                self._transcriber = await self._transcriber_future

            if (self._transcriber is None) or (self._wav_file is None):
                await self._write_empty_transcript()
                self._reset_request_state()
                return False

            self._wav_file.close()
            self._wav_file = None

            # Do transcription in a separate thread
            text = await asyncio.to_thread(
                self._transcriber.transcribe,
                self._wav_path,
                self._language,
                beam_size=self._loader.beam_size,
                initial_prompt=self._loader.initial_prompt,
            )
            sanitized_text = sanitize_transcript_text(text, max_chars=500)
            if sanitized_text != text:
                _LOGGER.warning(
                    "Sanitized suspicious transcript from %s to %s characters",
                    len(text),
                    len(sanitized_text),
                )
            if text and not sanitized_text:
                _LOGGER.warning("Dropped suspicious transcript after safety filtering")
            text = sanitized_text
            _LOGGER.info(text)
            if not await self._safe_write_event(Transcript(text=text).event(), "Transcript"):
                self._reset_request_state()
                return False
            _LOGGER.debug("Completed request")

            self._reset_request_state()
            return False

        if Transcribe.is_type(event.type):
            transcribe = Transcribe.from_event(event)
            self._language = transcribe.language or self._loader.preferred_language
            _LOGGER.debug("Language set to %s", self._language)
            return True

        if Describe.is_type(event.type):
            if not await self._safe_write_event(self.wyoming_info_event, "Info"):
                return False
            _LOGGER.debug("Sent info")
            return True

        return True
