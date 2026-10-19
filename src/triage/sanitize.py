"""Claim text sanitization per SPEC 11."""

import unicodedata


def sanitize_text(text: str) -> tuple[str, bool]:
    """Sanitize claim text.

    Strips control characters (Unicode category 'Cc') except newline, tab,
    and carriage return, strips Unicode format control characters (category 'Cf'),
    and caps length at 20,000 characters.

    Returns:
        tuple[str, bool]: (sanitized_text, was_modified)
    """
    clean_chars = []
    for c in text:
        cat = unicodedata.category(c)
        if cat == "Cf":
            continue
        if cat == "Cc" and c not in "\n\t\r":
            continue
        clean_chars.append(c)

    cleaned = "".join(clean_chars)
    capped = cleaned[:20000]

    was_modified = (len(text) != len(capped)) or (text != capped)
    return capped, was_modified
