import json
import io
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from xml.etree import ElementTree as ET

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

    def test_complete_match_with_incomplete_same_id_candidate_matches(self):
        incomplete = song_resource(
            title="Current Track",
            artist=None,
            album=None,
            duration_ms=None,
            ttml="",
        )
        responses = [
            response(song_resource()),
            response(incomplete),
        ]
        parsed = [CatalogResponseParser().parse(item) for item in responses]
        matcher = TrackMatcher()

        result = matcher.match(track(catalogId=TRACK_ID), responses, parsed)

        self.assertEqual(TRACK_ID, result.catalogId)
        self.assertEqual(LYRIC_TTML, result.lyrics[0].ttml)
        self.assertEqual({
            "candidateCount": 2,
            "completeCandidateCount": 1,
            "matchedCandidateCount": 1,
            "incompleteCandidateCount": 1,
            "finalStatus": "matched",
        }, matcher.last_diagnostics)

    def test_two_complete_consistent_same_id_candidates_match(self):
        result = match([
            song_resource(),
            song_resource(ttml=LYRIC_TTML.replace("Hello world", "duplicate")),
        ], track(catalogId=TRACK_ID))

        self.assertEqual(TRACK_ID, result.catalogId)
        self.assertEqual(2, len(result.lyrics))

    def test_two_complete_conflicting_same_id_candidates_are_invalid(self):
        result = match([
            song_resource(),
            song_resource(title="Different Complete Track"),
        ], track(catalogId=TRACK_ID))

        self.assertIsInstance(result, InvalidMatch)

    def test_complete_wrong_track_with_same_catalog_id_is_not_matched(self):
        result = match([
            song_resource(
                title="Different Complete Track",
                artist="Different Artist",
                album="Different Album",
                duration_ms=240000,
            ),
        ], track(catalogId=TRACK_ID))

        self.assertIsInstance(result, InvalidMatch)
        self.assertNotIsInstance(result, Lyrics)

    def test_only_incomplete_same_id_candidates_are_not_found(self):
        responses = [response(song_resource(
            title="Current Track",
            artist=None,
            album=None,
            duration_ms=None,
            ttml="",
        ))]
        parsed = [CatalogResponseParser().parse(item) for item in responses]
        matcher = TrackMatcher()
        result = matcher.match(track(catalogId=TRACK_ID), responses, parsed)

        self.assertIsInstance(result, NotFound)
        self.assertEqual("notFound", matcher.last_diagnostics["finalStatus"])

    def test_provider_selects_complete_match_over_incomplete_same_id_record(self):
        incomplete = song_resource(
            title="Current Track",
            artist=None,
            album=None,
            duration_ms=None,
            ttml="",
        )
        provider = AppleMusicCacheProvider(scanner=ProviderTests.FakeScanner([
            response(song_resource()),
            response(incomplete),
        ]))

        result = provider.get_lyrics(track(catalogId=TRACK_ID))

        self.assertIsInstance(result, Lyrics)
        self.assertEqual(TRACK_ID, result.catalogId)
        self.assertEqual(1, len(result.lines))
        self.assertEqual({
            "candidateCount": 2,
            "completeCandidateCount": 1,
            "matchedCandidateCount": 1,
            "incompleteCandidateCount": 1,
            "finalStatus": "matched",
        }, provider.matcher.last_diagnostics)

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
    FIXTURE = Path(__file__).parent / "fixtures" / "apple_music_syllable_timing.xml"

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

    def test_parses_bare_seconds_and_clock_time_formats(self):
        expected = {
            "41.955": 41.955,
            "0.500": 0.5,
            "1.0": 1.0,
            "1": 1.0,
            "00:41.955": 41.955,
            "00:00:41.955": 41.955,
            "00:01:02.500": 62.5,
            "500ms": 0.5,
            "0.5s": 0.5,
            "1m": 60.0,
            "1h": 3600.0,
        }
        for value, seconds in expected.items():
            with self.subTest(value=value):
                self.assertAlmostEqual(seconds, TTMLParser._parse_time(value))

    def test_absolute_child_timing_is_not_added_to_parent_twice(self):
        document = (
            "<tt xmlns='http://www.w3.org/ns/ttml'><body dur='300.395'>"
            "<div begin='111.437' end='143.591'>"
            "<p begin='111.437' end='118.419'>"
            "<span begin='111.437' end='112.000'>word</span>"
            "</p></div></body></tt>"
        )
        line = TTMLParser().parse(document).lines[0]
        self.assertAlmostEqual(111.437, line.startTime)
        self.assertAlmostEqual(118.419, line.endTime)
        self.assertAlmostEqual(111.437, line.words[0].startTime)
        self.assertAlmostEqual(112.0, line.words[0].endTime)

    def test_local_child_timing_is_still_relative_to_its_parent(self):
        document = (
            "<tt xmlns='http://www.w3.org/ns/ttml'><body>"
            "<div begin='10s' end='20s'><p begin='1s' end='3s'>local</p>"
            "</div></body></tt>"
        )
        line = TTMLParser().parse(document).lines[0]
        self.assertEqual(11.0, line.startTime)
        self.assertEqual(13.0, line.endTime)

    def test_local_times_inside_parent_range_are_not_mistaken_for_absolute(self):
        document = (
            "<tt xmlns='http://www.w3.org/ns/ttml'><body>"
            "<div begin='10s' end='20s'><p begin='11s' end='13s'>local</p>"
            "</div></body></tt>"
        )
        line = TTMLParser().parse(document).lines[0]
        self.assertEqual(21.0, line.startTime)
        self.assertEqual(23.0, line.endTime)

    def test_multiple_absolute_divisions_do_not_accumulate_drift(self):
        document = (
            "<tt xmlns='http://www.w3.org/ns/ttml'><body dur='300.395'>"
            "<div begin='41.955' end='76.242'>"
            "<p begin='41.955' end='48.922'>a</p>"
            "<p begin='49.360' end='57.903'>b</p></div>"
            "<div begin='111.437' end='143.591'>"
            "<p begin='111.437' end='118.419'>c</p>"
            "<p begin='118.942' end='126.942'>d</p></div>"
            "</body></tt>"
        )
        lines = TTMLParser().parse(document).lines
        self.assertEqual([41.955, 49.360, 111.437, 118.942],
                         [line.startTime for line in lines])

    def test_redacted_real_apple_music_fixture_preserves_all_main_lyric_lines(self):
        document = self.FIXTURE.read_text(encoding="utf-8")
        root = ET.fromstring(document)
        local_name = lambda element: element.tag.rsplit("}", 1)[-1]
        raw_paragraphs = [
            element for element in root.iter()
            if local_name(element) == "p"
        ]
        lyrics = TTMLParser().parse(document)

        self.assertEqual(18, len(raw_paragraphs))
        self.assertEqual(18, len(lyrics.lines))
        self.assertAlmostEqual(41.955, lyrics.lines[0].startTime)
        self.assertAlmostEqual(260.183, lyrics.lines[-1].startTime)
        self.assertAlmostEqual(270.680, lyrics.lines[-1].endTime)
        self.assertLess(lyrics.lines[-1].endTime, 300.395)
        self.assertFalse(any(line.startTime >= 500 for line in lyrics.lines))

    def test_real_fixture_restores_timed_words_without_changing_line_text(self):
        document = self.FIXTURE.read_text(encoding="utf-8")
        root = ET.fromstring(document)
        local_name = lambda element: element.tag.rsplit("}", 1)[-1]
        timed_spans = [
            element for element in root.iter()
            if local_name(element) == "span"
            and any(name in element.attrib for name in ("begin", "end", "dur"))
        ]
        lyrics = TTMLParser().parse(document)

        self.assertEqual(171, len(timed_spans))
        self.assertEqual(171, sum(len(line.words) for line in lyrics.lines))
        self.assertTrue(lyrics.hasWordTiming)
        self.assertEqual("x" * len(lyrics.lines[0].words), lyrics.lines[0].text)

    def test_untimed_span_inherits_parent_line_interval(self):
        document = (
            "<tt xmlns='http://www.w3.org/ns/ttml'><body>"
            "<p begin='1s' end='3s'><span>word</span></p>"
            "</body></tt>"
        )
        line = TTMLParser().parse(document).lines[0]
        self.assertEqual(1.0, line.words[0].startTime)
        self.assertEqual(3.0, line.words[0].endTime)

    def test_head_transliteration_is_not_parsed_as_an_extra_lyric_line(self):
        document = (
            "<tt xmlns='http://www.w3.org/ns/ttml'><head><metadata>"
            "<transliterations><text>romanized metadata</text></transliterations>"
            "</metadata></head><body><p begin='1s' end='2s'>main</p></body></tt>"
        )
        lyrics = TTMLParser().parse(document)
        self.assertEqual(1, len(lyrics.lines))
        self.assertEqual("main", lyrics.lines[0].text)

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
