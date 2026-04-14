import json
import tempfile
import unittest
from pathlib import Path

from app.config import Settings


class SettingsVoiceModelTests(unittest.TestCase):
    def _build_settings(self, **overrides) -> Settings:
        base = {
            "HOME_ASSISTANT_URL": "http://homeassistant.local:8123",
            "HOME_ASSISTANT_TOKEN": "test-token",
            "VOICE_MODEL_FILE": "",
            "ALLOWED_ENTITIES": "",
            "ALLOWED_SCENES": "",
            "ALLOWED_SCRIPTS": "",
            "IGNORED_ENTITIES": "",
            "HEALTH_MONITORED_ENTITIES": "",
            "TARGET_OVERRIDES_JSON": "{}",
        }
        base.update(overrides)
        return Settings(**base)

    def test_voice_model_file_supplies_allowed_targets_and_overrides(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            voice_model_path = Path(temp_dir) / "voice_model.json"
            voice_model_path.write_text(
                json.dumps(
                    {
                        "allowed_entities": ["light.room", "light.studio"],
                        "allowed_scenes": ["scene.good_night"],
                        "allowed_scripts": ["script.prepare_bedtime"],
                        "ignored_entities": ["sensor.noisy_debug"],
                        "target_overrides": {
                            "light.room": {"aliases": ["room lights"]},
                            "light.studio": {"aliases": ["studio lights"]},
                        },
                    }
                ),
                encoding="utf-8",
            )

            settings = self._build_settings(VOICE_MODEL_FILE=str(voice_model_path))

            self.assertEqual(settings.allowed_entities, ["light.room", "light.studio"])
            self.assertEqual(settings.allowed_scenes, ["scene.good_night"])
            self.assertEqual(settings.allowed_scripts, ["script.prepare_bedtime"])
            self.assertEqual(settings.ignored_entities, ["sensor.noisy_debug"])
            self.assertEqual(
                settings.target_overrides["light.room"]["aliases"],
                ["room lights"],
            )

    def test_explicit_env_lists_override_voice_model_lists(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            voice_model_path = Path(temp_dir) / "voice_model.json"
            voice_model_path.write_text(
                json.dumps({"allowed_entities": ["light.room"]}),
                encoding="utf-8",
            )

            settings = self._build_settings(
                VOICE_MODEL_FILE=str(voice_model_path),
                ALLOWED_ENTITIES="light.kitchen,light.den",
            )

            self.assertEqual(settings.allowed_entities, ["light.kitchen", "light.den"])

    def test_explicit_json_overrides_merge_with_voice_model_overrides(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            voice_model_path = Path(temp_dir) / "voice_model.json"
            voice_model_path.write_text(
                json.dumps(
                    {
                        "target_overrides": {
                            "light.room": {"aliases": ["room lights"]},
                            "light.studio": {"aliases": ["studio lights"]},
                        }
                    }
                ),
                encoding="utf-8",
            )

            settings = self._build_settings(
                VOICE_MODEL_FILE=str(voice_model_path),
                TARGET_OVERRIDES_JSON=json.dumps(
                    {
                        "light.studio": {"aliases": ["creative studio lights"]},
                        "switch.cafetera": {"aliases": ["coffee machine"]},
                    }
                ),
            )

            self.assertEqual(
                settings.target_overrides["light.room"]["aliases"],
                ["room lights"],
            )
            self.assertEqual(
                settings.target_overrides["light.studio"]["aliases"],
                ["creative studio lights"],
            )
            self.assertEqual(
                settings.target_overrides["switch.cafetera"]["aliases"],
                ["coffee machine"],
            )

    def test_health_monitored_entities_are_split_from_csv(self):
        settings = self._build_settings(
            HEALTH_MONITORED_ENTITIES="light.room, light.studio ,binary_sensor.front_door",
        )

        self.assertEqual(
            settings.health_monitored_entities,
            ["light.room", "light.studio", "binary_sensor.front_door"],
        )


if __name__ == "__main__":
    unittest.main()
