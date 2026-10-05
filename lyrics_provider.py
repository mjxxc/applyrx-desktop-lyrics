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
            parent_has_absolute_children: bool,
            start_is_defined: bool,
            end_is_defined: bool,
            inherited_language: Optional[str],
        ) -> None:
            language = element.attrib.get(XML_LANG, inherited_language)
            start, end = self._element_timing(
                element,
                parent_start,
                parent_end,
                parent_has_absolute_children,
            )
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
            children_are_absolute = self._children_use_absolute_timing(
                element,
                child_start,
                end,
            )
            for child in element:
                visit(
                    child,
                    child_start,
                    end,
                    children_are_absolute,
                    has_start,
                    has_end,
                    language,
                )

        bodies = [
            element for element in root.iter()
            if self._local_name(element.tag) == "body"
        ]
        for body in bodies:
            visit(body, 0.0, None, False, False, False, language_hint)

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

        def visit(
            element: ET.Element,
            parent_start: float,
            parent_end: Optional[float],
            parent_has_absolute_children: bool,
        ) -> None:
            start, end = self._element_timing(
                element,
                parent_start,
                parent_end,
                parent_has_absolute_children,
            )
            timed = any(name in element.attrib for name in ("begin", "end", "dur"))
            children = list(element)
            if self._local_name(element.tag) == "span":
                text = "".join(element.itertext()).strip()
                timed_descendant = any(
                    self._local_name(descendant.tag) == "span"
                    and any(name in descendant.attrib for name in ("begin", "end", "dur"))
                    for child in children
                    for descendant in child.iter()
                )
                has_independent_timing = (
                    timed
                    and start is not None
                    and end is not None
                    and end > start
                    and start >= line_start - 0.001
                    and end <= line_end + 0.001
                )
                if text and not timed_descendant:
                    word_start, word_end = (
                        (start, end)
                        if has_independent_timing
                        else (parent_start, parent_end)
                    )
                    if (word_start is not None and word_end is not None
                            and line_start - 0.001 <= word_start
                            and word_end <= line_end + 0.001
                            and word_end > word_start):
                        words.append(LyricWord(
                            startTime=word_start,
                            endTime=word_end,
                            text=text,
                        ))
                    return
                if not children:
                    return
            child_start = start if start is not None else parent_start
            children_are_absolute = self._children_use_absolute_timing(
                element,
                child_start,
                end,
            )
            for child in children:
                visit(child, child_start, end, children_are_absolute)

        paragraph_children_are_absolute = self._children_use_absolute_timing(
            paragraph,
            line_start,
            line_end,
        )
        for child in paragraph:
            visit(child, line_start, line_end, paragraph_children_are_absolute)
        words.sort(key=lambda word: (word.startTime, word.endTime))
        return words

    def _element_timing(
        self,
        element: ET.Element,
        parent_start: float,
        parent_end: Optional[float],
        parent_has_absolute_children: bool = False,
    ) -> Tuple[Optional[float], Optional[float]]:
        begin_value = element.attrib.get("begin")
        end_value = element.attrib.get("end")
        duration_value = element.attrib.get("dur")
        begin = self._parse_time(begin_value) if begin_value is not None else None
        parsed_end = self._parse_time(end_value) if end_value is not None else None
        if begin_value is not None and begin is None:
            return None, None
        if end_value is not None and parsed_end is None:
            return None, None

        absolute = (
            parent_has_absolute_children
            and self._fits_parent_interval(begin, parsed_end, parent_start, parent_end)
        )
        start = (
            begin if absolute and begin is not None
            else parent_start + begin if begin is not None
            else parent_start
        )
        end = None
        if parsed_end is not None:
            end = (
                parsed_end if absolute
                else parent_start + parsed_end
            )
        elif duration_value is not None:
            duration = self._parse_time(duration_value)
            if duration is None or duration <= 0:
                return None, None
            end = start + duration
        elif parent_end is not None:
            end = parent_end
        return start, end

    @classmethod
    def _children_use_absolute_timing(
        cls,
        parent: ET.Element,
        parent_start: float,
        parent_end: Optional[float],
    ) -> bool:
        if parent_end is None:
            return False
        timed_children = [
            child for child in parent
            if any(name in child.attrib for name in ("begin", "end", "dur"))
        ]
        if not timed_children:
            return False

        fits = True
        aligns_with_boundary = False
        for child in timed_children:
            raw_begin = cls._parse_time(child.attrib["begin"]) if "begin" in child.attrib else None
            raw_end = cls._parse_time(child.attrib["end"]) if "end" in child.attrib else None
            raw_duration = cls._parse_time(child.attrib["dur"]) if "dur" in child.attrib else None
            if (("begin" in child.attrib and raw_begin is None)
                    or ("end" in child.attrib and raw_end is None)
                    or ("dur" in child.attrib and raw_duration is None)):
                return False
            child_start = raw_begin if raw_begin is not None else parent_start
            child_end = (
                raw_end if raw_end is not None
                else child_start + raw_duration if raw_duration is not None
                else parent_end
            )
            if not cls._fits_parent_interval(
                child_start,
                child_end,
                parent_start,
                parent_end,
            ):
                fits = False
                break
            aligns_with_boundary = aligns_with_boundary or (
                raw_begin is not None
                and math.isclose(raw_begin, parent_start, abs_tol=0.001)
            ) or (
                raw_end is not None
                and math.isclose(raw_end, parent_end, abs_tol=0.001)
            )
        return fits and aligns_with_boundary

    @staticmethod
    def _fits_parent_interval(
        begin: Optional[float],
        end: Optional[float],
        parent_start: float,
        parent_end: Optional[float],
    ) -> bool:
        if parent_end is None:
            return False
        child_start = begin if begin is not None else parent_start
        child_end = end
        return (
            parent_start <= child_start <= parent_end
            and (child_end is None or child_start <= child_end <= parent_end)
        )

    @staticmethod
    def _parse_time(value: str) -> Optional[float]:
        value = value.strip()
        if not value:
            return None
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
                seconds = float(value)
        except ValueError:
            return None
        return seconds if math.isfinite(seconds) and seconds >= 0 else None

    @staticmethod
    def _local_name(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

class TrackMatcher:
    """Require one catalog identity and exact metadata with bounded duration drift."""

    def __init__(self) -> None:
        self.last_diagnostics = {
            "candidateCount": 0,
            "completeCandidateCount": 0,
            "matchedCandidateCount": 0,
            "incompleteCandidateCount": 0,
            "finalStatus": "notFound",
        }

    def match(
        self,
        track: CurrentTrack,
        responses: Sequence[CacheResponse],
        songs_by_response: Sequence[Sequence[CatalogSong]],
    ) -> Union[CatalogSong, NotFound, Ambiguous, InvalidMatch]:
        if len(responses) != len(songs_by_response):
            return self._finish(
                InvalidMatch("Cached response parsing is incomplete.")
            )
        if not self._valid_track(track):
            return self._finish(
                InvalidMatch("Current track metadata is incomplete or invalid.")
            )

        grouped: Dict[str, List[CatalogSong]] = {}
        request_conflict = False
        off_id_metadata_matches = False

        for response, songs in zip(responses, songs_by_response):
            for song in songs:
                requested_ids = response.requestedSongIds
                metadata_matches = self._metadata_matches(track, song)
                if track.catalogId and song.catalogId != track.catalogId:
                    if metadata_matches:
                        off_id_metadata_matches = True
                    if track.catalogId in requested_ids:
                        request_conflict = True
                    continue
                if requested_ids and song.catalogId not in requested_ids:
                    if metadata_matches or track.catalogId == song.catalogId:
                        request_conflict = True
                    continue
                grouped.setdefault(song.catalogId, []).append(song)

        if track.catalogId:
            relevant = grouped.get(track.catalogId, [])
        else:
            relevant = [song for group in grouped.values() for song in group]

        complete_count = sum(self._metadata_complete(song) for song in relevant)
        matched_by_id: Dict[str, List[CatalogSong]] = {}
        conflicting_ids: Set[str] = set()
        for catalog_id, group in grouped.items():
            complete = [song for song in group if self._metadata_complete(song)]
            if any(
                self._metadata_conflicts(first, second)
                for index, first in enumerate(complete)
                for second in complete[index + 1:]
            ):
                conflicting_ids.add(catalog_id)
            strict_matches = [
                song for song in complete
                if self._metadata_matches(track, song)
            ]
            if strict_matches:
                matched_by_id[catalog_id] = strict_matches

        matched_count = sum(
            len(group)
            for catalog_id, group in matched_by_id.items()
            if not track.catalogId or catalog_id == track.catalogId
        )
        self._set_diagnostics(
            candidate_count=len(relevant),
            complete_count=complete_count,
            matched_count=matched_count,
            incomplete_count=len(relevant) - complete_count,
        )

        if request_conflict:
            return self._finish(
                InvalidMatch("Cached request IDs conflict with the current track.")
            )
        if track.catalogId:
            if track.catalogId in conflicting_ids:
                return self._finish(
                    InvalidMatch("Complete cached metadata conflicts for the requested catalog ID.")
                )
            matches = matched_by_id.get(track.catalogId, [])
            if matches:
                return self._finish(self._select_best(matches))
            if any(self._metadata_complete(song) for song in relevant):
                return self._finish(
                    InvalidMatch("Cached metadata conflicts for the requested catalog ID.")
                )
            if off_id_metadata_matches:
                return self._finish(InvalidMatch(
                    "Cached metadata matches a different catalog ID than the requested one."
                ))
            return self._finish(
                NotFound("No complete cached song matches the requested catalog ID.")
            )

        matching_ids = set(matched_by_id)
        if conflicting_ids & matching_ids:
            return self._finish(
                InvalidMatch("Complete cached metadata conflicts for a matching catalog ID.")
            )
        if len(matching_ids) > 1:
            return self._finish(
                Ambiguous("Multiple cached catalog IDs match the current track.")
            )
        if matching_ids:
            catalog_id = next(iter(matching_ids))
            return self._finish(self._select_best(matched_by_id[catalog_id]))

        return self._finish(NotFound("No cached song resource matches the current track."))

    def _set_diagnostics(
        self,
        candidate_count: int,
        complete_count: int,
        matched_count: int,
        incomplete_count: int,
    ) -> None:
        self.last_diagnostics = {
            "candidateCount": candidate_count,
            "completeCandidateCount": complete_count,
            "matchedCandidateCount": matched_count,
            "incompleteCandidateCount": incomplete_count,
            "finalStatus": "notFound",
        }

    def _finish(self, result: Union[CatalogSong, NotFound, Ambiguous, InvalidMatch]):
        self.last_diagnostics["finalStatus"] = (
            "matched" if isinstance(result, CatalogSong)
            else "ambiguous" if isinstance(result, Ambiguous)
            else "invalid" if isinstance(result, InvalidMatch)
            else "notFound"
        )
        return result

    @classmethod
    def _metadata_complete(cls, song: CatalogSong) -> bool:
        return (
            bool(normalize_text(song.title))
            and bool(normalize_text(song.artist))
            and bool(normalize_text(song.album))
            and song.durationMilliseconds is not None
            and math.isfinite(song.durationMilliseconds)
            and song.durationMilliseconds > 0
        )

    @classmethod
    def _select_best(cls, candidates: Sequence[CatalogSong]) -> CatalogSong:
        best = max(candidates, key=cls._candidate_completeness)
        return cls._merge_candidate(best, *candidates)

    @staticmethod
    def _candidate_completeness(song: CatalogSong) -> int:
        return sum((
            bool(normalize_text(song.title)),
            bool(normalize_text(song.artist)),
            bool(normalize_text(song.album)),
            song.durationMilliseconds is not None
            and math.isfinite(song.durationMilliseconds)
            and song.durationMilliseconds > 0,
        ))

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
    def _merge_candidate(
        selected: CatalogSong,
        *others: CatalogSong,
    ) -> CatalogSong:
        return CatalogSong(
            catalogId=selected.catalogId,
            title=selected.title,
            artist=selected.artist,
            album=selected.album,
            durationMilliseconds=selected.durationMilliseconds,
            lyrics=tuple(dict.fromkeys(
                lyric for candidate in (selected,) + others for lyric in candidate.lyrics
            )),
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
