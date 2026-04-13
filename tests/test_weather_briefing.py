import unittest

from app.weather_briefing import WeatherBriefingService


class FakeSettings:
    local_timezone = "America/Argentina/Buenos_Aires"


class WeatherBriefingPatternTests(unittest.TestCase):
    def test_greetings_that_should_trigger_weather_briefing(self):
        service = WeatherBriefingService(FakeSettings(), home_assistant=None)

        self.assertTrue(service.should_handle("good morning"))
        self.assertTrue(service.should_handle("Good afternoon!"))
        self.assertTrue(service.should_handle("good evening"))
        self.assertTrue(service.should_handle("what's the weather"))

    def test_good_night_does_not_trigger_weather_briefing(self):
        service = WeatherBriefingService(FakeSettings(), home_assistant=None)

        self.assertFalse(service.should_handle("good night"))


if __name__ == "__main__":
    unittest.main()
