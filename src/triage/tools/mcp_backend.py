"""MCP tools backend connecting to Project A over HTTP Streamable protocol per SPEC 8."""

import asyncio
import concurrent.futures
import json
import re
import time
from collections.abc import Callable
from typing import Any

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from triage.config import get_settings
from triage.tools.base import ClaimsTools, ToolError


class McpToolsBackend(ClaimsTools):
    """Read-only claims tools backend calling Project A's MCP server over HTTP.

    Adheres strictly to SPEC S4: absolutely no write methods may exist.
    """

    def __init__(
        self,
        url: str | None = None,
        key: str | None = None,
        timeout: float = 10.0,
        client_override: Any = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ):
        settings = get_settings()
        self.url = url or settings.CLAIMS_MCP_URL
        self.key = key or settings.CLAIMS_MCP_KEY
        self.timeout = timeout
        self.client_override = client_override
        self.sleep_fn = sleep_fn

    def _run_sync(self, coro: Any) -> Any:
        """Execute an asynchronous coroutine synchronously, handling running event loops safely."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, coro).result()
        return asyncio.run(coro)

    def _call_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Call an MCP tool with per-call timeouts and retry on RATE_LIMITED only."""
        return self._run_sync(self._call_tool_async(name, args))

    async def _call_tool_async(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        backoff_delays = [1.0, 2.0, 4.0]
        max_attempts = len(backoff_delays) + 1

        for attempt in range(max_attempts):
            try:
                if self.client_override is not None:
                    call_res = await self.client_override.call_tool(name, args)
                else:
                    if not self.url:
                        raise ToolError("INTERNAL", "CLAIMS_MCP_URL is not configured")

                    async with (
                        httpx2.AsyncClient(
                            headers={"Authorization": f"Bearer {self.key}"},
                            timeout=self.timeout,
                        ) as hc,
                        Client(streamable_http_client(self.url, http_client=hc)) as mcp_client,
                    ):
                        call_res = await asyncio.wait_for(
                            mcp_client.call_tool(name, args),
                            timeout=self.timeout,
                        )

                # Check for tool-level error result
                if getattr(call_res, "is_error", False):
                    text = ""
                    if hasattr(call_res, "content") and call_res.content:
                        text = getattr(call_res.content[0], "text", "")

                    code_match = re.match(r"^([A-Z_]+)", text.strip())
                    err_code = code_match.group(1) if code_match else "INTERNAL"

                    if err_code == "RATE_LIMITED" and attempt < len(backoff_delays):
                        self.sleep_fn(backoff_delays[attempt])
                        continue

                    raise ToolError(err_code, text)

                # Success: parse JSON response
                if hasattr(call_res, "content") and call_res.content:
                    text_data = getattr(call_res.content[0], "text", "")
                    if text_data:
                        try:
                            return json.loads(text_data)
                        except json.JSONDecodeError:
                            return {"raw": text_data}

                if hasattr(call_res, "structured_content") and call_res.structured_content:
                    return call_res.structured_content

                return {}

            except ToolError:
                raise
            except Exception as exc:
                # Check for HTTP 401 unauthorized
                status = getattr(exc, "status_code", None)
                if status == 401 or "401" in str(exc) or "Unauthorized" in str(exc):
                    raise ToolError("UNAUTHENTICATED", str(exc)) from exc

                # Check for timeout
                if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
                    raise ToolError(
                        "INTERNAL", f"Tool call {name} timed out after {self.timeout}s"
                    ) from exc

                raise ToolError("INTERNAL", str(exc)) from exc

        raise ToolError("RATE_LIMITED", f"Tool {name} rate limit retries exhausted")

    def get_claim(self, claim_id: str) -> dict:
        """Fetch claim joined with provider network status and member plan."""
        return self._call_tool("get_claim", {"claim_id": claim_id})

    def list_member_claims(
        self,
        member_id: str,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """List member claims newest first."""
        args: dict[str, Any] = {"member_id": member_id, "limit": limit, "offset": offset}
        if status is not None:
            args["status"] = status
        return self._call_tool("list_member_claims", args)

    def get_coverage_rule(self, plan_id: str, procedure_code: str) -> dict:
        """Fetch coverage rule for a plan and procedure."""
        return self._call_tool(
            "get_coverage_rule",
            {"plan_id": plan_id, "procedure_code": procedure_code},
        )

    def get_member_ytd_summary(self, member_id: str, year: int) -> dict:
        """Fetch year-to-date summary and remaining limit."""
        return self._call_tool(
            "get_member_ytd_summary",
            {"member_id": member_id, "year": year},
        )

    def get_claim_notes(self, claim_id: str, limit: int = 10) -> dict:
        """Fetch claim notes marked with untrusted: true."""
        return self._call_tool("get_claim_notes", {"claim_id": claim_id, "limit": limit})
