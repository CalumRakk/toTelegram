import unittest

from totelegram.identity import Settings


class TestSettingsBufferSize(unittest.TestCase):
    def test_default_buffer_size(self):
        """Verifica que el valor por defecto sea 64 MB."""
        settings = Settings(profile_name="test")
        self.assertEqual(settings.upload_buffer_size_mb, 64)
        self.assertEqual(settings.upload_buffer_size_bytes, 64 * 1024 * 1024)

    def test_custom_valid_buffer_size(self):
        """Verifica asignación de valores válidos (ej. 128 MB o 0 para desactivar)."""
        settings_custom = Settings(profile_name="test", upload_buffer_size_mb=128)
        self.assertEqual(settings_custom.upload_buffer_size_mb, 128)
        self.assertEqual(settings_custom.upload_buffer_size_bytes, 128 * 1024 * 1024)

        settings_disabled = Settings(profile_name="test", upload_buffer_size_mb=0)
        self.assertEqual(settings_disabled.upload_buffer_size_mb, 0)
        self.assertEqual(settings_disabled.upload_buffer_size_bytes, 0)

    def test_invalid_negative_buffer_size(self):
        """Verifica que rechace números negativos."""
        with self.assertRaises(ValueError):
            Settings(profile_name="test", upload_buffer_size_mb=-10)

    def test_invalid_excessive_buffer_size(self):
        """Verifica que rechace valores excesivos (> 2048 MB)."""
        with self.assertRaises(ValueError):
            Settings(profile_name="test", upload_buffer_size_mb=4096)


if __name__ == "__main__":
    unittest.main()
