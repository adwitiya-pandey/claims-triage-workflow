"""Tests for safe, explicit cassette recording options."""

import pytest

from scripts.record_cassettes import record_all_cassettes


def test_strict_recording_requires_key_before_creating_cassette(tmp_path, monkeypatch):
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    (cases_dir / "case.json").write_text("{}", encoding="utf-8")
    cassette_path = tmp_path / "cassette.jsonl"
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr("scripts.record_cassettes.load_dotenv", lambda: None)

    with pytest.raises(RuntimeError, match="GEMINI_API_KEY is required"):
        record_all_cassettes(
            cases_dir=cases_dir,
            cassette_path=cassette_path,
            require_live=True,
        )

    assert not cassette_path.exists()


def test_strict_recording_never_overwrites_existing_cassette(tmp_path, monkeypatch):
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    (cases_dir / "case.json").write_text("{}", encoding="utf-8")
    cassette_path = tmp_path / "cassette.jsonl"
    cassette_path.write_text('{"existing":true}\n', encoding="utf-8")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-a-real-credential")
    monkeypatch.setattr("scripts.record_cassettes.load_dotenv", lambda: None)

    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        record_all_cassettes(
            cases_dir=cases_dir,
            cassette_path=cassette_path,
            require_live=True,
        )

    assert cassette_path.read_text(encoding="utf-8") == '{"existing":true}\n'


def test_strict_recording_rejects_seeded_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-a-real-credential")
    monkeypatch.setattr("scripts.record_cassettes.load_dotenv", lambda: None)

    with pytest.raises(ValueError, match="cannot be combined"):
        record_all_cassettes(
            cases_dir=tmp_path,
            force_seed=True,
            require_live=True,
        )
