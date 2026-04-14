import unittest

from app.auth import request_has_valid_bridge_access


class FakeSettings:
    command_bridge_api_token = ""
    command_bridge_api_header_name = "X-Bridge-Token"


class BridgeAuthTests(unittest.TestCase):
    def test_requests_are_allowed_when_token_is_disabled(self):
        self.assertTrue(
            request_has_valid_bridge_access(
                FakeSettings(),
                headers={},
                client_host="192.168.1.10",
            )
        )

    def test_loopback_requests_are_allowed_when_token_is_enabled(self):
        settings = FakeSettings()
        settings.command_bridge_api_token = "secret-token"

        self.assertTrue(
            request_has_valid_bridge_access(
                settings,
                headers={},
                client_host="127.0.0.1",
            )
        )

    def test_custom_header_token_is_accepted(self):
        settings = FakeSettings()
        settings.command_bridge_api_token = "secret-token"

        self.assertTrue(
            request_has_valid_bridge_access(
                settings,
                headers={"X-Bridge-Token": "secret-token"},
                client_host="192.168.1.10",
            )
        )

    def test_bearer_token_is_accepted(self):
        settings = FakeSettings()
        settings.command_bridge_api_token = "secret-token"

        self.assertTrue(
            request_has_valid_bridge_access(
                settings,
                headers={"Authorization": "Bearer secret-token"},
                client_host="192.168.1.10",
            )
        )

    def test_invalid_remote_request_is_rejected(self):
        settings = FakeSettings()
        settings.command_bridge_api_token = "secret-token"

        self.assertFalse(
            request_has_valid_bridge_access(
                settings,
                headers={"X-Bridge-Token": "wrong-token"},
                client_host="192.168.1.10",
            )
        )
