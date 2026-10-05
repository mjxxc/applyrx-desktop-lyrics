#!/opt/homebrew/bin/python3
"""Fetch Apple Music TTML lyrics using Music.app's cached signed request."""

from __future__ import annotations

import argparse
import atexit
import shutil
import tempfile
import gzip
import json
import plistlib
import sqlite3
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree as ET


CACHE_DB_CANDIDATES = [
    Path.home() / "Library/Caches/com.apple.Music/Cache.db",
]

TEMP_DIRS: list[str] = []


def cleanup_temp_dirs() -> None:
    for temp_dir in TEMP_DIRS:
        shutil.rmtree(temp_dir, ignore_errors=True)


atexit.register(cleanup_temp_dirs)


def prepare_readable_cache_copy(cache_db: Path) -> Path:
    temp_dir = tempfile.mkdtemp(prefix="apple-music-cache-")
    TEMP_DIRS.append(temp_dir)
    temp_db = Path(temp_dir) / "Cache.db"
    # Do not copy metadata/xattrs: Music's cache can carry protected attrs that
    # make GUI-launched Python hit EPERM even when the bytes are readable.
    shutil.copyfile(cache_db, temp_db)

    for suffix in ("-wal", "-shm"):
        sidecar = cache_db.with_name(cache_db.name + suffix)
        if sidecar.exists():
            shutil.copyfile(sidecar, Path(temp_dir) / sidecar.name)
    return temp_db

KEEP_HEADERS = [
    "Accept",
    "Accept-Language",
    "Cookie",
    "User-Agent",
    "X-Apple-ActionSignature",
    "X-Apple-Cuid",
    "X-Apple-I-Client-Time",
    "X-Apple-I-Locale",
    "X-Apple-I-MD",
    "X-Apple-I-MD-LU",
    "X-Apple-I-MD-M",
    "X-Apple-I-MD-RINFO",
    "X-Apple-I-TimeZone",
    "X-Apple-Store-Front",
    "X-Apple-TID-State",
    "X-Apple-Tz",
    "X-Apple-Uuid",
    "X-Dsid",
    "X-Guid",
    "X-Token",
    "iCloud-DSID",
]


def get_recent_cached_requests(
    limit: int = 10,
    pattern: str = "%ttmlLyrics%",
) -> list[tuple[str, dict[str, str], str]]:
    """返回最近的缓存条目：(url, headers, time_stamp)，按时间降序。"""
    cache_db = next((path for path in CACHE_DB_CANDIDATES if path.exists()), None)
    if cache_db is None:
        checked = ", ".join(str(path) for path in CACHE_DB_CANDIDATES)
        raise SystemExit(f"Cache db not found. Checked: {checked}")

    try:
        readable_db = prepare_readable_cache_copy(cache_db)
    except OSError as exc:
        raise SystemExit(f"Could not copy cache db: {cache_db} ({exc})") from exc

    try:
        conn = sqlite3.connect(str(readable_db))
        conn.row_factory = sqlite3.Row
        sql = """
            select request_key, request_object, time_stamp
            from cfurl_cache_response
            join cfurl_cache_blob_data using(entry_ID)
            where request_key like ?
            order by time_stamp desc limit ?
        """
        rows = conn.execute(sql, (pattern, limit)).fetchall()
    finally:
        try:
            conn.close()
        except Exception:
            pass
        temp_parent = str(readable_db.parent)
        shutil.rmtree(temp_parent, ignore_errors=True)
        if temp_parent in TEMP_DIRS:
            TEMP_DIRS.remove(temp_parent)

    results: list[tuple[str, dict[str, str], str]] = []
    for row in rows:
        try:
            request = plistlib.loads(row["request_object"])
            headers = request["Array"][19]
            ts = str(row["time_stamp"] or "")
            results.append((row["request_key"], headers, ts))
        except Exception:
            continue
    return results


def get_latest_cached_request(song_id: str | None) -> tuple[str, dict[str, str]]:
    cache_db = next((path for path in CACHE_DB_CANDIDATES if path.exists()), None)
    if cache_db is None:
        checked = ", ".join(str(path) for path in CACHE_DB_CANDIDATES)
        raise SystemExit(f"Cache db not found. Checked: {checked}")

    try:
        readable_db = prepare_readable_cache_copy(cache_db)
    except OSError as exc:
        raise SystemExit(f"Could not copy cache db: {cache_db} ({exc})") from exc

    try:
        conn = sqlite3.connect(str(readable_db))
    except sqlite3.OperationalError as exc:
        raise SystemExit(f"Could not open cache db copy: {readable_db} ({exc})") from exc
    conn.row_factory = sqlite3.Row

    sql = """
        select request_key, request_object
        from cfurl_cache_response
        join cfurl_cache_blob_data using(entry_ID)
        where request_key like '%ttmlLyrics%'
    """
    params: list[str] = []
    if song_id:
        sql += " and request_key like ?"
        params.append(f"%id={song_id}%")
    sql += " order by time_stamp desc limit 1"

    try:
        row = conn.execute(sql, params).fetchone()
    finally:
        conn.close()
        # 立即清理临时文件，不等 atexit
        temp_parent = str(readable_db.parent)
        shutil.rmtree(temp_parent, ignore_errors=True)
        if temp_parent in TEMP_DIRS:
            TEMP_DIRS.remove(temp_parent)

    if row is None:
        hint = f" for song id {song_id}" if song_id else ""
        raise SystemExit(f"No cached ttmlLyrics request found{hint}. Open lyrics in Music.app first.")

    request = plistlib.loads(row["request_object"])
    headers = request["Array"][19]
    return row["request_key"], headers


def fetch(url: str, headers: dict[str, str]) -> str:
    cmd = ["curl", "-sS", "-L", "--max-time", "20", url, "-H", "Accept-Encoding: gzip"]
    for key in KEEP_HEADERS:
        value = headers.get(key)
        if value:
            cmd += ["-H", f"{key}: {value}"]

    result = subprocess.run(cmd, capture_output=True, check=False)
    if result.returncode != 0:
        raise SystemExit(result.stderr.decode("utf-8", "replace") or f"curl failed: {result.returncode}")

    data = result.stdout
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return data.decode("utf-8", "replace")


def parse_ttml_lines(ttml: str) -> list[dict[str, str]]:
    ns = {
        "ttml": "http://www.w3.org/ns/ttml",
        "itunes": "http://music.apple.com/lyric-ttml-internal",
    }
    root = ET.fromstring(ttml)
    lines: list[dict[str, str]] = []

    translations: dict[str, str] = {}
    for text in root.findall(".//itunes:translations/itunes:translation/itunes:text", ns):
        line_id = text.attrib.get("for")
        if line_id:
            translations[line_id] = "".join(text.itertext()).strip()

    for p in root.findall(".//ttml:body//ttml:p", ns):
        begin = p.attrib.get("begin", "")
        end = p.attrib.get("end", "")
        line_id = p.attrib.get("{http://music.apple.com/lyric-ttml-internal}key")
        content = "".join(p.itertext()).strip()
        if not content and line_id:
            content = translations.get(line_id, "")
        if content:
            lines.append({"begin": begin, "end": end, "text": content})
    return lines


def ttml_to_lines(ttml: str) -> list[str]:
    return [f"{line['begin']}\t{line['text']}" for line in parse_ttml_lines(ttml)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--song-id", help="Specific Apple song id to reuse from cache.")
    parser.add_argument("--json", action="store_true", help="Print raw JSON response.")
    parser.add_argument("--lines", action="store_true", help="Print time + lyric lines.")
    args = parser.parse_args()

    url, headers = get_latest_cached_request(args.song_id)
    body = fetch(url, headers)

    if args.json:
        print(body)
        return 0

    payload = json.loads(body)
    ttml = payload.get("ttml")
    if not ttml:
        raise SystemExit("Response did not contain TTML lyrics.")

    if args.lines:
        for line in ttml_to_lines(ttml):
            print(line)
        return 0

    print(ttml)
    return 0


if __name__ == "__main__":
    sys.exit(main())
