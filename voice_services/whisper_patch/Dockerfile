FROM rhasspy/wyoming-whisper:latest

# Local hotfix for rhasspy/wyoming-faster-whisper issue #86:
# AudioStop can arrive without prior AudioChunk, which currently crashes the
# handler with an AssertionError and leaves later wake cycles unreliable.
COPY dispatch_handler.py /usr/src/wyoming_faster_whisper/dispatch_handler.py
COPY transcript_safety.py /usr/src/wyoming_faster_whisper/transcript_safety.py
