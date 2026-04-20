import importlib.util
from pathlib import Path
import unittest


_MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "voice_services"
    / "whisper_patch"
    / "transcript_safety.py"
)
_SPEC = importlib.util.spec_from_file_location("whisper_transcript_safety", _MODULE_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Unable to load transcript safety module from {_MODULE_PATH}")
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

sanitize_transcript_text = _MODULE.sanitize_transcript_text


class WhisperTranscriptSafetyTests(unittest.TestCase):
    def test_sanitize_transcript_collapses_exact_repetition_loop(self):
        text = "Turn off the lights. " * 12

        self.assertEqual(
            sanitize_transcript_text(text, max_chars=500),
            "Turn off the lights.",
        )

    def test_sanitize_transcript_preserves_normal_command(self):
        text = "Turn on the studio lights"

        self.assertEqual(
            sanitize_transcript_text(text, max_chars=500),
            text,
        )

    def test_sanitize_transcript_trims_remaining_long_text(self):
        text = " ".join(f"token{i}" for i in range(250))

        sanitized = sanitize_transcript_text(text, max_chars=120)

        self.assertLessEqual(len(sanitized), 120)
        self.assertTrue(sanitized.startswith("token0 token1"))


if __name__ == "__main__":
    unittest.main()
