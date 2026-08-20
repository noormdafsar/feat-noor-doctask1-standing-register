from __future__ import annotations

import re
from dataclasses import dataclass

# Deliberate hard-coded defences layered alongside the model-side rule in
# app/llm/call.py::SOURCE_RULE. Neither is trusted alone: the preamble is what
# stops the model complying, the scanner is what guarantees the attempt is
# *reported* even if the model would have ignored it silently.
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("override", re.compile(r"\b(ignore|disregard|forget)\b[^.\n]{0,40}\b(previous|prior|above|all)\b[^.\n]{0,30}\b(instruction|prompt|rule|direction)", re.I)),
    ("verdict_steering", re.compile(r"\b(mark|treat|report|classify|record)\b[^.\n]{0,40}\b(as\s+)?(compliant|approved|passing|no\s+findings|clean)\b", re.I)),
    ("suppression", re.compile(r"\b(do\s*not|don't|never)\b[^.\n]{0,30}\b(report|flag|surface|mention|disclose|escalate)\b", re.I)),
    ("role_address", re.compile(r"\b(system|assistant|ai|agent|model|llm)\s*[:,]\s*(you|please|must|should)\b", re.I)),
    ("auto_approve", re.compile(r"\b(approve|auto[- ]approve|sign[- ]?off)\b[^.\n]{0,30}\b(without|no)\b[^.\n]{0,20}\b(review|human|approval)\b", re.I)),
]


@dataclass
class InjectionHit:
    pattern: str
    quote: str
    char_start: int
    char_end: int


def scan(text: str) -> list[InjectionHit]:
    hits: list[InjectionHit] = []
    seen: set[tuple[int, int]] = set()
    for name, rx in PATTERNS:
        for m in rx.finditer(text):
            start = max(0, m.start() - 40)
            end = min(len(text), m.end() + 40)
            if (m.start(), m.end()) in seen:
                continue
            seen.add((m.start(), m.end()))
            hits.append(
                InjectionHit(
                    pattern=name,
                    quote=text[start:end].strip(),
                    char_start=m.start(),
                    char_end=m.end(),
                )
            )
    return hits


def wrap_source(document_id: str, filename: str, body: str) -> str:
    """The only way source text is ever put in front of a model."""
    return (
        f'<source id="{document_id}" filename="{filename}">\n'
        f"{body}\n"
        f"</source>"
    )
