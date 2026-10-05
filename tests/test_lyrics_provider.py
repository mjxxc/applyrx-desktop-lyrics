import json
import io
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import lyrics_demo
from lyrics_provider import (
    Ambiguous,
    AppleMusicCacheProvider,
    CacheResponse,
    CacheScanner,
    CatalogResponseParser,
    CurrentTrack,
    InvalidMatch,
    Lyrics,
    NotFound,
    TTMLParseError,
    TTMLParser,
    TrackMatcher,
)


TRACK_ID = "123456789"
LYRIC_TTML = (
    "<tt xmlns='http://www.w3.org/ns/ttml' xml:lang='en'>"
    "<body><div><p begin='00:00:01.000' end='00:00:03.000'>"
    "Hello world</p></div></body></tt>"
)


def track(**changes):
    values = {
        "title": "Current Track",
        "artist": "Current Artist",
        "album": "Current Album",
        "duration": 180.0,
    }
    values.update(changes)
    return CurrentTrack(**values)


def song_resource(
    song_id=TRACK_ID,
    title="Current Track",
    artist="Current Artist",
    album="Current Album",
    duration_ms=180000,
    lyric_catalog_id=TRACK_ID,
    ttml=LYRIC_TTML,
):
    lyric = {
        "id": "lyric-resource",
        "type": "syllable-lyrics",
        "attributes": {
            "playParams": {"catalogId": lyric_catalog_id},
            "ttmlLocalizations": ttml,
        },
    }
    return {
        "id": song_id,
        "type": "songs",
        "attributes": {
            "name": title,
            "artistName": artist,
            "albumName": album,
            "durationInMillis": duration_ms,
        },
        "relationships": {"syllable-lyrics": {"data": [lyric]}},
    }


def response(resource, requested_ids=()):
    return CacheResponse(
        endpoint="/v1/catalog/cn/songs",
        storefront="cn",
        parameterNames=("extend[syllable-lyrics]",),
        requestedSongIds=frozenset(requested_ids),
        payload={"data": [resource]},
    )


def match(resources, current_track=None):
    responses = [response(resource) for resource in resources]
    parsed = [CatalogResponseParser().parse(item) for item in responses]
    return TrackMatcher().match(current_track or track(), responses, parsed)


class TrackMatchingTests(unittest.TestCase):
    def test_normal_strict_metadata_match(self):
        self.assertEqual(TRACK_ID, match([song_resource()]).catalogId)

    def test_explicit_catalog_id_match(self):
        result = match([song_resource()], track(catalogId=TRACK_ID))
        self.assertEqual(TRACK_ID, result.catalogId)

    def test_explicit_catalog_id_takes_priority_over_duplicate_metadata(self):
        result = match([
            song_resource(song_id=TRACK_ID),
            song_resource(
                song_id="987654321",
                lyric_catalog_id="987654321",
                ttml=LYRIC_TTML.replace("Hello world", "OTHER_CATALOG_ENTRY"),
            ),
        ], track(catalogId=TRACK_ID))
        self.assertEqual(TRACK_ID, result.catalogId)
        self.assertEqual(TRACK_ID, result.lyrics[0].catalogId)

    def test_title_mismatch_for_catalog_id_is_invalid(self):
        result = match([song_resource(title="Different")], track(catalogId=TRACK_ID))
        self.assertIsInstance(result, InvalidMatch)

    def test_artist_mismatch_is_not_accepted(self):
        self.assertIsInstance(match([song_resource(artist="Different")]), NotFound)

    def test_album_mismatch_is_not_accepted(self):
        self.assertIsInstance(match([song_resource(album="Different Album")]), NotFound)

    def test_missing_current_track_field_is_invalid(self):
        result = match(
            [song_resource()],
            CurrentTrack(title="Current Track", artist="Current Artist", album="", duration=180),
        )
        self.assertIsInstance(result, InvalidMatch)

    def test_duration_two_second_tolerance_is_inclusive(self):
        result = match([song_resource(duration_ms=182000)])
        self.assertEqual(TRACK_ID, result.catalogId)

    def test_duration_outside_tolerance_is_not_accepted(self):
        self.assertIsInstance(match([song_resource(duration_ms=182001)]), NotFound)

    def test_multiple_matching_catalog_ids_are_ambiguous(self):
        result = match([
            song_resource(song_id="123456789"),
            song_resource(song_id="987654321", lyric_catalog_id="987654321"),
        ])
        self.assertIsInstance(result, Ambiguous)

    def test_no_matching_cached_song_is_not_found(self):
        result = match([song_resource(title="Different")])
        self.assertIsInstance(result, NotFound)

    def test_request_id_conflicting_with_song_resource_is_invalid(self):
        responses = [response(song_resource(), requested_ids={"987654321"})]
        parsed = [CatalogResponseParser().parse(item) for item in responses]
        result = TrackMatcher().match(track(), responses, parsed)
        self.assertIsInstance(result, InvalidMatch)

    def test_request_id_conflict_with_supplied_catalog_id_is_invalid(self):
        responses = [response(song_resource(), requested_ids={"987654321"})]
        parsed = [CatalogResponseParser().parse(item) for item in responses]
        result = TrackMatcher().match(track(catalogId=TRACK_ID), responses, parsed)
        self.assertIsInstance(result, InvalidMatch)

    def test_catalog_id_conflicting_with_matching_metadata_is_invalid(self):
        result = match(
            [song_resource(song_id="987654321", lyric_catalog_id="987654321")],
            track(catalogId=TRACK_ID),
        )
        self.assertIsInstance(result, InvalidMatch)

    def test_conflicting_metadata_for_same_catalog_id_is_invalid(self):
        result = match([
            song_resource(),
            song_resource(title="Conflicting Title"),
        ])
        self.assertIsInstance(result, InvalidMatch)

    def test_track_a_never_receives_track_b_cached_lyrics(self):
        track_a = track(catalogId=TRACK_ID)
        resource_b = song_resource(
            song_id="987654321",
            title="Track B",
            artist="Artist B",
            album="Album B",
            lyric_catalog_id="987654321",
            ttml=LYRIC_TTML.replace("Hello world", "TRACK_B_LYRIC_SENTINEL"),
        )
        result = AppleMusicCacheProvider(
            scanner=ProviderTests.FakeScanner([response(resource_b)])
        ).get_lyrics(track_a)
        self.assertIsInstance(result, NotFound)
        self.assertNotIsInstance(result, Lyrics)

    def test_track_a_metadata_cannot_accept_track_b_lyric_relationship(self):
        resource_a_with_b_lyrics = song_resource(lyric_catalog_id="987654321")
        result = AppleMusicCacheProvider(
            scanner=ProviderTests.FakeScanner([response(resource_a_with_b_lyrics)])
        ).get_lyrics(track())
        self.assertIsInstance(result, InvalidMatch)
        self.assertNotIsInstance(result, Lyrics)

    def test_valid_catalog_id_does_not_bypass_metadata_validation(self):
        result = match(
            [song_resource(artist="Different Artist")],
            track(catalogId=TRACK_ID),
        )
        self.assertIsInstance(result, InvalidMatch)

    def test_cached_ttml_for_track_b_is_not_returned_for_track_a(self):
        track_a = track()
        resource_a_with_track_b_ttml = song_resource(
            lyric_catalog_id="987654321",
            ttml=LYRIC_TTML.replace("Hello world", "TRACK_B_ONLY"),
        )
        result = AppleMusicCacheProvider(
            scanner=ProviderTests.FakeScanner([
                response(resource_a_with_track_b_ttml)
            ])
        ).get_lyrics(track_a)
        self.assertIsInstance(result, InvalidMatch)
        self.assertNotIsInstance(result, Lyrics)


class TTMLParserTests(unittest.TestCase):
    def test_parses_timed_lines_and_inherited_language(self):
        document = (
            "<tt xmlns='http://www.w3.org/ns/ttml' xml:lang='zh-Hans'>"
            "<body><div><p begin='00:00:01.500' end='00:00:03.000'>Line</p>"
            "</div></body></tt>"
        )
        lyrics = TTMLParser().parse(document)
        self.assertEqual("zh-Hans", lyrics.language)
        self.assertEqual(1.5, lyrics.lines[0].startTime)
        self.assertEqual(3.0, lyrics.lines[0].endTime)
        self.assertFalse(lyrics.hasWordTiming)

    def test_parses_word_timing_spans(self):
        document = (
            "<tt xmlns='http://www.w3.org/ns/ttml' xml:lang='en'>"
            "<body><p begin='00:00:01.000' end='00:00:03.000'>"
            "<span begin='00:00:00.000' end='00:00:00.700'>Hello</span>"
            "<span begin='00:00:00.700' end='00:00:02.000'>world</span>"
            "</p></body></tt>"
        )
        lyrics = TTMLParser().parse(document)
        self.assertTrue(lyrics.hasWordTiming)
        self.assertEqual(2, len(lyrics.lines[0].words))
        self.assertEqual(1.0, lyrics.lines[0].words[0].startTime)
        self.assertEqual(1.7, lyrics.lines[0].words[1].startTime)

    def test_rejects_ttml_without_a_time_axis(self):
        with self.assertRaises(TTMLParseError):
            TTMLParser().parse("<tt><body><p>Untimed</p></body></tt>")

    def test_rejects_ttml_with_only_an_end_time(self):
        with self.assertRaises(TTMLParseError):
            TTMLParser().parse(
                "<tt><body><p end='00:00:03.000'>Missing start</p></body></tt>"
            )

    def test_rejects_malformed_ttml(self):
        with self.assertRaises(TTMLParseError):
            TTMLParser().parse("<tt><body>")


class ProviderTests(unittest.TestCase):
    class FakeScanner:
        def __init__(self, responses):
            self.responses = responses

        def scan(self):
            return self.responses

    def test_provider_requires_related_catalog_id(self):
        result = AppleMusicCacheProvider(
            scanner=self.FakeScanner([
                response(song_resource(lyric_catalog_id="987654321"))
            ])
        ).get_lyrics(track())
        self.assertIsInstance(result, InvalidMatch)

    def test_provider_reports_unparseable_cached_ttml(self):
        result = AppleMusicCacheProvider(
            scanner=self.FakeScanner([
                response(song_resource(ttml="<tt><body><p>Untimed</p></body></tt>"))
            ])
        ).get_lyrics(track())
        self.assertIsInstance(result, NotFound)

    def test_provider_returns_timed_lyrics(self):
        result = AppleMusicCacheProvider(
            scanner=self.FakeScanner([response(song_resource())])
        ).get_lyrics(track())
        self.assertIsInstance(result, Lyrics)
        self.assertEqual(1, len(result.lines))
        self.assertEqual("en", result.language)


class CacheScannerTests(unittest.TestCase):
    def make_cache(self, cache_db, resource):
        fs_cache = cache_db.parent / "fsCachedData"
        fs_cache.mkdir()
        filename = "b" * 36
        payload = {"data": [resource]}
        (fs_cache / filename).write_text(json.dumps(payload), encoding="utf-8")
        connection = sqlite3.connect(str(cache_db))
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript("""
            CREATE TABLE cfurl_cache_response (
                entry_ID INTEGER PRIMARY KEY,
                request_key TEXT
            );
            CREATE TABLE cfurl_cache_receiver_data (
                entry_ID INTEGER PRIMARY KEY,
                isDataOnFS INTEGER,
                receiver_data BLOB
            );
        """)
        request_key = (
            "https://amp-api.music.apple.com/v1/catalog/cn/songs"
            "?ids%5Bsongs%5D=123456789&extend%5Bsyllable-lyrics%5D=1"
        )
        connection.execute(
            "INSERT INTO cfurl_cache_response VALUES (?, ?)",
            (1, request_key),
        )
        connection.execute(
            "INSERT INTO cfurl_cache_receiver_data VALUES (?, ?, ?)",
            (1, 1, filename),
        )
        connection.commit()
        return connection

    def test_reads_wal_and_filesystem_body_without_mutating_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache_db = root / "Cache.db"
            connection = self.make_cache(cache_db, song_resource())
            before = cache_db.stat().st_size
            responses = CacheScanner(cache_db).scan()
            self.assertTrue((root / "Cache.db-wal").exists())
            self.assertEqual(1, len(responses))
            self.assertEqual(1, len(CatalogResponseParser().parse(responses[0])))
            self.assertEqual(before, cache_db.stat().st_size)
            connection.close()

    def test_fixed_current_track_cli_displays_only_first_five_timed_lines(self):
        lines = "".join(
            "<p begin='00:00:{:02d}.000' end='00:00:{:02d}.000'>LINE_{}</p>".format(
                index, index + 1, index
            )
            for index in range(1, 7)
        )
        ttml = (
            "<tt xmlns='http://www.w3.org/ns/ttml' xml:lang='en'>"
            "<body><div>{}</div></body></tt>"
        ).format(lines)
        resource = song_resource(ttml=ttml)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache_db = root / "Cache.db"
            connection = self.make_cache(cache_db, resource)
            track_file = root / "track.json"
            track_file.write_text(json.dumps({
                "title": "Current Track",
                "artist": "Current Artist",
                "album": "Current Album",
                "duration": 180,
                "catalogId": TRACK_ID,
            }), encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                status = lyrics_demo.main([
                    "--track-json", str(track_file),
                    "--cache-db", str(cache_db),
                ])
            rendered = output.getvalue()
            self.assertEqual(0, status)
            self.assertIn("Current Track:", rendered)
            self.assertIn("Catalog ID: matched (redacted)", rendered)
            self.assertIn("MATCHED", rendered)
            self.assertIn("Word timing: no", rendered)
            self.assertIn("[00:00:01.000 - 00:00:02.000] LINE_1", rendered)
            self.assertIn("LINE_5", rendered)
            self.assertNotIn("LINE_6", rendered)
            self.assertNotIn(TRACK_ID, rendered)
            connection.close()


if __name__ == "__main__":
    unittest.main()
