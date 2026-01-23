import unittest
from pathlib import Path

# import patch
from unittest.mock import MagicMock, patch

from totelegram.utils import is_excluded


class TestExclusion(unittest.TestCase):
    def setUp(self):
        self.settings = MagicMock()

        self.settings.exclude_files = ["*.tmp", "secret.*"]
        self.settings.exclude_files_default = ["*.log"]

        self.settings.is_excluded.side_effect = lambda p: any(
            p.match(x) for x in self.settings.exclude_files
        )
        self.settings.is_excluded_default.side_effect = lambda p: any(
            p.match(x) for x in self.settings.exclude_files_default
        )

    def test_should_exclude_logs_by_default(self):
        p = Path("error.log")
        with patch.object(Path, "exists", return_value=True), patch.object(
            Path, "is_dir", return_value=False
        ):
            exclusion_patterns = (
                self.settings.exclude_files_default + self.settings.exclude_files
            )
            self.assertTrue(is_excluded(p, exclusion_patterns))

    def test_should_allow_regular_file(self):
        p = Path("video.mp4")
        with patch.object(Path, "exists", return_value=True), patch.object(
            Path, "is_dir", return_value=False
        ):
            self.assertFalse(is_excluded(p, self.settings))
