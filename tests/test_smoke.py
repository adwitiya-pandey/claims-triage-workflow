"""Smoke test importing triage package."""

import triage


def test_triage_package_imports():
    """Verify package imports successfully."""
    assert triage is not None
