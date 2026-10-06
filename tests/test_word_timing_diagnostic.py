import unittest

from lyrics_provider import CurrentTrack, LyricLine, LyricWord, Lyrics, NotFound
from scripts.diagnose_word_timing import summarize


class WordTimingDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.track = CurrentTrack(
            "Title",
            "Artist",
            "Album",
            10,
            "100000001",
        )

    def test_summary_reports_word_counts_without_lyric_content(self):
        result = Lyrics(
            (
                LyricLine(
                    0,
                    2,
                    "private lyric words",
                    (LyricWord(0, 1, "private"), LyricWord(1, 2, " words")),
                ),
            ),
            "en",
            True,
            "100000001",
        )

        summary = summarize(self.track, result)

        self.assertEqual({
            "title": "Title",
            "artist": "Artist",
            "hasWordTiming": True,
            "lineCount": 1,
            "wordCount": 2,
        }, summary)
        self.assertNotIn("private lyric words", str(summary))

    def test_unmatched_track_has_unknown_timing_not_a_false_claim(self):
        summary = summarize(self.track, NotFound("not cached"))

        self.assertEqual("unknown", summary["hasWordTiming"])
        self.assertEqual(0, summary["lineCount"])
        self.assertEqual(0, summary["wordCount"])


if __name__ == "__main__":
    unittest.main()
