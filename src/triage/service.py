"""Triage orchestration service and state machine per SPEC 13."""

import re
import sqlite3
import threading
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from triage.config import get_settings
from triage.graph import build_graph
from triage.models import ReviewAction
from triage.nodes import make_nodes
from triage.store import (
    get_awaiting_review,
    get_decision,
    get_latest_decision,
    get_latest_version,
    get_review_by_thread,
    init_db,
    list_decisions,
    list_pending_reviews,
    resolve_review,
    save_review_awaiting,
)
from triage.tools.base import ClaimsTools
from triage.tools.fixture_backend import FixtureToolsBackend


class NotFoundError(Exception):
    """Raised when an entity (claim, thread, decision) cannot be found."""


class ConflictError(Exception):
    """Raised when a state conflict occurs (e.g. submitting review on an already resolved thread)."""


class TriageService:
    """Orchestration service coordinating LangGraph execution, persistence, and human review."""

    def __init__(
        self,
        db_path: Path | str | None = None,
        checkpoint_db_path: Path | str | None = None,
        tools: ClaimsTools | None = None,
        llm_mode: str | None = None,
        checkpointer: Any = None,
    ):
        settings = get_settings()
        self.db_path = Path(db_path or settings.TRIAGE_DB_PATH)
        self.checkpoint_db_path = Path(checkpoint_db_path or settings.CHECKPOINT_DB_PATH)
        self.llm_mode = llm_mode or settings.LLM_MODE

        init_db(self.db_path)

        # Initialize tools backend
        if tools is not None:
            self.tools = tools
        elif settings.CLAIMS_MCP_URL:
            from triage.tools.mcp_backend import McpToolsBackend

            self.tools = McpToolsBackend(
                url=settings.CLAIMS_MCP_URL,
                key=settings.CLAIMS_MCP_KEY,
            )
        else:
            self.tools = FixtureToolsBackend("data/cases")

        # Global invocation lock to serialize graph executions per SPEC 14
        self._lock = threading.Lock()

        # Build checkpointer and graph
        if checkpointer is not None:
            self.checkpointer = checkpointer
        else:
            self.checkpoint_db_path.parent.mkdir(parents=True, exist_ok=True)
            self._cp_conn = sqlite3.connect(str(self.checkpoint_db_path), check_same_thread=False)
            self.checkpointer = SqliteSaver(self._cp_conn)

        nodes = make_nodes(
            {
                "tools": self.tools,
                "llm_mode": self.llm_mode,
                "db_path": self.db_path,
            }
        )
        self.graph = build_graph(nodes, checkpointer=self.checkpointer)

    def _extract_packet_from_state(self, thread_id: str) -> dict[str, Any]:
        """Extract the review packet payload from graph checkpointer state."""
        cfg = {"configurable": {"thread_id": thread_id}}
        st = self.graph.get_state(cfg)
        if st and st.tasks:
            for task in st.tasks:
                if task.interrupts:
                    return task.interrupts[0].value
        return {}

    def start_triage(self, claim_id: str, reprocess: bool = False) -> dict[str, Any]:
        """Start or retrieve claims triage workflow according to idempotency rules in SPEC 13."""
        if not re.fullmatch(r"CLM[0-9]{8}", claim_id):
            raise ValueError(f"Invalid claim ID format: {claim_id}")

        # 1. If an awaiting review exists for the claim, return it without creating a new thread
        awaiting = get_awaiting_review(self.db_path, claim_id)
        if awaiting:
            thread_id = awaiting["thread_id"]
            packet = self._extract_packet_from_state(thread_id)
            return {
                "status": "awaiting_review",
                "thread_id": thread_id,
                "packet": packet,
            }

        # 2. If a decision already exists and reprocess is False, return the latest decision
        if not reprocess:
            latest = get_latest_decision(self.db_path, claim_id)
            if latest:
                return {
                    "status": "completed",
                    "decision": latest,
                }

        # 3. Create version n+1 and run the graph
        current_version = get_latest_version(self.db_path, claim_id)
        version = current_version + 1
        thread_id = f"triage-{claim_id}-v{version}"
        cfg = {"configurable": {"thread_id": thread_id}}

        with self._lock:
            result = self.graph.invoke({"claim_id": claim_id, "version": version}, cfg)

            # Check if execution paused at human review gate
            if result.get("__interrupt__"):
                packet = result["__interrupt__"][0].value
                save_review_awaiting(self.db_path, thread_id, claim_id, version)
                return {
                    "status": "awaiting_review",
                    "thread_id": thread_id,
                    "packet": packet,
                }

            # Completed without pause (e.g. FAST_TRACK)
            decision = get_decision(self.db_path, claim_id, version) or result.get("decision")
            return {
                "status": "completed",
                "decision": decision,
            }

    def submit_review(
        self, thread_id: str, review_action: dict[str, Any] | ReviewAction
    ) -> dict[str, Any]:
        """Resume a paused workflow with a human review action per SPEC 12 & 13."""
        review_row = get_review_by_thread(self.db_path, thread_id)
        if not review_row:
            raise NotFoundError(f"Review thread '{thread_id}' not found")

        if review_row["status"] == "done":
            raise ConflictError(f"Review thread '{thread_id}' has already been resolved")

        # Validate action against Pydantic model
        if isinstance(review_action, dict):
            action_obj = ReviewAction.model_validate(review_action)
        else:
            action_obj = review_action

        cfg = {"configurable": {"thread_id": thread_id}}

        with self._lock:
            res = self.graph.invoke(Command(resume=action_obj.model_dump(mode="json")), cfg)
            resolve_review(self.db_path, thread_id, action_obj.reviewer_id)

            claim_id = review_row["claim_id"]
            version = review_row["version"]
            decision = get_decision(self.db_path, claim_id, version) or res.get("decision")
            return {
                "status": "completed",
                "decision": decision,
            }

    def get_decisions(self, claim_id: str) -> list[dict[str, Any]]:
        """Retrieve all decision versions for a claim, ordered newest first."""
        if not re.fullmatch(r"CLM[0-9]{8}", claim_id):
            raise ValueError(f"Invalid claim ID format: {claim_id}")

        decisions = list_decisions(self.db_path, claim_id, newest_first=True)
        if not decisions:
            raise NotFoundError(f"No decisions found for claim '{claim_id}'")
        return decisions

    def list_pending(self) -> list[dict[str, Any]]:
        """List all awaiting review threads."""
        return list_pending_reviews(self.db_path)
