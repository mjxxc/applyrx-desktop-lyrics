"""Offline Apple Music cache-backed lyrics provider."""

from __future__ import annotations

import gzip
import io
import json
import math
import re
import sqlite3
import subprocess
import unicodedata
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Protocol, Sequence, Set, Tuple, Union
from xml.etree import ElementTree as ET


DEFAULT_CACHE_DB = Path.home() / "Library/Caches/com.apple.Music/Cache.db"
MAX_CACHE_BODY_BYTES = 32 * 1024 * 1024
MAX_TTML_BYTES = 4 * 1024 * 1024
DURATION_TOLERANCE_SECONDS = 2.0
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
METADATA_TRANSLATION = str.maketrans({
    "親": "亲", "愛": "爱", "這": "这", "裡": "里", "裏": "里", "麼": "么",
    "為": "为", "會": "会", "聽": "听", "說": "说", "過": "过", "還": "还",
    "讓": "让", "總": "总", "個": "个", "夢": "梦", "見": "见", "倆": "俩",
    "問": "问", "現": "现", "給": "给", "機": "机", "開": "开", "聲": "声",
    "輕": "轻", "轉": "转", "離": "离", "歡": "欢", "關": "关", "緊": "紧",
    "處": "处", "間": "间", "嗎": "吗", "時": "时", "氣": "气", "東": "东",
    "萬": "万", "與": "与", "妳": "你", "夠": "够", "動": "动", "記": "记",
})


@dataclass(frozen=True)
class LyricWord:
    startTime: float
    endTime: float
    text: str


@dataclass(frozen=True)
class LyricLine:
    startTime: float
    endTime: float
    text: str
    words: Tuple[LyricWord, ...] = ()


@dataclass(frozen=True)
class Lyrics:
    lines: Tuple[LyricLine, ...]
    language: Optional[str]
    hasWordTiming: bool
    catalogId: Optional[str] = None


@dataclass(frozen=True)
class CurrentTrack:
    title: str
    artist: str
    album: str
    duration: float
    catalogId: Optional[str] = None
    playbackPosition: float = 0.0
    playbackState: Optional[str] = None


TrackMetadata = CurrentTrack


@dataclass(frozen=True)
class NotFound:
    message: str


@dataclass(frozen=True)
class Ambiguous:
    message: str


@dataclass(frozen=True)
class InvalidMatch:
    message: str


ProviderResult = Union[Lyrics, NotFound, Ambiguous, InvalidMatch]


class LyricsProvider(Protocol):
    def get_lyrics(self, track: CurrentTrack) -> ProviderResult:
        ...


class ProviderError(Exception):
    """Safe, user-facing provider failure."""


class CacheUnavailable(ProviderError):
    pass


class CacheReadError(ProviderError):
    pass


class TTMLParseError(ValueError):
    pass


@dataclass(frozen=True)
class CacheResponse:
    endpoint: str
    storefront: str
    parameterNames: Tuple[str, ...]
    requestedSongIds: frozenset
    payload: Any


@dataclass(frozen=True)
class CachedLyrics:
    language: Optional[str]
    catalogId: Optional[str]
    ttml: str


@dataclass(frozen=True)
class CatalogSong:
    catalogId: str
    title: Optional[str]
    artist: Optional[str]
    album: Optional[str]
    durationMilliseconds: Optional[float]
    lyrics: Tuple[CachedLyrics, ...]


class CacheScanner:
    """Read catalog-song responses and fsCachedData blobs without writing."""

    _LYRICS_EXTENSION = "extend[syllable-lyrics]"
    _SENSITIVE_PARAMETER = re.compile(
        r"(?:auth|token|cookie|signature|sig|credential|password|secret|dsid|key)",
        re.IGNORECASE,
    )

    def __init__(self, cache_db: Path = DEFAULT_CACHE_DB):
        self.cache_db = Path(cache_db)

    def scan(self) -> List[CacheResponse]:
        if not self.cache_db.is_file():
            raise CacheUnavailable("Apple Music cache database is unavailable.")

        uri = "file:" + urllib.parse.quote(str(self.cache_db.resolve()), safe="/") + "?mode=ro"
        try:
            connection = sqlite3.connect(uri, uri=True, timeout=5)
            connection.execute("PRAGMA query_only = ON")
        except sqlite3.Error as exc:
            raise CacheUnavailable("Apple Music cache database could not be opened read-only.") from exc

        try:
            tables = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            required = {
                "cfurl_cache_response",
                "cfurl_cache_receiver_data",
            }
            if not required.issubset(tables):
                raise CacheReadError("Apple Music cache schema is not supported.")
            rows = connection.execute(
                """
                SELECT r.request_key, d.isDataOnFS, d.receiver_data
                FROM cfurl_cache_response AS r
                JOIN cfurl_cache_receiver_data AS d USING (entry_ID)
                """
            ).fetchall()
            responses = []
            fs_cache_dir = self.cache_db.parent / "fsCachedData"
            for request_key, is_data_on_fs, receiver_data in rows:
                endpoint_info = self._parse_endpoint(request_key)
                if endpoint_info is None:
                    continue
                body = self._read_body(is_data_on_fs, receiver_data, fs_cache_dir)
                if body is None:
                    continue
                payload = self._parse_json(body)
                if payload is None:
                    continue
                responses.append(CacheResponse(payload=payload, **endpoint_info))
            return responses
        except sqlite3.Error as exc:
            raise CacheReadError("Apple Music cache could not be read.") from exc
        finally:
            connection.close()

    @classmethod
    def _parse_endpoint(cls, request_key: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(request_key, str):
            return None
        try:
            parsed = urllib.parse.urlsplit(request_key)
            query_items = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        except ValueError:
            return None
        match = re.fullmatch(r"/v1/catalog/([a-z]{2,3})/songs", parsed.path)
        if parsed.hostname != "amp-api.music.apple.com" or not match:
            return None

        allowed_parameters = [
            key for key, _ in query_items
            if re.fullmatch(r"[A-Za-z0-9_.\[\]-]{1,100}", key)
            and not cls._SENSITIVE_PARAMETER.search(key)
        ]
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        extension_values = query.get("extend", [])
        requests_lyrics = (
            cls._LYRICS_EXTENSION in query
            or any(
                cls._LYRICS_EXTENSION in value.split(",")
                for value in extension_values
            )
        )
        if not requests_lyrics:
            return None

        ids: Set[str] = set()
        for key in ("ids[songs]", "id", "ids"):
            for value in query.get(key, []):
                ids.update(re.findall(r"(?<!\d)\d{6,}(?!\d)", value))
        return {
            "endpoint": "/v1/catalog/{}/songs".format(match.group(1)),
            "storefront": match.group(1),
            "parameterNames": tuple(sorted(set(allowed_parameters))),
            "requestedSongIds": frozenset(ids),
        }

    @staticmethod
    def _read_body(is_data_on_fs: Any, receiver_data: Any, fs_cache_dir: Path) -> Optional[bytes]:
        if receiver_data is None:
            return None
        if is_data_on_fs:
            if isinstance(receiver_data, bytes):
                try:
                    filename = receiver_data.decode("ascii")
                except UnicodeDecodeError:
                    return None
            elif isinstance(receiver_data, str):
                filename = receiver_data
            else:
                return None
            if not filename or Path(filename).name != filename or filename in (".", ".."):
                return None
            try:
                root = fs_cache_dir.resolve()
                target = fs_cache_dir / filename
                if target.is_symlink() or target.resolve().parent != root or not target.is_file():
                    return None
                if target.stat().st_size > MAX_CACHE_BODY_BYTES:
                    return None
                return target.read_bytes()
            except OSError:
                return None

        if isinstance(receiver_data, str):
            body = receiver_data.encode("utf-8", "replace")
        elif isinstance(receiver_data, bytes):
            body = receiver_data
        else:
            return None
        return body if len(body) <= MAX_CACHE_BODY_BYTES else None

    @staticmethod
    def _parse_json(body: bytes) -> Optional[Any]:
        if body.startswith(b"\x1f\x8b"):
            try:
                with gzip.GzipFile(fileobj=io.BytesIO(body)) as stream:
                    body = stream.read(MAX_CACHE_BODY_BYTES + 1)
                if len(body) > MAX_CACHE_BODY_BYTES:
                    return None
            except (OSError, EOFError):
                return None
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None


class CatalogResponseParser:
    """Extract song identity and its related syllable-lyrics resource."""

    def parse(self, response: CacheResponse) -> List[CatalogSong]:
        songs = []
        for resource in self._song_resources(response.payload):
            attributes = resource.get("attributes", {})
            if not isinstance(attributes, dict):
                continue
            catalog_id = resource.get("id")
            if not isinstance(catalog_id, (str, int)) or not re.fullmatch(
                r"\d{6,}", str(catalog_id)
            ):
                continue
            duration = attributes.get("durationInMillis")
            try:
                duration_ms = float(duration) if duration is not None else None
            except (TypeError, ValueError):
                duration_ms = None
            songs.append(CatalogSong(
                catalogId=str(catalog_id),
                title=self._optional_string(attributes.get("name")),
                artist=self._optional_string(attributes.get("artistName")),
                album=self._optional_string(attributes.get("albumName")),
                durationMilliseconds=duration_ms,
                lyrics=tuple(self._related_lyrics(resource)),
            ))
        return songs

    @staticmethod
    def _optional_string(value: Any) -> Optional[str]:
        return value if isinstance(value, str) and value.strip() else None

    @staticmethod
    def _song_resources(value: Any) -> Iterable[Dict[str, Any]]:
        if isinstance(value, dict):
            if value.get("type") == "songs":
                yield value
            for child in value.values():
                yield from CatalogResponseParser._song_resources(child)
        elif isinstance(value, list):
            for child in value:
                yield from CatalogResponseParser._song_resources(child)

    def _related_lyrics(self, resource: Dict[str, Any]) -> Iterable[CachedLyrics]:
        relationships = resource.get("relationships")
        if not isinstance(relationships, dict):
            return
        relationship = relationships.get("syllable-lyrics")
        if not isinstance(relationship, dict):
            return
        lyric_resources = relationship.get("data")
        if isinstance(lyric_resources, dict):
            lyric_resources = [lyric_resources]
        if not isinstance(lyric_resources, list):
            return
        for lyric_resource in lyric_resources:
            if not isinstance(lyric_resource, dict):
                continue
            lyric_attributes = lyric_resource.get("attributes")
            if not isinstance(lyric_attributes, dict):
                continue
            localizations = lyric_attributes.get("ttmlLocalizations")
            for language, ttml in self._localization_documents(localizations):
                lyric_attributes = lyric_resource.get("attributes", {})
                play_params = lyric_attributes.get("playParams", {})
                catalog_id = play_params.get("catalogId") if isinstance(play_params, dict) else None
                yield CachedLyrics(
                    language=language,
                    catalogId=str(catalog_id) if catalog_id is not None else None,
                    ttml=ttml,
                )

    @staticmethod
    def _localization_documents(value: Any, language_hint: Optional[str] = None):
        if isinstance(value, str):
            if value.strip():
                yield language_hint, value
        elif isinstance(value, dict):
            for key, child in value.items():
                hint = key if isinstance(key, str) and len(key) <= 35 else language_hint
                yield from CatalogResponseParser._localization_documents(child, hint)
        elif isinstance(value, list):
            for child in value:
                yield from CatalogResponseParser._localization_documents(child, language_hint)


class TTMLParser:
    """Parse timed paragraphs and explicit inline word timings."""

    def parse(self, ttml: str, language_hint: Optional[str] = None) -> Lyrics:
        if not isinstance(ttml, str) or not ttml.strip():
            raise TTMLParseError("TTML document is empty.")
        if len(ttml.encode("utf-8")) > MAX_TTML_BYTES:
            raise TTMLParseError("TTML document exceeds the supported size.")
        if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", ttml, re.IGNORECASE):
            raise TTMLParseError("TTML document contains a prohibited declaration.")
        try:
            root = ET.fromstring(ttml)
        except ET.ParseError as exc:
            raise TTMLParseError("TTML document is malformed.") from exc

        lines = []
        line_languages = set()

        def visit(
            element: ET.Element,
            parent_start: float,
            parent_end: Optional[float],
            start_is_defined: bool,
            end_is_defined: bool,
            inherited_language: Optional[str],
        ) -> None:
            language = element.attrib.get(XML_LANG, inherited_language)
            start, end = self._element_timing(element, parent_start, parent_end)
            has_start = start_is_defined or "begin" in element.attrib
            has_end = (
                end_is_defined
                or "end" in element.attrib
                or "dur" in element.attrib
            )
            if self._local_name(element.tag) == "p":
                text = "".join(element.itertext()).strip()
                if (has_start and has_end and start is not None and end is not None
                        and end > start and text):
                    words = self._timed_words(element, start, end)
                    lines.append(LyricLine(
                        startTime=start,
                        endTime=end,
                        text=text,
                        words=tuple(words),
                    ))
                    if language:
                        line_languages.add(language)
                return

            child_start = start if start is not None else parent_start
            for child in element:
                visit(child, child_start, end, has_start, has_end, language)

        visit(root, 0.0, None, False, False, language_hint)

        lines.sort(key=lambda line: (line.startTime, line.endTime))
        if not lines:
            raise TTMLParseError("TTML contains no timed lyric lines.")
        language = root.attrib.get(XML_LANG, language_hint)
        if not language and len(line_languages) == 1:
            language = next(iter(line_languages))
        return Lyrics(
            lines=tuple(lines),
            language=language,
            hasWordTiming=any(line.words for line in lines),
        )

    def _timed_words(
        self,
        paragraph: ET.Element,
        line_start: float,
        line_end: float,
    ) -> List[LyricWord]:
        words = []

        def visit(element: ET.Element, parent_start: float, parent_end: Optional[float]) -> None:
            start, end = self._element_timing(element, parent_start, parent_end)
            timed = any(name in element.attrib for name in ("begin", "end", "dur"))
            children = list(element)
            if (self._local_name(element.tag) == "span" and timed and not children
                    and start is not None and end is not None and end > start):
                text = "".join(element.itertext()).strip()
                if text and start >= line_start - 0.001 and end <= line_end + 0.001:
                    words.append(LyricWord(startTime=start, endTime=end, text=text))
                return
            child_start = start if start is not None else parent_start
            for child in children:
                visit(child, child_start, end)

        for child in paragraph:
            visit(child, line_start, line_end)
        words.sort(key=lambda word: (word.startTime, word.endTime))
        return words

    def _element_timing(
        self,
        element: ET.Element,
        parent_start: float,
        parent_end: Optional[float],
    ) -> Tuple[Optional[float], Optional[float]]:
        begin_value = element.attrib.get("begin")
        end_value = element.attrib.get("end")
        duration_value = element.attrib.get("dur")
        start = parent_start
        if begin_value is not None:
            begin = self._parse_time(begin_value)
            if begin is None:
                return None, None
            start += begin
        end = None
        if end_value is not None:
            parsed_end = self._parse_time(end_value)
            if parsed_end is None:
                return None, None
            end = parent_start + parsed_end
        elif duration_value is not None:
            duration = self._parse_time(duration_value)
            if duration is None or duration <= 0:
                return None, None
            end = start + duration
        elif parent_end is not None:
            end = parent_end
        return start, end

    @staticmethod
    def _parse_time(value: str) -> Optional[float]:
        value = value.strip()
        try:
            if value.endswith("ms"):
                seconds = float(value[:-2]) / 1000
            elif value.endswith("h"):
                seconds = float(value[:-1]) * 3600
            elif value.endswith("m"):
                seconds = float(value[:-1]) * 60
            elif value.endswith("s"):
                seconds = float(value[:-1])
            elif ":" in value:
                parts = value.split(":")
                if len(parts) == 3:
                    hours, minutes, seconds_part = parts
                    seconds = float(hours) * 3600 + float(minutes) * 60 + float(seconds_part)
                elif len(parts) == 2:
                    minutes, seconds_part = parts
                    seconds = float(minutes) * 60 + float(seconds_part)
                else:
                    return None
            else:
                return None
        except ValueError:
            return None
        return seconds if math.isfinite(seconds) and seconds >= 0 else None

    @staticmethod
    def _local_name(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

class TrackMatcher:
    """Require one catalog identity and exact metadata with bounded duration drift."""

    def match(
        self,
        track: CurrentTrack,
        responses: Sequence[CacheResponse],
        songs_by_response: Sequence[Sequence[CatalogSong]],
    ) -> Union[CatalogSong, NotFound, Ambiguous, InvalidMatch]:
        if len(responses) != len(songs_by_response):
            return InvalidMatch("Cached response parsing is incomplete.")
        if not self._valid_track(track):
            return InvalidMatch("Current track metadata is incomplete or invalid.")
        candidates: Dict[str, CatalogSong] = {}
        identity_conflicts: Set[str] = set()
        relevant_conflicts: Set[str] = set()
        off_id_metadata_matches: Set[str] = set()
        candidate_metadata: Dict[str, CatalogSong] = {}

        for response, songs in zip(responses, songs_by_response):
            for song in songs:
                requested_ids = response.requestedSongIds
                metadata_matches = self._metadata_matches(track, song)
                if track.catalogId and song.catalogId != track.catalogId:
                    if metadata_matches:
                        off_id_metadata_matches.add(song.catalogId)
                    if track.catalogId in requested_ids:
                        identity_conflicts.add(track.catalogId)
                    continue
                requested_id_mismatch = bool(
                    requested_ids
                    and song.catalogId not in requested_ids
                    and (metadata_matches or track.catalogId == song.catalogId)
                )
                if requested_id_mismatch:
                    identity_conflicts.add(song.catalogId)
                    if metadata_matches or track.catalogId == song.catalogId:
                        relevant_conflicts.add(song.catalogId)
                    continue
                if requested_ids and song.catalogId not in requested_ids:
                    continue
                previous_metadata = candidate_metadata.get(song.catalogId)
                if previous_metadata is not None and self._metadata_conflicts(
                    previous_metadata, song
                ):
                    identity_conflicts.add(song.catalogId)
                else:
                    candidate_metadata[song.catalogId] = self._merge_metadata(
                        previous_metadata, song
                    )

                if track.catalogId and song.catalogId == track.catalogId:
                    if not metadata_matches:
                        identity_conflicts.add(song.catalogId)
                    else:
                        candidates[song.catalogId] = self._merge_candidate(
                            candidates.get(song.catalogId), song
                        )
                    continue
                if not track.catalogId and metadata_matches:
                    candidates[song.catalogId] = self._merge_candidate(
                        candidates.get(song.catalogId), song
                    )

        if track.catalogId:
            if track.catalogId in identity_conflicts or track.catalogId in relevant_conflicts:
                return InvalidMatch("Cached metadata conflicts for the requested catalog ID.")
            if track.catalogId in candidates:
                return candidates[track.catalogId]
            if candidates:
                return InvalidMatch("Cached catalog ID conflicts with the current track.")
            if off_id_metadata_matches:
                return InvalidMatch(
                    "Cached metadata matches a different catalog ID than the requested one."
                )
            return NotFound("No cached song resource matches the requested catalog ID.")

        if identity_conflicts & set(candidates):
            return InvalidMatch("Cached metadata conflicts for a matching catalog ID.")
        if relevant_conflicts:
            return InvalidMatch("Cached request IDs conflict with the current track.")
        for catalog_id in identity_conflicts:
            candidates.pop(catalog_id, None)
        if len(candidates) > 1:
            return Ambiguous("Multiple cached catalog IDs match the current track.")
        if len(candidates) == 1:
            return next(iter(candidates.values()))

        return NotFound("No cached song resource matches the current track.")

    @staticmethod
    def _valid_track(track: CurrentTrack) -> bool:
        if not all(normalize_text(value) for value in (track.title, track.artist, track.album)):
            return False
        try:
            duration = float(track.duration)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(duration) or duration <= 0:
            return False
        return track.catalogId is None or (
            isinstance(track.catalogId, str)
            and bool(re.fullmatch(r"\d{6,}", track.catalogId))
        )

    @staticmethod
    def _metadata_matches(track: CurrentTrack, song: CatalogSong) -> bool:
        if not all((song.title, song.artist, song.album, song.durationMilliseconds)):
            return False
        if normalize_text(track.title) != normalize_text(song.title):
            return False
        if normalize_text(track.artist) != normalize_text(song.artist):
            return False
        if normalize_text(track.album) != normalize_text(song.album):
            return False
        try:
            track_duration = float(track.duration)
            song_duration = float(song.durationMilliseconds) / 1000
        except (TypeError, ValueError):
            return False
        return abs(track_duration - song_duration) <= DURATION_TOLERANCE_SECONDS

    @staticmethod
    def _metadata_conflicts(first: CatalogSong, second: CatalogSong) -> bool:
        for field in ("title", "artist", "album"):
            first_value = normalize_text(getattr(first, field))
            second_value = normalize_text(getattr(second, field))
            if first_value and second_value and first_value != second_value:
                return True
        first_duration = first.durationMilliseconds
        second_duration = second.durationMilliseconds
        return bool(
            first_duration
            and second_duration
            and abs(first_duration - second_duration) > DURATION_TOLERANCE_SECONDS * 1000
        )

    @staticmethod
    def _merge_metadata(
        previous: Optional[CatalogSong],
        current: CatalogSong,
    ) -> CatalogSong:
        if previous is None:
            return current
        return CatalogSong(
            catalogId=current.catalogId,
            title=current.title or previous.title,
            artist=current.artist or previous.artist,
            album=current.album or previous.album,
            durationMilliseconds=(
                current.durationMilliseconds or previous.durationMilliseconds
            ),
            lyrics=previous.lyrics or current.lyrics,
        )

    @staticmethod
    def _merge_candidate(
        previous: Optional[CatalogSong],
        current: CatalogSong,
    ) -> CatalogSong:
        if previous is None:
            return current
        return CatalogSong(
            catalogId=current.catalogId,
            title=current.title,
            artist=current.artist,
            album=current.album,
            durationMilliseconds=current.durationMilliseconds,
            lyrics=tuple(dict.fromkeys(previous.lyrics + current.lyrics)),
        )


def normalize_text(value: Optional[str]) -> str:
    if not isinstance(value, str):
        return ""
    simplified = value.translate(METADATA_TRANSLATION)
    simplified = unicodedata.normalize("NFKC", simplified).casefold().strip()
    return re.sub(r"[\s\-_·・'’\"“”()\[\]（）。,.，:：!！?？]+", "", simplified)


class AppleMusicCacheProvider:
    def __init__(
        self,
        scanner: Optional[CacheScanner] = None,
        catalog_parser: Optional[CatalogResponseParser] = None,
        ttml_parser: Optional[TTMLParser] = None,
        matcher: Optional[TrackMatcher] = None,
    ):
        self.scanner = scanner or CacheScanner()
        self.catalog_parser = catalog_parser or CatalogResponseParser()
        self.ttml_parser = ttml_parser or TTMLParser()
        self.matcher = matcher or TrackMatcher()

    def get_lyrics(self, track: CurrentTrack) -> ProviderResult:
        try:
            responses = self.scanner.scan()
        except ProviderError as exc:
            return NotFound(str(exc))
        parsed_songs = [self.catalog_parser.parse(response) for response in responses]
        matched = self.matcher.match(track, responses, parsed_songs)
        if isinstance(matched, (NotFound, Ambiguous, InvalidMatch)):
            return matched
        if not matched.lyrics:
            return NotFound("The matched catalog song has no cached syllable lyrics.")

        parsed_variants = []
        for cached_lyrics in matched.lyrics:
            if cached_lyrics.catalogId != matched.catalogId:
                return InvalidMatch(
                    "Syllable-lyrics catalog ID is missing or conflicts with its parent song."
                )
            try:
                parsed = self.ttml_parser.parse(cached_lyrics.ttml, cached_lyrics.language)
            except TTMLParseError:
                continue
            parsed_variants.append(parsed)
        unique_variants = {
            (lyrics.language, lyrics.lines): lyrics for lyrics in parsed_variants
        }
        if not unique_variants:
            return NotFound("The matched catalog song has no parseable timed TTML lyrics.")
        if len(unique_variants) > 1:
            return Ambiguous("Multiple distinct TTML lyric variants are cached for this song.")
        selected = next(iter(unique_variants.values()))
        return Lyrics(
            lines=selected.lines,
            language=selected.language,
            hasWordTiming=selected.hasWordTiming,
            catalogId=matched.catalogId,
        )

    @staticmethod
    def read_current_track() -> CurrentTrack:
        script = '''
if application "Music" is not running then return "NOT_RUNNING"
tell application "Music"
  if not (exists current track) then return "NO_TRACK"
  set track_name to my safe_text(name of current track)
  set track_artist to my safe_text(artist of current track)
  set track_album to my safe_text(album of current track)
  set track_duration to my safe_text(duration of current track)
  set track_position to my safe_text(player position)
  set track_state to my safe_text(player state)
  return "OK" & (character id 31) & track_name & (character id 31) & track_artist & (character id 31) & track_album & (character id 31) & track_duration & (character id 31) & track_position & (character id 31) & track_state
end tell
on safe_text(value_to_convert)
  try
    return value_to_convert as text
  on error
    return ""
  end try
end safe_text
'''
        try:
            result = subprocess.run(
                ["osascript"],
                input=script,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CacheReadError("Music.app metadata could not be read.") from exc
        if result.returncode:
            raise CacheReadError("Music.app metadata could not be read.")
        output = result.stdout.rstrip("\r\n")
        if output == "NOT_RUNNING":
            raise CacheReadError("Music.app is not running.")
        if output == "NO_TRACK":
            raise CacheReadError("Music.app has no current track.")
        fields = output.split("\x1f", 6)
        if len(fields) != 7 or fields[0] != "OK":
            raise CacheReadError("Music.app returned incomplete track metadata.")
        try:
            duration = float(fields[4])
            position = float(fields[5])
        except ValueError as exc:
            raise CacheReadError("Music.app returned invalid track timing.") from exc
        if not all(field.strip() for field in fields[1:4]) or duration <= 0:
            raise CacheReadError("Music.app returned incomplete track metadata.")
        return CurrentTrack(
            title=fields[1],
            artist=fields[2],
            album=fields[3],
            duration=duration,
            playbackPosition=position,
            playbackState=fields[6],
        )
