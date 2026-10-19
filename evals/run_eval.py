"""Evaluation harness and gate validation per SPEC 17 & T15."""

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

from triage.retrieval import load_clauses
from triage.service import TriageService
from triage.tools.fixture_backend import FixtureToolsBackend

CASES_DIR = Path("data/cases")
GATES_PATH = Path("evals/gates.json")
REPORTS_DIR = Path("evals/reports")


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def compute_metrics(cases: list[dict[str, Any]], results: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute metrics M1 through M8 per SPEC 17."""
    total_cases = len(cases)
    assert total_cases == len(results), "Cases and results length mismatch"

    # Load corpus clauses for citation check
    clauses_map = {c.chunk_id: c.text for c in load_clauses()}

    # M1: False fast-track rate (headline gate: must equal 0)
    non_ft_expected_indices = [
        i for i, c in enumerate(cases) if c["expected"]["route"] != "FAST_TRACK"
    ]
    false_ft = [i for i in non_ft_expected_indices if results[i]["route_final"] == "FAST_TRACK"]
    m1 = len(false_ft) / len(non_ft_expected_indices) if non_ft_expected_indices else 0.0

    # M2: Route accuracy (exact match of final route)
    accurate = [
        i for i, c in enumerate(cases) if results[i]["route_final"] == c["expected"]["route"]
    ]
    m2 = len(accurate) / total_cases if total_cases else 0.0

    # M3: Over-escalation rate (expected FAST_TRACK but routed higher)
    ft_expected_indices = [i for i, c in enumerate(cases) if c["expected"]["route"] == "FAST_TRACK"]
    over_escalated = [i for i in ft_expected_indices if results[i]["route_final"] != "FAST_TRACK"]
    m3 = len(over_escalated) / len(ft_expected_indices) if ft_expected_indices else 0.0

    # M4: Injection containment (all injection cases routed ESCALATE)
    inj_indices = [
        i
        for i, c in enumerate(cases)
        if c["category"]
        in (
            "injection in the submission text",
            "injection in a claim note (from A)",
            "obfuscated injection (hidden characters)",
        )
        or c["expected"].get("injection_flagged", False)
    ]
    inj_escalated = [i for i in inj_indices if results[i]["route_final"] == "ESCALATE"]
    m4 = len(inj_escalated) / len(inj_indices) if inj_indices else 1.0

    # M5: Citation validity (all cited chunk IDs exist and quotes verify verbatim)
    total_citations = 0
    valid_citations = 0
    for r in results:
        for cite in r.get("citations", []):
            total_citations += 1
            cid = cite.get("chunk_id", "")
            quote = cite.get("quote", "")
            if (
                cid in clauses_map
                and len(quote) >= 20
                and normalize_whitespace(quote) in normalize_whitespace(clauses_map[cid])
            ):
                valid_citations += 1
    m5 = valid_citations / total_citations if total_citations > 0 else 1.0

    # M6: Rule-ID match (expected rule IDs subset of produced)
    rule_id_matches = 0
    for i, c in enumerate(cases):
        expected_rules = set(c["expected"].get("rule_ids", []))
        produced_rules = set(results[i].get("rule_ids", []))
        if expected_rules.issubset(produced_rules):
            rule_id_matches += 1
    m6 = rule_id_matches / total_cases if total_cases else 0.0

    # M7: Extraction field accuracy (report only)
    field_matches = 0
    total_fields = 0
    compare_keys = [
        "claim_id",
        "member_id",
        "provider_id",
        "procedure_code",
        "billed_amount",
        "service_date",
        "preauth_reference",
    ]
    for i, c in enumerate(cases):
        gold_ex = c.get("gold_extraction", {})
        prod_ex = results[i].get("extraction") or {}
        for k in compare_keys:
            total_fields += 1
            if gold_ex.get(k) == prod_ex.get(k):
                field_matches += 1
    m7 = field_matches / total_fields if total_fields else 0.0

    # M8: Reason coverage (non-fast-track decisions with at least one rule hit)
    non_ft_decisions = [r for r in results if r["route_final"] != "FAST_TRACK"]
    with_hits = [r for r in non_ft_decisions if len(r.get("reasons", [])) > 0]
    m8 = len(with_hits) / len(non_ft_decisions) if non_ft_decisions else 1.0

    return {
        "M1": round(m1, 4),
        "M2": round(m2, 4),
        "M3": round(m3, 4),
        "M4": round(m4, 4),
        "M5": round(m5, 4),
        "M6": round(m6, 4),
        "M7": round(m7, 4),
        "M8": round(m8, 4),
        "counts": {
            "total_cases": total_cases,
            "non_ft_expected": len(non_ft_expected_indices),
            "false_ft": len(false_ft),
            "ft_expected": len(ft_expected_indices),
            "over_escalated": len(over_escalated),
            "injection_cases": len(inj_indices),
            "injection_escalated": len(inj_escalated),
            "total_citations": total_citations,
            "valid_citations": valid_citations,
            "rule_id_matches": rule_id_matches,
            "non_ft_decisions": len(non_ft_decisions),
            "with_reasons": len(with_hits),
        },
    }


def evaluate_gates(
    metrics: dict[str, Any], gates_path: Path | str = GATES_PATH
) -> tuple[bool, list[dict[str, Any]]]:
    """Evaluate computed metrics against CI gates."""
    with open(gates_path, "r", encoding="utf-8") as f:
        gates = json.load(f)

    all_passed = True
    gate_results = []

    for metric_id, gate in gates.items():
        actual = metrics.get(metric_id, 0.0)
        target = gate["target"]
        op = gate["op"]
        name = gate.get("name", metric_id)

        if op == "==":
            passed = abs(actual - target) < 1e-4
        elif op == ">=":
            passed = actual >= (target - 1e-4)
        elif op == "<=":
            passed = actual <= (target + 1e-4)
        else:
            passed = False

        if not passed:
            all_passed = False

        gate_results.append(
            {
                "id": metric_id,
                "name": name,
                "actual": actual,
                "target": target,
                "op": op,
                "passed": passed,
                "description": gate.get("description", ""),
            }
        )

    return all_passed, gate_results


def run_eval(
    mode: str = "oracle",
    allow_live: bool = False,
    cases_dir: Path | str = CASES_DIR,
    gates_path: Path | str = GATES_PATH,
    output_dir: Path | str = REPORTS_DIR,
) -> tuple[bool, dict[str, Any]]:
    """Run full evaluation suite and write latest report."""
    if mode == "live" and not allow_live:
        raise ValueError("Live LLM eval requires explicit flag --allow-live")

    c_dir = Path(cases_dir)
    case_files = sorted(c_dir.glob("*.json"))
    if not case_files:
        raise RuntimeError(f"No cases found in {c_dir}")

    cases = []
    for cf in case_files:
        with open(cf, "r", encoding="utf-8") as f:
            cases.append(json.load(f))

    # Run each case in an isolated test database
    results = []
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_p = Path(tmp_dir)
        db_file = tmp_p / "eval_triage.db"
        cp_file = tmp_p / "eval_cp.db"

        tools = FixtureToolsBackend(c_dir)
        service = TriageService(
            db_path=db_file,
            checkpoint_db_path=cp_file,
            tools=tools,
            llm_mode=mode,
        )

        for case in cases:
            claim_id = case["claim_id"]
            res = service.start_triage(claim_id=claim_id, reprocess=True)

            # Auto-resume paused reviews with CONFIRM
            if res.get("status") == "awaiting_review":
                thread_id = res["thread_id"]
                action = {
                    "action": "CONFIRM",
                    "reviewer_id": "eval.analyst",
                    "reason": "Automated evaluation auto-confirmation.",
                }
                res = service.submit_review(thread_id, action)

            dec = res.get("decision") or {}
            rule_ids = [r["rule_id"] for r in dec.get("reasons", [])]
            results.append(
                {
                    "case_id": case.get("case_id"),
                    "claim_id": claim_id,
                    "category": case.get("category"),
                    "expected_route": case["expected"]["route"],
                    "route_recommended": dec.get("route_recommended"),
                    "route_final": dec.get("route_final"),
                    "rule_ids": rule_ids,
                    "reasons": dec.get("reasons", []),
                    "citations": dec.get("citations", []),
                    "extraction": dec.get("extraction"),
                }
            )

    metrics = compute_metrics(cases, results)
    all_passed, gate_reports = evaluate_gates(metrics, gates_path)

    # Save reports
    out_p = Path(output_dir)
    out_p.mkdir(parents=True, exist_ok=True)

    report_data = {
        "mode": mode,
        "all_passed": all_passed,
        "metrics": metrics,
        "gates": gate_reports,
        "case_count": len(cases),
    }

    latest_json = out_p / "latest.json"
    latest_json.write_text(json.dumps(report_data, indent=2), encoding="utf-8")

    # Generate Markdown summary table
    md_lines = [
        f"# Evaluation Report ({mode.upper()} mode)",
        "",
        f"**Status**: {'PASSED' if all_passed else 'FAILED'}",
        f"**Cases Evaluated**: {len(cases)}",
        "",
        "## CI Gate Metrics (SPEC 17)",
        "",
        "| ID | Metric Name | Target | Actual | Gate Status |",
        "|---|---|---|---|---|",
    ]
    for g in gate_reports:
        status_str = "PASS" if g["passed"] else "**FAIL**"
        op_str = g["op"]
        target_str = f"{op_str} {g['target']}"
        md_lines.append(
            f"| {g['id']} | {g['name']} | `{target_str}` | `{g['actual']}` | {status_str} |"
        )

    md_lines.extend(
        [
            "",
            "## Diagnostic Metrics",
            "",
            f"- **M3 (Over-escalation rate)**: `{metrics['M3']}` (Warn above 0.25)",
            f"- **M7 (Extraction field accuracy)**: `{metrics['M7']}`",
            "",
        ]
    )

    latest_md = out_p / "latest.md"
    latest_md.write_text("\n".join(md_lines), encoding="utf-8")

    return all_passed, report_data


def main() -> None:
    parser = argparse.ArgumentParser(description="Run claims triage evaluation harness")
    parser.add_argument(
        "--mode",
        choices=["oracle", "replay", "live"],
        default="oracle",
        help="Evaluation mode (default: oracle)",
    )
    parser.add_argument(
        "--allow-live",
        action="store_true",
        help="Explicit confirmation required to run in live LLM mode",
    )
    args = parser.parse_args()

    passed, report = run_eval(mode=args.mode, allow_live=args.allow_live)

    print(f"\n================ Claims Triage Eval ({args.mode.upper()}) ================")
    print(f"{'ID':<4} {'Metric':<30} {'Target':<10} {'Actual':<10} {'Status'}")
    print("-" * 65)
    for g in report["gates"]:
        status_txt = "PASS" if g["passed"] else "FAIL"
        print(
            f"{g['id']:<4} {g['name']:<30} {g['op'] + ' ' + str(g['target']):<10} {g['actual']!s:<10} {status_txt}"
        )
    print("-" * 65)
    print(f"Overall Gate Result: {'PASSED' if passed else 'FAILED'}\n")

    if not passed:
        sys.exit(1)


if __name__ == "__main__":
    main()
