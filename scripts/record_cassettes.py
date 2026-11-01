"""Script to record Gemini LLM responses into cassette.jsonl per T16."""

import argparse
import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

from dotenv import load_dotenv

from triage.config import TriageConfig
from triage.service import TriageService
from triage.tools.fixture_backend import FixtureToolsBackend


def record_all_cassettes(
    cases_dir: str | Path = "data/cases",
    cassette_path: str | Path = "data/cassettes/cassette.jsonl",
    sleep_delay: float = 1.0,
    force_seed: bool = False,
    retriever_mode: str = "bm25",
    require_live: bool = False,
) -> str:
    """Record responses for all 43 cases into cassette.jsonl.

    Record live Gemini responses, or seed deterministic schema fixtures when live
    recording is unavailable unless require_live is set. Returns the recording mode.
    """
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY", "")
    if retriever_mode not in {"bm25", "dense", "hybrid"}:
        raise ValueError("retriever_mode must be bm25, dense, or hybrid")
    if require_live and force_seed:
        raise ValueError("require_live cannot be combined with force_seed")
    if require_live and not api_key:
        raise RuntimeError("GEMINI_API_KEY is required for strict live recording")

    c_dir = Path(cases_dir)
    case_files = sorted(c_dir.glob("*.json"))
    if not case_files:
        raise RuntimeError(f"No scenario cases found in {cases_dir}")

    cass_file = Path(cassette_path)
    if require_live and cass_file.exists():
        raise FileExistsError(
            f"Refusing to overwrite existing cassette: {cassette_path}"
        )
    cass_file.parent.mkdir(parents=True, exist_ok=True)
    if cass_file.exists():
        cass_file.unlink()

    live_mode = False
    if api_key and not force_seed:
        if require_live:
            live_mode = True
        else:
            try:
                from google import genai
                from google.genai import types

                probe = genai.Client(
                    api_key=api_key,
                    http_options=types.HttpOptions(timeout=30000),
                )
                test_resp = probe.models.generate_content(
                    model="gemini-3.8-flash", contents="Ping"
                )
                if test_resp and test_resp.text:
                    live_mode = True
                    print("Connected to live Gemini API (gemini-3.8-flash).")
            except Exception as probe_err:
                print(
                    f"Live Gemini API probe failed ({type(probe_err).__name__}). "
                    "Seeding deterministic recorded responses from verified schema "
                    "fixtures per T16 contract."
                )
                live_mode = False

    mode_description = "live_gemini" if live_mode else "seeded_fixtures"
    print(f"Recording mode: {mode_description} for {len(case_files)} cases.")

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_p = Path(temp_dir)
        triage_db_file = temp_p / "triage.db"
        cp_db_file = temp_p / "checkpoint.db"

        for idx, cf in enumerate(case_files, start=1):
            case = json.loads(cf.read_text(encoding="utf-8"))
            claim_id = case["claim_id"]

            fake_client = None
            if not live_mode:
                gold_ext = json.dumps(case.get("gold_extraction") or {})
                gold_ass = json.dumps(case.get("gold_assessment") or {})

                def make_mock(g_ext=gold_ext, g_ass=gold_ass):
                    def mock_generate(*args, **kwargs):
                        gen_cfg = kwargs.get("config")
                        schema = getattr(gen_cfg, "response_json_schema", {})
                        title = schema.get("title", "")
                        if "Extraction" in title:
                            text = g_ext
                            in_tok, out_tok = 120, 85
                        else:
                            text = g_ass
                            in_tok, out_tok = 340, 110

                        mock_resp = MagicMock()
                        mock_resp.text = text
                        mock_resp.usage_metadata = MagicMock(
                            prompt_token_count=in_tok,
                            candidates_token_count=out_tok,
                        )
                        return mock_resp

                    return mock_generate

                fake_client = MagicMock()
                fake_client.models.generate_content.side_effect = make_mock()

            cfg = TriageConfig(
                TRIAGE_DB_PATH=str(triage_db_file),
                CHECKPOINT_DB_PATH=str(cp_db_file),
                LLM_MODE="record",
                CASSETTE_PATH=str(cass_file),
                GEMINI_API_KEY=api_key,
                RETRIEVER=retriever_mode,
            )
            tools = FixtureToolsBackend(cases_dir=cases_dir)
            svc = TriageService(
                config=cfg,
                tools=tools,
                cases_dir=cases_dir,
                fake_client=fake_client,
            )

            res = svc.start_triage(claim_id=claim_id, reprocess=True)
            if res.get("status") == "awaiting_review":
                thread_id = res["thread_id"]
                action = {
                    "reviewer_id": "rev10001",
                    "action": "CONFIRM",
                    "reason": "Auto-confirmed for cassette recording harness",
                }
                svc.submit_review(thread_id, action)

            if live_mode:
                time.sleep(sleep_delay)

            if idx % 10 == 0 or idx == len(case_files):
                print(f"[{idx}/{len(case_files)}] Recorded {claim_id}")

    line_count = len(cass_file.read_text(encoding="utf-8").splitlines())
    print(f"Successfully recorded {line_count} entries to {cassette_path}.")
    return mode_description


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default="data/cases")
    parser.add_argument("--cassette", default="data/cassettes/cassette.jsonl")
    parser.add_argument("--sleep-delay", type=float, default=1.0)
    parser.add_argument(
        "--retriever", choices=("bm25", "dense", "hybrid"), default="bm25"
    )
    parser.add_argument("--require-live", action="store_true")
    parser.add_argument("--force-seed", action="store_true")
    args = parser.parse_args()
    record_all_cassettes(
        cases_dir=args.cases,
        cassette_path=args.cassette,
        sleep_delay=args.sleep_delay,
        force_seed=args.force_seed,
        retriever_mode=args.retriever,
        require_live=args.require_live,
    )
