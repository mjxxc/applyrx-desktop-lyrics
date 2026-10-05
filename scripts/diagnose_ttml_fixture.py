#!/usr/bin/env python3
"""Print timing-only statistics for the redacted Apple Music TTML fixture."""

from __future__ import annotations

import sys
from pathlib import Path
from xml.etree import ElementTree as ET

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from lyrics_provider import TTMLParser


FIXTURE = PROJECT_DIR / "tests" / "fixtures" / "apple_music_syllable_timing.xml"


def main() -> int:
    document = FIXTURE.read_text(encoding="utf-8")
    root = ET.fromstring(document)
    local_name = lambda element: element.tag.rsplit("}", 1)[-1]
    paragraphs = [
        element for element in root.iter()
        if local_name(element) == "p"
    ]
    starts = [
        TTMLParser._parse_time(element.attrib["begin"])
        for element in paragraphs
        if "begin" in element.attrib
    ]
    if len(starts) != len(paragraphs) or not starts:
        raise SystemExit("Fixture must contain a start time for every paragraph.")

    lyrics = TTMLParser().parse(document)
    parsed_starts = [line.startTime for line in lyrics.lines]
    raw_gaps = [right - left for left, right in zip(starts, starts[1:])]
    parsed_gaps = [
        right - left for left, right in zip(parsed_starts, parsed_starts[1:])
    ]
    timed_spans = sum(
        local_name(element) == "span"
        and any(name in element.attrib for name in ("begin", "end", "dur"))
        for element in root.iter()
    )

    values = (
        ("rawPCount", len(paragraphs)),
        ("parsedLineCount", len(lyrics.lines)),
        ("firstRawStart", starts[0]),
        ("firstParsedStart", parsed_starts[0]),
        ("lastRawStart", starts[-1]),
        ("lastParsedStart", parsed_starts[-1]),
        ("maxGapRaw", max(raw_gaps, default=0.0)),
        ("maxGapParsed", max(parsed_gaps, default=0.0)),
        ("wordTimingSpanCount", timed_spans),
        ("wordCount", sum(len(line.words) for line in lyrics.lines)),
    )
    for name, value in values:
        rendered = "{:.3f}".format(value) if isinstance(value, float) else value
        print("{}={}".format(name, rendered))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
