"""Prompt injection and hostile character detection per SPEC 11."""

import re
import unicodedata

INJECTION = [
    r"\b(ignore|disregard|forget|override)\b[^.\n]{0,40}\b(instructions?|rules?|guidelines?|polic(?:y|ies)|prompt)\b",
    r"\bsystem\s+prompt\b",
    r"\bnew instructions?\b",
    r"\byou are now\b",
    r"\bact as\b",
    r"\bdo not (escalate|flag|review)\b",
    r"\bdon[’']?t (escalate|flag|review)\b",
    r"\b(mark|set|route|classify|label|treat)\b[^.\n]{0,30}\bfast[-_ ]?track\w*\b",
    r"</?\s*(system|assistant|instructions?)\s*>",
    r"\[/?INST\]",
    r"\"(tool|function|name)\"\s*:\s*\"[a-z_]+\"",
    r"\bas an ai\b",
    r"\bjailbreak\b",
]

INJ_RE = [re.compile(p, re.IGNORECASE) for p in INJECTION]


def hidden_chars(t: str) -> bool:
    """Return True if text contains hidden control or format characters."""
    return any(
        unicodedata.category(c) == "Cf" or (unicodedata.category(c) == "Cc" and c not in "\n\t\r")
        for c in t
    )


def injection_flags(t: str) -> list[str]:
    """Scan original text for prompt injection patterns and hidden characters."""
    flags: list[str] = []
    if any(r.search(t) for r in INJ_RE):
        flags.append("PATTERN")
    if hidden_chars(t):
        flags.append("HIDDEN_CHARS")
    return flags
