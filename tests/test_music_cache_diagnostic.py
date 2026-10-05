import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "diagnose_music_cache.py"
SPEC = importlib.util.spec_from_file_location("diagnose_music_cache", MODULE_PATH)
diagnostic = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(diagnostic)


def song_resource(
    song_id="123456789",
    title="Current Track",
    artist="Current Artist",
    album="Current Album",
    duration_ms=180000,
    lyric_catalog_id=None,
    ttml="<tt xmlns='http://www.w3.org/ns/ttml'><body><p begin='00:00:01.000'>x</p></body></tt>",
):
    lyric = {
        "id": "lyric-resource",
        "type": "syllable-lyrics",
        "attributes": {
            "playParams": {"catalogId": lyric_catalog_id or song_id},
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
        "relationships": {
            "syllable-lyrics": {"data": [lyric]},
        },
    }


def record(resource, requested_ids=None, lyrical=True):
    endpoint = {
        "host": "amp-api.music.apple.com",
        "path": "/v1/catalog/cn/songs",
        "storefront": "cn",
        "parameter_names": ["extend[syllable-lyrics]"] if lyrical else ["ids[songs]"],
        "requests_syllable_lyrics": lyrical,
        "requested_song_ids": set(requested_ids or []),
    }
    return {"endpoint": endpoint, "payload": {"data": [resource]}}


class MetadataMatchingTests(unittest.TestCase):
    def setUp(self):
        self.track = {
            "title": "Current Track",
            "artist": "Current Artist",
            "album": "Current Album",
            "duration_seconds": 180,
            "playback_position_seconds": 10,
            "playback_state": "playing",
        }

    def test_resolves_unique_local_song_resource(self):
        result = diagnostic.metadata_catalog_resolution(
            self.track, [record(song_resource(), requested_ids={"123456789"}, lyrical=False)]
        )
        self.assertEqual(("resolved", "123456789", 1), result)

    def test_rejects_loose_or_incomplete_metadata(self):
        partial_track = dict(self.track, album="")
        result = diagnostic.metadata_catalog_resolution(
            partial_track, [record(song_resource(), lyrical=False)]
        )
        self.assertEqual(("unresolved", None, 0), result)

    def test_rejects_ambiguous_same_metadata_with_different_ids(self):
        result = diagnostic.metadata_catalog_resolution(self.track, [
            record(song_resource(song_id="123456789"), lyrical=False),
            record(song_resource(song_id="987654321"), lyrical=False),
        ])
        self.assertEqual(("ambiguous", None, 2), result)

    def test_rejects_request_and_response_id_conflict(self):
        result = diagnostic.metadata_catalog_resolution(
            self.track, [record(song_resource(), requested_ids={"987654321"}, lyrical=False)]
        )
        self.assertEqual(("unresolved", None, 0), result)


class CacheSafetyTests(unittest.TestCase):
    def test_endpoint_summary_discards_query_values(self):
        endpoint = diagnostic.safe_endpoint(
            "https://amp-api.music.apple.com/v1/catalog/cn/songs"
            "?ids%5Bsongs%5D=123456789&extend%5Bsyllable-lyrics%5D=1"
            "&auth-token=secret-value"
        )
        self.assertEqual("/v1/catalog/cn/songs", endpoint["path"])
        self.assertTrue(endpoint["requests_syllable_lyrics"])
        public_summary = {
            key: endpoint[key]
            for key in (
                "host", "path", "storefront", "parameter_names", "requests_syllable_lyrics"
            )
        }
        self.assertNotIn("secret-value", json.dumps(public_summary))
        self.assertNotIn("123456789", json.dumps(public_summary))
        self.assertNotIn("authorization", endpoint["parameter_names"])

    def test_fs_reference_cannot_escape_cache_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_dir = Path(directory)
            outside = cache_dir.parent / "outside-cache-test-body"
            outside.write_bytes(b"secret")
            try:
                body, status = diagnostic.read_response_body(
                    1, "../outside-cache-test-body", cache_dir
                )
                self.assertIsNone(body)
                self.assertEqual("invalid_fs_reference", status)
            finally:
                outside.unlink()

    def test_reads_filesystem_body_by_cache_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_dir = Path(directory)
            body_content = b'{"data":[]}'
            (cache_dir / "cache-body-name").write_bytes(body_content)
            body, status = diagnostic.read_response_body(
                1, "cache-body-name", cache_dir
            )
            self.assertEqual("read", status)
            self.assertEqual(body_content, body)


class TtmlOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.track = {
            "title": "Current Track",
            "artist": "Current Artist",
            "album": "Current Album",
            "duration_seconds": 180,
            "playback_position_seconds": 1,
            "playback_state": "playing",
        }
        self.cache_template = {
            "total_cache_entries": 1,
            "wal_present": True,
            "songs_endpoint_records": 1,
            "syllable_lyrics_request_records": 1,
            "fs_backed_songs_bodies": 0,
            "unreadable_or_non_json_songs_bodies": 0,
        }

    def test_reports_true_only_for_nested_matching_catalog_id(self):
        resource = song_resource(
            lyric_catalog_id="123456789",
            ttml="<tt xmlns='http://www.w3.org/ns/ttml'><body>"
            "<p begin='00:00:01.000'>PRIVATE_LYRIC_SENTINEL</p></body></tt>",
        )
        report = diagnostic.build_diagnostic(
            self.track,
            dict(self.cache_template, records=[record(resource)]),
        )
        self.assertTrue(report["belongs_to_current_song"])
        self.assertEqual(1, report["syllable_lyrics"]["parseable_ttml_documents"])
        self.assertEqual(1, report["syllable_lyrics"]["maximum_parseable_line_count"])
        self.assertNotIn("PRIVATE_LYRIC_SENTINEL", json.dumps(report))

    def test_rejects_mismatched_nested_catalog_id(self):
        resource = song_resource(lyric_catalog_id="987654321")
        report = diagnostic.build_diagnostic(
            self.track,
            dict(self.cache_template, records=[record(resource)]),
        )
        self.assertFalse(report["belongs_to_current_song"])
        self.assertFalse(report["ttml_catalog_id_link"]["all_nested_catalog_ids_match_parent_song"])

    def test_rejects_request_id_that_disagrees_with_song_resource(self):
        resource = song_resource(lyric_catalog_id="123456789")
        report = diagnostic.build_diagnostic(
            self.track,
            dict(
                self.cache_template,
                records=[record(resource, requested_ids={"987654321"})],
            ),
        )
        self.assertFalse(report["belongs_to_current_song"])
        self.assertFalse(report["ttml_catalog_id_link"]["all_request_ids_match_parent_song"])

    def test_rejects_ttML_that_cannot_be_parsed(self):
        resource = song_resource(
            lyric_catalog_id="123456789",
            ttml="<tt><body>PRIVATE_LYRIC_SENTINEL</tt>",
        )
        report = diagnostic.build_diagnostic(
            self.track,
            dict(self.cache_template, records=[record(resource)]),
        )
        self.assertFalse(report["belongs_to_current_song"])
        self.assertEqual(0, report["syllable_lyrics"]["parseable_ttml_documents"])
        self.assertNotIn("PRIVATE_LYRIC_SENTINEL", json.dumps(report))

    def test_no_lyric_relationship_means_no_confirmation(self):
        resource = song_resource()
        resource.pop("relationships")
        report = diagnostic.build_diagnostic(
            self.track,
            dict(self.cache_template, records=[record(resource, lyrical=False)]),
        )
        self.assertFalse(report["belongs_to_current_song"])
        self.assertEqual(0, report["syllable_lyrics"]["ttml_documents_found"])


if __name__ == "__main__":
    unittest.main()
