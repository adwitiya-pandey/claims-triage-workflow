"""Build SQLite scenario database from cases using Project A schema."""

import argparse
import json
import sqlite3
from pathlib import Path

SCHEMA_FILE = Path("data/schema_from_project_a.sql")
DEFAULT_CASES_DIR = Path("data/cases")
DEFAULT_OUT_DB = Path("data/scenario.db")


def build_scenario_db(cases_dir: Path, schema_file: Path, out_db: Path) -> None:
    """Build SQLite database from case fixtures enforcing schema constraints."""
    if out_db.exists():
        out_db.unlink()

    out_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(out_db))
    conn.execute("PRAGMA foreign_keys = ON;")

    # Apply schema
    with open(schema_file, "r", encoding="utf-8") as f:
        schema_sql = f.read()
    conn.executescript(schema_sql)

    cur = conn.cursor()

    plans_seen = set()
    members_seen = set()
    providers_seen = set()
    rules_seen = set()
    claims_seen = set()

    for case_file in sorted(cases_dir.glob("*.json")):
        with open(case_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Plan
        plan = data.get("plan")
        if plan and plan["plan_id"] not in plans_seen:
            cur.execute(
                "INSERT INTO plans (plan_id, plan_name, annual_limit, deductible, copay_pct) VALUES (?, ?, ?, ?, ?)",
                (
                    plan["plan_id"],
                    plan["plan_name"],
                    plan["annual_limit"],
                    plan["deductible"],
                    plan["copay_pct"],
                ),
            )
            plans_seen.add(plan["plan_id"])

        # Member
        member = data.get("member")
        if member and member["member_id"] not in members_seen:
            cur.execute(
                "INSERT INTO members (member_id, plan_id, age_band, state, enrolled_on) VALUES (?, ?, ?, ?, ?)",
                (
                    member["member_id"],
                    member["plan_id"],
                    member["age_band"],
                    member["state"],
                    member["enrolled_on"],
                ),
            )
            members_seen.add(member["member_id"])

        # Provider
        provider = data.get("provider")
        if provider and provider["provider_id"] not in providers_seen:
            cur.execute(
                "INSERT INTO providers (provider_id, specialty, state, network_status) VALUES (?, ?, ?, ?)",
                (
                    provider["provider_id"],
                    provider["specialty"],
                    provider["state"],
                    provider["network_status"],
                ),
            )
            providers_seen.add(provider["provider_id"])

        # Coverage rules
        for rule in data.get("coverage_rules", []):
            rule_key = (rule["plan_id"], rule["procedure_code"])
            if rule_key not in rules_seen:
                cur.execute(
                    "INSERT INTO coverage_rules (plan_id, procedure_code, covered, requires_preauth, max_allowed) VALUES (?, ?, ?, ?, ?)",
                    (
                        rule["plan_id"],
                        rule["procedure_code"],
                        1 if rule["covered"] else 0,
                        1 if rule["requires_preauth"] else 0,
                        rule.get("max_allowed"),
                    ),
                )
                rules_seen.add(rule_key)

        # Primary claim (skip if claim_missing is True)
        claim = data.get("claim")
        if claim and not data.get("claim_missing") and claim["claim_id"] not in claims_seen:
            cur.execute(
                """INSERT INTO claims
                (claim_id, member_id, provider_id, procedure_code, diagnosis_code, service_date, submitted_on, billed_amount, allowed_amount, paid_amount, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    claim["claim_id"],
                    claim["member_id"],
                    claim["provider_id"],
                    claim["procedure_code"],
                    claim.get("diagnosis_code", "Z00.00"),
                    claim["service_date"],
                    claim["submitted_on"],
                    claim["billed_amount"],
                    claim.get("allowed_amount"),
                    claim.get("paid_amount"),
                    claim["status"],
                ),
            )
            claims_seen.add(claim["claim_id"])

        # Other claims (e.g. for duplicates or limits)
        for other in data.get("other_claims", []):
            if other["claim_id"] not in claims_seen:
                cur.execute(
                    """INSERT INTO claims
                    (claim_id, member_id, provider_id, procedure_code, diagnosis_code, service_date, submitted_on, billed_amount, allowed_amount, paid_amount, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        other["claim_id"],
                        other["member_id"],
                        other["provider_id"],
                        other["procedure_code"],
                        other.get("diagnosis_code", "Z00.00"),
                        other["service_date"],
                        other["submitted_on"],
                        other["billed_amount"],
                        other.get("allowed_amount"),
                        other.get("paid_amount"),
                        other["status"],
                    ),
                )
                claims_seen.add(other["claim_id"])

        # Notes
        cid = data.get("claim_id")
        if cid and not data.get("claim_missing"):
            for note in data.get("notes", []):
                cur.execute(
                    "INSERT INTO claim_notes (claim_id, created_on, author_type, note_text) VALUES (?, ?, ?, ?)",
                    (
                        cid,
                        note.get("created_on", "2026-05-20"),
                        note.get("author_type", "CLAIMANT"),
                        note.get("note_text", ""),
                    ),
                )

    conn.commit()
    conn.close()
    print(f"Scenario database built successfully at {out_db}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build scenario database from cases.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_DIR, help="Path to cases dir")
    parser.add_argument("--schema", type=Path, default=SCHEMA_FILE, help="Path to schema SQL")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DB, help="Output SQLite DB path")
    args = parser.parse_args()

    build_scenario_db(args.cases, args.schema, args.out)
