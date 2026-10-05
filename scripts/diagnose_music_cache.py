#!/usr/bin/env python3
"""Offline, read-only diagnostic for Apple Music's cached syllable lyrics."""

from __future__ import annotations

import gzip
import io
import json
import re
import sqlite3
import subprocess
import sys
import unicodedata
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple
from xml.etree import ElementTree as ET


CACHE_DB = Path.home() / "Library/Caches/com.apple.Music/Cache.db"
MAX_BODY_BYTES = 32 * 1024 * 1024
FIELD_SEPARATOR = "\x1f"
MUSIC_SCRIPT = f'''
if application "Music" is not running then return "NOT_RUNNING"
tell application "Music"
  if not (exists current track) then return "NO_TRACK"
  set track_name to my safe_text(name of current track)
  set track_artist to my safe_text(artist of current track)
  set track_album to my safe_text(album of current track)
  set track_duration to my safe_text(duration of current track)
  set track_position to my safe_text(player position)
  set track_state to my safe_text(player state)
  return "OK{FIELD_SEPARATOR}" & track_name & "{FIELD_SEPARATOR}" & track_artist & "{FIELD_SEPARATOR}" & track_album & "{FIELD_SEPARATOR}" & track_duration & "{FIELD_SEPARATOR}" & track_position & "{FIELD_SEPARATOR}" & track_state
end tell
on safe_text(value_to_convert)
  try
    return value_to_convert as text
  on error
    return ""
  end try
end safe_text
'''
SAFE_PARAMETER_NAME = re.compile(r"^[A-Za-z0-9_.\[\]-]{1,100}$")
SENSITIVE_PARAMETER_NAME = re.compile(
    r"(?:auth|token|cookie|signature|sig|credential|password|secret|dsid|key)",
    re.IGNORECASE,
)
SAFE_STORE_FRONT = re.compile(r"^[a-z]{2,3}$")
TTML_OPEN = re.compile(r"<(?:[A-Za-z0-9_-]+:)?tt(?:\s|>)", re.IGNORECASE)
METADATA_TRANSLATION = str.maketrans({
    "親": "亲", "愛": "爱", "這": "这", "裡": "里", "裏": "里", "麼": "么",
    "為": "为", "會": "会", "聽": "听", "說": "说", "過": "过", "還": "还",
    "讓": "让", "總": "总", "個": "个", "夢": "梦", "見": "见", "倆": "俩",
    "問": "问", "現": "现", "給": "给", "機": "机", "開": "开", "聲": "声",
    "輕": "轻", "轉": "转", "離": "离", "歡": "欢", "關": "关", "緊": "紧",
    "處": "处", "間": "间", "嗎": "吗", "時": "时", "氣": "气", "東": "东",
    "萬": "万", "與": "与", "妳": "你", "夠": "够", "動": "动", "記": "记",
})


class DiagnosticError(Exception):
    """An expected failure with a safe, non-sensitive reason code."""


def normalize_metadata(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    value = unicodedata.normalize("NFKC", value.translate(METADATA_TRANSLATION)).casefold().strip()
    return re.sub(r"[\s\-_·・'’\"“”()\[\]（）。,.，:：!！?？]+", "", value)


def read_current_track() -> Dict[str, Any]:
    try:
        result = subprocess.run(
            ["osascript"],
            input=MUSIC_SCRIPT,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DiagnosticError("music_applescript_unavailable") from exc
    if result.returncode:
        raise DiagnosticError("music_applescript_failed")

    output = result.stdout.rstrip("\r\n")
    if output == "NOT_RUNNING":
        raise DiagnosticError("music_not_running")
    if output == "NO_TRACK":
        raise DiagnosticError("no_current_track")
    parts = output.split(FIELD_SEPARATOR, 6)
    if len(parts) != 7 or parts[0] != "OK":
        raise DiagnosticError("music_metadata_incomplete")
    try:
        duration = float(parts[4])
        position = float(parts[5])
    except ValueError as exc:
        raise DiagnosticError("music_timing_unreliable") from exc
    if not all((parts[1].strip(), parts[2].strip(), parts[3].strip())) or duration <= 0:
        raise DiagnosticError("music_metadata_incomplete")
    return {
        "title": parts[1],
        "artist": parts[2],
        "album": parts[3],
        "duration_seconds": duration,
        "playback_position_seconds": position,
        "playback_state": parts[6],
    }


def open_cache_read_only(cache_db: Path) -> sqlite3.Connection:
    if not cache_db.is_file():
        raise DiagnosticError("cache_db_missing")
    uri_path = urllib.parse.quote(str(cache_db.resolve()), safe="/")
    try:
        connection = sqlite3.connect("file:" + uri_path + "?mode=ro", uri=True, timeout=5)
        connection.execute("PRAGMA query_only = ON")
        return connection
    except sqlite3.Error as exc:
        raise DiagnosticError("cache_db_open_failed") from exc


def safe_endpoint(request_key: str) -> Optional[Dict[str, Any]]:
    if not isinstance(request_key, str):
        return None
    try:
        parsed = urllib.parse.urlsplit(request_key)
    except ValueError:
        return None
    if parsed.hostname != "amp-api.music.apple.com":
        return None
    match = re.fullmatch(r"/v1/catalog/([a-z]{2,3})/songs", parsed.path)
    if not match:
        return None
    storefront = match.group(1)
    if not SAFE_STORE_FRONT.fullmatch(storefront):
        return None
    try:
        parameter_names = sorted({
            key for key, _ in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
            if SAFE_PARAMETER_NAME.fullmatch(key)
            and not SENSITIVE_PARAMETER_NAME.search(key)
        })
    except ValueError:
        return None
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    extend_lyrics = (
        "extend[syllable-lyrics]" in query
        or any(
            key == "extend" and any("syllable-lyrics" in value.split(",") for value in values)
            for key, values in query.items()
        )
    )
    return {
        "host": "amp-api.music.apple.com",
        "path": "/v1/catalog/" + storefront + "/songs",
        "storefront": storefront,
        "parameter_names": parameter_names,
        "requests_syllable_lyrics": extend_lyrics,
        "requested_song_ids": sorted(query_song_ids(query)),
    }


def query_song_ids(query: Dict[str, Sequence[str]]) -> Set[str]:
    ids: Set[str] = set()
    for key, values in query.items():
        if key in ("ids[songs]", "id", "ids"):
            for value in values:
                ids.update(re.findall(r"(?<!\d)\d{6,}(?!\d)", value))
    return ids


def read_response_body(
    is_data_on_fs: Any,
    receiver_data: Any,
    fs_cache_dir: Path,
) -> Tuple[Optional[bytes], str]:
    if receiver_data is None:
        return None, "body_reference_missing"
    if is_data_on_fs:
        if isinstance(receiver_data, bytes):
            try:
                filename = receiver_data.decode("ascii")
            except UnicodeDecodeError:
                return None, "invalid_fs_reference"
        elif isinstance(receiver_data, str):
            filename = receiver_data
        else:
            return None, "invalid_fs_reference"
        if (not filename or Path(filename).name != filename or filename in (".", "..")
                or "\x00" in filename):
            return None, "invalid_fs_reference"
        try:
            cache_root = fs_cache_dir.resolve()
            target = fs_cache_dir / filename
            if target.is_symlink() or target.resolve().parent != cache_root or not target.is_file():
                return None, "fs_body_missing"
            if target.stat().st_size > MAX_BODY_BYTES:
                return None, "body_too_large"
            return target.read_bytes(), "read"
        except OSError:
            return None, "fs_body_unreadable"
    if isinstance(receiver_data, str):
        body = receiver_data.encode("utf-8", "replace")
    elif isinstance(receiver_data, bytes):
        body = receiver_data
    else:
        return None, "invalid_inline_body"
    if len(body) > MAX_BODY_BYTES:
        return None, "body_too_large"
    return body, "read"


def parse_json_body(body: bytes) -> Optional[Any]:
    if body.startswith(b"\x1f\x8b"):
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(body)) as compressed:
                body = compressed.read(MAX_BODY_BYTES + 1)
            if len(body) > MAX_BODY_BYTES:
                return None
        except (OSError, EOFError):
            return None
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def song_resources(value: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(value, dict):
        if value.get("type") == "songs" and isinstance(value.get("attributes"), dict):
            yield value
        for child in value.values():
            yield from song_resources(child)
    elif isinstance(value, list):
        for child in value:
            yield from song_resources(child)


def song_metadata(resource: Dict[str, Any]) -> Dict[str, Any]:
    attributes = resource.get("attributes", {})
    return {
        "id": str(resource.get("id", "")),
        "title": attributes.get("name"),
        "artist": attributes.get("artistName"),
        "album": attributes.get("albumName"),
        "duration_ms": attributes.get("durationInMillis"),
    }


def metadata_matches(track: Dict[str, Any], cached: Dict[str, Any]) -> bool:
    required_track_fields = ("title", "artist", "album")
    required_cached_fields = ("title", "artist", "album")
    if any(not normalize_metadata(track.get(key)) for key in required_track_fields):
        return False
    if any(not normalize_metadata(cached.get(key)) for key in required_cached_fields):
        return False
    if not all(
        normalize_metadata(track[key]) == normalize_metadata(cached[key])
        for key in required_track_fields
    ):
        return False
    try:
        current_ms = float(track["duration_seconds"]) * 1000
        cached_ms = float(cached["duration_ms"])
    except (KeyError, TypeError, ValueError):
        return False
    if current_ms <= 0 or cached_ms <= 0 or abs(current_ms - cached_ms) > 2000:
        return False
    return bool(re.fullmatch(r"\d{6,}", str(cached.get("id", ""))))


def metadata_catalog_resolution(
    track: Dict[str, Any],
    cache_records: Sequence[Dict[str, Any]],
) -> Tuple[str, Optional[str], int]:
    matched: Dict[str, Dict[str, Any]] = {}
    conflicting_ids: Set[str] = set()
    for record in cache_records:
        for resource in song_resources(record.get("payload")):
            metadata = song_metadata(resource)
            if not metadata_matches(track, metadata):
                continue
            requested_ids = record.get("endpoint", {}).get("requested_song_ids", set())
            if requested_ids and metadata["id"] not in requested_ids:
                continue
            candidate_id = metadata["id"]
            previous = matched.get(candidate_id)
            if previous and any(previous.get(key) != metadata.get(key)
                                for key in ("title", "artist", "album", "duration_ms")):
                conflicting_ids.add(candidate_id)
            matched[candidate_id] = metadata
    for candidate_id in conflicting_ids:
        matched.pop(candidate_id, None)
    if len(matched) == 1:
        return "resolved", next(iter(matched)), 1
    if len(matched) > 1:
        return "ambiguous", None, len(matched)
    return "unresolved", None, 0


def ttml_values(localizations: Any) -> List[str]:
    values: List[str] = []
    if isinstance(localizations, str):
        if TTML_OPEN.search(localizations):
            values.append(localizations)
    elif isinstance(localizations, dict):
        for value in localizations.values():
            values.extend(ttml_values(value))
    elif isinstance(localizations, list):
        for value in localizations:
            values.extend(ttml_values(value))
    return values


def parse_ttml_line_count(ttml: str) -> Optional[int]:
    try:
        root = ET.fromstring(ttml)
    except ET.ParseError:
        return None
    count = 0
    for paragraph in root.iter():
        if paragraph.tag.rsplit("}", 1)[-1] != "p":
            continue
        if "".join(paragraph.itertext()).strip():
            count += 1
    return count if count > 0 else None


def relationship_evidence(
    resource: Dict[str, Any],
    requested_song_ids: Sequence[str],
) -> List[Dict[str, Any]]:
    relationships = resource.get("relationships")
    if not isinstance(relationships, dict):
        return []
    lyrics = relationships.get("syllable-lyrics")
    if not isinstance(lyrics, dict):
        return []
    lyric_resources = lyrics.get("data")
    if isinstance(lyric_resources, dict):
        lyric_resources = [lyric_resources]
    if not isinstance(lyric_resources, list):
        return []
    evidence = []
    parent_id = str(resource.get("id", ""))
    for lyric_resource in lyric_resources:
        if not isinstance(lyric_resource, dict):
            continue
        attributes = lyric_resource.get("attributes", {})
        if not isinstance(attributes, dict):
            continue
        play_params = attributes.get("playParams", {})
        if not isinstance(play_params, dict):
            play_params = {}
        catalog_id = str(play_params.get("catalogId", ""))
        localizations = attributes.get("ttmlLocalizations")
        documents = ttml_values(localizations)
        parsed_counts = [
            line_count for line_count in (parse_ttml_line_count(doc) for doc in documents)
            if line_count is not None
        ]
        evidence.append({
            "parent_song_id_matches_lyric_catalog_id": bool(parent_id and catalog_id == parent_id),
            "parent_song_id_present": bool(parent_id),
            "lyric_catalog_id_present": bool(catalog_id),
            "request_id_matches_parent_song": (
                not requested_song_ids or parent_id in requested_song_ids
            ),
            "ttml_document_count": len(documents),
            "parseable_ttml_document_count": len(parsed_counts),
            "parsed_line_count": max(parsed_counts) if parsed_counts else 0,
        })
    return evidence


def collect_cache_records(cache_db: Path) -> Dict[str, Any]:
    connection = open_cache_read_only(cache_db)
    fs_cache_dir = cache_db.parent / "fsCachedData"
    try:
        table_names = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        required = {
            "cfurl_cache_response", "cfurl_cache_blob_data", "cfurl_cache_receiver_data"
        }
        if not required.issubset(table_names):
            raise DiagnosticError("cache_schema_unsupported")
        wal_exists = Path(str(cache_db) + "-wal").is_file()
        total_entries = connection.execute(
            "SELECT COUNT(*) FROM cfurl_cache_response"
        ).fetchone()[0]
        sql = """
            SELECT r.request_key, d.isDataOnFS, d.receiver_data
            FROM cfurl_cache_response AS r
            LEFT JOIN cfurl_cache_receiver_data AS d USING (entry_ID)
        """
        rows = connection.execute(sql).fetchall()
        records = []
        missing_or_unreadable_body_count = 0
        fs_body_count = 0
        for request_key, is_data_on_fs, receiver_data in rows:
            endpoint = safe_endpoint(request_key)
            if endpoint is None:
                continue
            if is_data_on_fs:
                fs_body_count += 1
            body, body_status = read_response_body(
                is_data_on_fs, receiver_data, fs_cache_dir
            )
            if body is None:
                missing_or_unreadable_body_count += 1
                payload = None
            else:
                payload = parse_json_body(body)
                if payload is None:
                    missing_or_unreadable_body_count += 1
            records.append({"endpoint": endpoint, "payload": payload})
        return {
            "total_cache_entries": total_entries,
            "wal_present": wal_exists,
            "songs_endpoint_records": len(records),
            "syllable_lyrics_request_records": sum(
                bool(record["endpoint"]["requests_syllable_lyrics"]) for record in records
            ),
            "fs_backed_songs_bodies": fs_body_count,
            "unreadable_or_non_json_songs_bodies": missing_or_unreadable_body_count,
            "records": records,
        }
    except sqlite3.Error as exc:
        raise DiagnosticError("cache_read_failed") from exc
    finally:
        connection.close()


def build_diagnostic(track: Dict[str, Any], cache_data: Dict[str, Any]) -> Dict[str, Any]:
    records = cache_data["records"]
    resolution, current_catalog_id, candidate_count = metadata_catalog_resolution(track, records)
    endpoint_counts: Dict[Tuple[str, str], Dict[str, Any]] = {}
    global_ttml_documents = 0
    global_parseable_documents = 0
    global_max_line_count = 0
    related_evidence = []

    for record in records:
        endpoint = record["endpoint"]
        key = (endpoint["host"], endpoint["path"])
        summary = endpoint_counts.setdefault(key, {
            "endpoint": endpoint["host"] + endpoint["path"],
            "storefront": endpoint["storefront"],
            "parameter_names": set(),
            "request_count": 0,
            "syllable_lyrics_request_count": 0,
        })
        summary["request_count"] += 1
        summary["parameter_names"].update(endpoint["parameter_names"])
        if endpoint["requests_syllable_lyrics"]:
            summary["syllable_lyrics_request_count"] += 1

        payload = record.get("payload")
        if payload is None:
            continue
        for resource in song_resources(payload):
            parent_id = str(resource.get("id", ""))
            if endpoint["requests_syllable_lyrics"]:
                for evidence in relationship_evidence(
                    resource, endpoint["requested_song_ids"]
                ):
                    global_ttml_documents += evidence["ttml_document_count"]
                    global_parseable_documents += evidence["parseable_ttml_document_count"]
                    global_max_line_count = max(
                        global_max_line_count, evidence["parsed_line_count"]
                    )
                    if current_catalog_id and parent_id == current_catalog_id:
                        related_evidence.append(evidence)

    strict_link = bool(
        resolution == "resolved"
        and current_catalog_id
        and related_evidence
        and all(item["parent_song_id_matches_lyric_catalog_id"] for item in related_evidence)
        and all(item["request_id_matches_parent_song"] for item in related_evidence)
        and all(item["ttml_document_count"] > 0 for item in related_evidence)
        and all(item["parseable_ttml_document_count"] > 0 for item in related_evidence)
    )
    endpoint_summaries = []
    for summary in sorted(endpoint_counts.values(), key=lambda item: item["endpoint"]):
        summary["parameter_names"] = sorted(summary["parameter_names"])
        endpoint_summaries.append(summary)

    return {
        "current_track": track,
        "cache": {
            key: value for key, value in cache_data.items() if key != "records"
        },
        "catalog_id": {
            "status": resolution,
            "value": current_catalog_id,
            "candidate_count": candidate_count,
            "evidence": "unique local catalog song resource with exact title, artist, album and duration match",
        },
        "cached_requests": endpoint_summaries,
        "syllable_lyrics": {
            "request_records": cache_data["syllable_lyrics_request_records"],
            "ttml_documents_found": global_ttml_documents,
            "parseable_ttml_documents": global_parseable_documents,
            "maximum_parseable_line_count": global_max_line_count,
            "current_song_link_evidence_records": len(related_evidence),
        },
        "ttml_catalog_id_link": {
            "all_nested_catalog_ids_match_parent_song": bool(related_evidence) and all(
                item["parent_song_id_matches_lyric_catalog_id"]
                for item in related_evidence
            ),
            "all_request_ids_match_parent_song": bool(related_evidence) and all(
                item["request_id_matches_parent_song"]
                for item in related_evidence
            ),
        },
        "belongs_to_current_song": strict_link,
    }


def main() -> int:
    try:
        track = read_current_track()
        cache_data = collect_cache_records(CACHE_DB)
        report = build_diagnostic(track, cache_data)
    except DiagnosticError as exc:
        report = {
            "status": "partial",
            "reason": str(exc),
            "belongs_to_current_song": False,
        }
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
