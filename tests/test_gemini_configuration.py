"""Gemini Pro extended-thinking configuration tests."""

import unittest

from google.genai import types

from app.core.config import settings
from app.services.auto_generator import GenerationFailed, gemini_generation_config


class GeminiConfigurationTests(unittest.TestCase):
    def test_default_model_is_current_pro_preview(self):
        self.assertEqual(settings.gemini_model, "gemini-3.1-pro-preview")

    def test_extended_profile_uses_high_thinking_and_json(self):
        previous = settings.gemini_thinking_level
        try:
            settings.gemini_thinking_level = "high"
            config = gemini_generation_config("system")
            self.assertEqual(config.response_mime_type, "application/json")
            self.assertEqual(config.system_instruction, "system")
            self.assertEqual(
                config.thinking_config.thinking_level,
                types.ThinkingLevel.HIGH,
            )
        finally:
            settings.gemini_thinking_level = previous

    def test_invalid_thinking_level_fails_closed(self):
        previous = settings.gemini_thinking_level
        try:
            settings.gemini_thinking_level = "extended"
            with self.assertRaises(GenerationFailed):
                gemini_generation_config("system")
        finally:
            settings.gemini_thinking_level = previous


if __name__ == "__main__":
    unittest.main()
