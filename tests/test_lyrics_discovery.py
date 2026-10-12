"""Tests for v0.2.3: cache reuse, fingerprint invalidation, id backfill, diagnostics.

Every test here builds its own throwaway cache so the suite never depends on
whatever Apple Music happens to have cached on the machine running it, and never
writes to the real cache. SQLite is always opened through the read-only scanner.
"""

import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

import lyrics_provider
from lyrics_provider import (
    AppleMusicCacheProvider,
    CacheFingerprint,
    CacheResponse,
    CacheScanner,
    CatalogResponseParser,
    CurrentTrack,
    FailureReason,
    NotFound,
    TrackMatcher,
    normalize_text,
)

REQUEST_KEY = (
    "https://amp-api.music.apple.com/v1/catalog/cn/songs"
    "?l%5Blyrics%5D=1&extend%5Bsyllable-lyrics%5D=1"
)

TTML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<tt xmlns="http://www.w3.org/ns/ttml"><body><div>'
    '<p begin="00:00:01.000" end="00:00:03.000">Hello</p>'
    "</div></body></tt>"
)


def song_body(catalog_id, title, artist, album, duration_ms=200000, ttml=TTML):
    """Build a cache body shaped like a real /v1/catalog/cn/songs response."""
    lyrics_relationship = {"data": []}
    if ttml is not None:
        lyrics_relationship = {
            "data": [{
                "type": "lyrics",
                "id": "lyric-" + str(catalog_id),
                "attributes": {
                    "ttmlLocalizations": {"en-US": ttml},
                    # The provider reads this to confirm the lyric belongs to the
                    # song; the real cache always carries it.
                    "playParams": {"catalogId": str(catalog_id)},
                },
            }]
        }
    return json.dumps({
        "data": [{
            "id": str(catalog_id),
            "type": "songs",
            "attributes": {
                "name": title,
                "artistName": artist,
                "albumName": album,
                "durationInMillis": duration_ms,
                "hasLyrics": ttml is not None,
                "hasTimeSyncedLyrics": ttml is not None,
            },
            "relationships": {"syllable-lyrics": lyrics_relationship},
        }],
        "meta": {"filters": {"equivalents": {str(catalog_id): [{
            "type": "songs",
            "id": str(catalog_id),
        }]}}},
    }).encode("utf-8")


class TempCache:
    """A disposable read/write cache used only to build fixtures."""

    def __init__(self):
        self._dir = tempfile.TemporaryDirectory()
        self.root = Path(self._dir.name)
        self.db = self.root / "Cache.db"
        (self.root / "fsCachedData").mkdir()
        connection = sqlite3.connect(self.db)
        connection.execute(
            "CREATE TABLE cfurl_cache_response "
            "(entry_ID INTEGER PRIMARY KEY, request_key TEXT)"
        )
        connection.execute(
            "CREATE TABLE cfurl_cache_receiver_data "
            "(entry_ID INTEGER PRIMARY KEY, isDataOnFS INTEGER, receiver_data BLOB)"
        )
        connection.commit()
        connection.close()
        self._next_id = 1

    def add_song(self, catalog_id, title, artist, album,
                 duration_ms=200000, ttml=TTML, request_key=REQUEST_KEY):
        body = song_body(catalog_id, title, artist, album, duration_ms, ttml)
        entry = self._next_id
        self._next_id += 1
        connection = sqlite3.connect(self.db)
        connection.execute(
            "INSERT INTO cfurl_cache_response VALUES (?, ?)", (entry, request_key)
        )
        connection.execute(
            "INSERT INTO cfurl_cache_receiver_data VALUES (?, 0, ?)", (entry, body)
        )
        connection.commit()
        connection.close()
        return entry

    def use_wal(self):
        connection = sqlite3.connect(self.db)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.commit()
        connection.close()

    def close(self):
        self._dir.cleanup()


class CacheFingerprintTests(unittest.TestCase):
    def setUp(self):
        self.cache = TempCache()
        self.addCleanup(self.cache.close)

    def test_missing_database_is_flagged(self):
        fingerprint = CacheFingerprint.of(self.cache.root / "absent.db")
        self.assertTrue(fingerprint.missing)

    def test_wal_is_part_of_the_fingerprint(self):
        # SQLite removes -wal once the last connection closes, so the handle has
        # to stay open while the signature is taken.
        connection = sqlite3.connect(self.cache.db)
        self.addCleanup(connection.close)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            "INSERT INTO cfurl_cache_receiver_data VALUES (99, 0, ?)", (b"{}",)
        )
        connection.commit()

        scanner = CacheScanner(self.cache.db)
        fingerprint = scanner.fingerprint()
        self.assertNotEqual(fingerprint.wal, (0, 0),
                            "a live WAL must change the signature")
        self.assertFalse(fingerprint.missing)

    def test_body_directory_is_part_of_the_fingerprint(self):
        scanner = CacheScanner(self.cache.db)
        before = scanner.fingerprint()
        (self.cache.root / "fsCachedData" / "new-body").write_bytes(b"{}")
        self.assertNotEqual(before, scanner.fingerprint())


class ScanReuseTests(unittest.TestCase):
    def setUp(self):
        self.cache = TempCache()
        self.addCleanup(self.cache.close)
        self.cache.add_song("123456", "First", "Artist", "Album")

    def test_second_scan_reuses_the_memo(self):
        scanner = CacheScanner(self.cache.db)
        first = scanner.scan()
        second = scanner.scan()
        self.assertEqual(first, second)
        self.assertEqual(scanner.scan_count, 1)
        self.assertEqual(scanner.cache_hits, 1)

    def test_new_lyric_is_discovered(self):
        scanner = CacheScanner(self.cache.db)
        scanner.scan()
        self.cache.add_song("789012", "Second", "Artist", "Album")
        titles = sorted(
            song.title
            for response in scanner.scan()
            for song in CatalogResponseParser().parse(response)
        )
        self.assertEqual(titles, ["First", "Second"])
        self.assertEqual(scanner.scan_count, 2)

    def test_missing_database_still_raises(self):
        scanner = CacheScanner(self.cache.root / "absent.db")
        with self.assertRaises(lyrics_provider.CacheUnavailable):
            scanner.scan()

    def test_invalidate_forces_a_rescan(self):
        scanner = CacheScanner(self.cache.db)
        scanner.scan()
        scanner.invalidate()
        scanner.scan()
        self.assertEqual(scanner.scan_count, 2)


class SongResourceTests(unittest.TestCase):
    """meta.filters.equivalents holds bare links; they must not become songs."""

    def setUp(self):
        self.cache = TempCache()
        self.addCleanup(self.cache.close)
        self.cache.add_song("123456", "Only One", "Artist", "Album")

    def test_equivalents_link_is_not_parsed_as_a_song(self):
        scanner = CacheScanner(self.cache.db)
        parser = CatalogResponseParser()
        songs = [s for response in scanner.scan() for s in parser.parse(response)]
        self.assertEqual(len(songs), 1)
        self.assertEqual(songs[0].title, "Only One")

    def test_included_section_is_still_read(self):
        payload = {
            "data": [],
            "included": [{
                "type": "songs",
                "id": "222222",
                "attributes": {
                    "name": "From Included",
                    "artistName": "Artist",
                    "albumName": "Album",
                    "durationInMillis": 200000,
                },
                "relationships": {"syllable-lyrics": {"data": []}},
            }],
        }
        songs = list(CatalogResponseParser._song_resources(payload))
        self.assertEqual([s["id"] for s in songs], ["222222"])


class RequestedIdBackfillTests(unittest.TestCase):
    def setUp(self):
        self.cache = TempCache()
        self.addCleanup(self.cache.close)

    def test_id_comes_from_the_body_when_the_url_omits_it(self):
        self.cache.add_song("123456", "Song", "Artist", "Album")
        provider = AppleMusicCacheProvider(scanner=CacheScanner(self.cache.db))
        responses, _ = provider._scan_and_parse()
        self.assertEqual(
            sorted(i for r in responses for i in r.requestedSongIds), ["123456"]
        )

    def test_response_without_songs_is_unchanged(self):
        response = CacheResponse("/v1/catalog/cn/songs", "cn", (), frozenset(), {})
        # Nothing usable: the same object comes back, no needless copy.
        self.assertIs(response.with_song_ids([]), response)
        self.assertIs(response.with_song_ids(["abc"]), response)

    def test_backfill_keeps_ids_already_in_the_url(self):
        key = REQUEST_KEY + "&ids%5Bsongs%5D=123456"
        self.cache.add_song("123456", "Song", "Artist", "Album", request_key=key)
        provider = AppleMusicCacheProvider(scanner=CacheScanner(self.cache.db))
        responses, _ = provider._scan_and_parse()
        self.assertEqual(
            sorted(i for r in responses for i in r.requestedSongIds), ["123456"]
        )

    def test_ids_that_are_not_catalog_numbers_are_dropped(self):
        response = CacheResponse(
            "/v1/catalog/cn/songs", "cn", (), frozenset(), {}
        )
        merged = response.with_song_ids(["123456", "not-an-id", "12"])
        self.assertEqual(merged.requestedSongIds, frozenset({"123456"}))


class MatchingTests(unittest.TestCase):
    def setUp(self):
        self.cache = TempCache()
        self.addCleanup(self.cache.close)
        self.cache.add_song("123456", "Song", "Artist", "Album")
        self.provider = AppleMusicCacheProvider(scanner=CacheScanner(self.cache.db))

    def _get(self, **kwargs):
        fields = dict(title="Song", artist="Artist", album="Album", duration=200.0)
        fields.update(kwargs)
        return self.provider.get_lyrics(CurrentTrack(**fields))

    def test_exact_hit(self):
        result = self._get()
        self.assertTrue(hasattr(result, "lines"))
        self.assertEqual(len(result.lines), 1)

    def test_hit_by_catalog_id_when_metadata_is_absent(self):
        self.cache.add_song("999888", "Other", "Nobody", "Elsewhere")
        result = self._get(catalogId="123456")
        self.assertTrue(hasattr(result, "lines"))

    def test_catalog_id_mismatch_is_rejected(self):
        self.cache.add_song("999888", "Song", "Artist", "Album")
        result = self._get(catalogId="555555")
        self.assertIsInstance(result, (NotFound, lyrics_provider.InvalidMatch,
                                       lyrics_provider.Ambiguous))
        self.assertFalse(hasattr(result, "lines"))

    def test_missing_metadata_falls_back_safely(self):
        result = self._get(album="Something Else Entirely")
        self.assertIsInstance(result, NotFound)

    def test_duration_drift_is_refused(self):
        result = self._get(duration=260.0)
        self.assertIsInstance(result, NotFound)

    def test_same_title_other_version_is_refused(self):
        """A different edition of the same song must not borrow these lyrics."""
        self.cache.add_song(
            "555000", "Song", "Artist", "Album", duration_ms=260000
        )
        result = self._get(duration=260.0)
        # Either nothing matches, or the matching candidate is the right one.
        if hasattr(result, "lines"):
            self.assertEqual(result.catalogId, "555000")

    def test_normalize_text_is_stable(self):
        self.assertEqual(normalize_text("Ｈｅｌｌｏ－World (Live)"),
                         normalize_text("Hello World LIVE"))


class DiagnosticReasonTests(unittest.TestCase):
    def setUp(self):
        self.cache = TempCache()
        self.addCleanup(self.cache.close)

    def test_cache_missing_song(self):
        self.cache.add_song("123456", "Song", "Artist", "Album")
        provider = AppleMusicCacheProvider(scanner=CacheScanner(self.cache.db))
        result = provider.get_lyrics(CurrentTrack(
            title="Nothing Like This", artist="Nobody",
            album="Nowhere", duration=123.0,
        ))
        self.assertIsInstance(result, NotFound)
        self.assertEqual(result.reason, FailureReason.CACHE_MISSING_SONG)

    def test_incomplete_track_metadata(self):
        self.cache.add_song("123456", "Song", "Artist", "Album")
        provider = AppleMusicCacheProvider(scanner=CacheScanner(self.cache.db))
        result = provider.get_lyrics(CurrentTrack(
            title="", artist="Artist", album="Album", duration=200.0,
        ))
        self.assertIsInstance(result, lyrics_provider.InvalidMatch)
        self.assertEqual(result.reason, FailureReason.TRACK_METADATA_INCOMPLETE)

    def test_missing_database_reports_unreadable_cache(self):
        provider = AppleMusicCacheProvider(
            scanner=CacheScanner(self.cache.root / "absent.db")
        )
        result = provider.get_lyrics(CurrentTrack(
            title="Song", artist="Artist", album="Album", duration=200.0,
        ))
        self.assertEqual(result.reason, FailureReason.CACHE_UNREADABLE)

    def test_no_syllable_relationship(self):
        self.cache.add_song("123456", "Song", "Artist", "Album", ttml=None)
        provider = AppleMusicCacheProvider(scanner=CacheScanner(self.cache.db))
        result = provider.get_lyrics(CurrentTrack(
            title="Song", artist="Artist", album="Album", duration=200.0,
        ))
        self.assertEqual(result.reason, FailureReason.NO_SYLLABLE_RELATIONSHIP)

    def test_invalid_ttml(self):
        self.cache.add_song("123456", "Song", "Artist", "Album", ttml="<not-ttml")
        provider = AppleMusicCacheProvider(scanner=CacheScanner(self.cache.db))
        result = provider.get_lyrics(CurrentTrack(
            title="Song", artist="Artist", album="Album", duration=200.0,
        ))
        self.assertEqual(result.reason, FailureReason.TTML_INVALID)

    def test_reasons_are_stable_strings(self):
        for reason in FailureReason:
            self.assertIsInstance(reason.value, str)


class ParseReuseTests(unittest.TestCase):
    def setUp(self):
        self.cache = TempCache()
        self.addCleanup(self.cache.close)
        self.cache.add_song("123456", "Song", "Artist", "Album")

    def test_parse_runs_once_per_fingerprint(self):
        provider = AppleMusicCacheProvider(scanner=CacheScanner(self.cache.db))
        track = CurrentTrack(title="Song", artist="Artist", album="Album", duration=200.0)
        for _ in range(4):
            provider.get_lyrics(track)
        self.assertEqual(provider.parse_count, 1)
        self.assertEqual(provider.index_hits, 3)

    def test_parse_reruns_after_the_cache_changes(self):
        provider = AppleMusicCacheProvider(scanner=CacheScanner(self.cache.db))
        track = CurrentTrack(title="Song", artist="Artist", album="Album", duration=200.0)
        provider.get_lyrics(track)
        self.cache.add_song("222333", "Another", "Artist", "Album")
        provider.get_lyrics(track)
        self.assertEqual(provider.parse_count, 2)


if __name__ == "__main__":
    unittest.main()