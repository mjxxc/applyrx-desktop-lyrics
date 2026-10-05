import runpy
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "apple_music_ttml.py"


class CachePathTests(unittest.TestCase):
    def test_cache_path_uses_the_runtime_home_directory(self):
        with patch.object(Path, "home", return_value=Path("/tmp/applyrx-test-home")):
            namespace = runpy.run_path(str(MODULE))

        self.assertEqual(
            [
                Path("/tmp/applyrx-test-home")
                / "Library/Caches/com.apple.Music/Cache.db"
            ],
            namespace["CACHE_DB_CANDIDATES"],
        )


if __name__ == "__main__":
    unittest.main()
