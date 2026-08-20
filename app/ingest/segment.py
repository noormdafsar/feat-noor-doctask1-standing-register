from __future__ import annotations

import re
from dataclasses import dataclass

MAX_CHUNK_CHARS = 900


@dataclass
class SegBlock:
    idx: int
    page: int
    char_start: int
    char_end: int
    text: str


def segment(text: str) -> list[SegBlock]:
    """Split into blocks while keeping exact offsets into the original string.

    Offsets are the whole point: a block that cannot say where it came from is
    not provenance, it is a paraphrase.
    """
    blocks: list[SegBlock] = []
    idx = 0
    page = 1
    for m in re.finditer(r"[^\n]+(?:\n(?!\n)[^\n]+)*", text):
        chunk_text = m.group(0)
        if not chunk_text.strip():
            continue
        # A synthetic page break marker keeps page numbers meaningful for
        # formats that have them and harmless for formats that do not.
        if chunk_text.strip().startswith("--- page"):
            page += 1
            continue
        blocks.append(
            SegBlock(
                idx=idx,
                page=page,
                char_start=m.start(),
                char_end=m.end(),
                text=chunk_text,
            )
        )
        idx += 1
    return blocks


def chunk_blocks(blocks: list[SegBlock]) -> list[tuple[list[int], str]]:
    """Group consecutive blocks into retrieval units, carrying their block indices."""
    out: list[tuple[list[int], str]] = []
    cur_ids: list[int] = []
    cur_text: list[str] = []
    size = 0
    for b in blocks:
        if size and size + len(b.text) > MAX_CHUNK_CHARS:
            out.append((cur_ids, "\n".join(cur_text)))
            cur_ids, cur_text, size = [], [], 0
        cur_ids.append(b.idx)
        cur_text.append(b.text)
        size += len(b.text)
    if cur_ids:
        out.append((cur_ids, "\n".join(cur_text)))
    return out


_WS = re.compile(r"\s+")


def normalise_for_match(s: str) -> str:
    return _WS.sub(" ", s).strip().lower()


def find_span(haystack: str, needle: str) -> tuple[int, int] | None:
    """Locate a quoted passage in the source, tolerant of whitespace only.

    Deliberately NOT fuzzy. A quote that does not actually appear is a fact the
    system will discard, and making this forgiving would quietly reintroduce the
    bluffing it exists to prevent.
    """
    if not needle.strip():
        return None
    direct = haystack.find(needle)
    if direct != -1:
        return direct, direct + len(needle)

    n_needle = normalise_for_match(needle)
    if not n_needle:
        return None

    # Whitespace-insensitive scan that still reports offsets into the original.
    positions: list[int] = []
    norm_chars: list[str] = []
    prev_space = False
    for i, ch in enumerate(haystack):
        if ch.isspace():
            if not prev_space and norm_chars:
                norm_chars.append(" ")
                positions.append(i)
            prev_space = True
            continue
        prev_space = False
        norm_chars.append(ch.lower())
        positions.append(i)
    norm = "".join(norm_chars)
    at = norm.find(n_needle)
    if at == -1:
        return None
    start = positions[at]
    end_idx = at + len(n_needle) - 1
    end = positions[end_idx] + 1
    return start, end
