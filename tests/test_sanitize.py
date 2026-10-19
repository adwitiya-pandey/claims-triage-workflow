"""Tests for claim text sanitization per SPEC 11."""

from triage.sanitize import sanitize_text


def test_sanitize_preserves_clean_text():
    clean = "Patient attended clinic on 2026-05-10 for regular checkup.\nLine 2 with tab:\tcontent."
    sanitized, modified = sanitize_text(clean)
    assert sanitized == clean
    assert not modified


def test_sanitize_empty_string():
    sanitized, modified = sanitize_text("")
    assert sanitized == ""
    assert not modified


def test_sanitize_strips_control_characters():
    # Null byte \x00, bell \x07, backspace \x08 (all category Cc)
    text_with_cc = "Patient\x00 record\x07 with\x08 bells."
    sanitized, modified = sanitize_text(text_with_cc)
    assert sanitized == "Patient record with bells."
    assert modified


def test_sanitize_preserves_newlines_tabs_and_carriage_returns():
    text = "Line 1\r\nLine 2\tTabbed\nLine 3"
    sanitized, modified = sanitize_text(text)
    assert sanitized == text
    assert not modified


def test_sanitize_strips_unicode_cf_category():
    # Zero-width space \u200B, zero-width non-joiner \u200C, soft hyphen \u00AD, right-to-left mark \u200F
    text_with_cf = "Doctor\u200b Note\u00ad with\u200c hidden\u200f characters"
    sanitized, modified = sanitize_text(text_with_cf)
    assert sanitized == "Doctor Note with hidden characters"
    assert modified


def test_sanitize_caps_at_20000_characters():
    long_text = "A" * 25000
    sanitized, modified = sanitize_text(long_text)
    assert len(sanitized) == 20000
    assert modified
    assert sanitized == "A" * 20000


def test_sanitize_caps_exactly_at_20000():
    exact_text = "B" * 20000
    sanitized, modified = sanitize_text(exact_text)
    assert len(sanitized) == 20000
    assert not modified
