import unittest

from app.voice_safety import (
    looks_like_repetition_loop,
    sanitize_spoken_response,
    sanitize_voice_input,
)


class VoiceSafetyTests(unittest.TestCase):
    def test_repetition_loop_detects_repeated_command(self):
        text = "Turn off the lights. " * 12
        self.assertTrue(looks_like_repetition_loop(text))

    def test_repetition_loop_detects_three_repeated_phrases(self):
        text = "Turn off the lights. Turn off the lights. Turn off the lights."
        self.assertTrue(looks_like_repetition_loop(text))

    def test_repetition_loop_ignores_normal_command(self):
        text = "turn the studio lights off"
        self.assertFalse(looks_like_repetition_loop(text))

    def test_sanitize_voice_input_rejects_repetition_loop(self):
        with self.assertRaises(ValueError):
            sanitize_voice_input("Turn off the lights " * 12)

    def test_sanitize_spoken_response_replaces_validation_error_blob(self):
        text = "[{'type': 'string_too_long', 'msg': 'String should have at most 500 characters'}]"
        self.assertEqual(
            sanitize_spoken_response(text, max_chars=220),
            "I had trouble understanding that. Please try again.",
        )

    def test_sanitize_spoken_response_trims_long_text(self):
        text = "x" * 300
        spoken = sanitize_spoken_response(text, max_chars=40)
        self.assertLessEqual(len(spoken), 41)
        self.assertTrue(spoken.endswith("."))

    def test_sanitize_spoken_response_replaces_home_assistant_failure_blob(self):
        text = (
            "Failed to execute Home Assistant service light.turn_off: "
            "Server error '500 Internal Server Error' for url "
            "'http://192.168.0.17:8123/api/services/light/turn_off'"
        )
        self.assertEqual(
            sanitize_spoken_response(text, max_chars=220),
            "I couldn't complete that in Home Assistant. Please try again.",
        )


if __name__ == "__main__":
    unittest.main()
