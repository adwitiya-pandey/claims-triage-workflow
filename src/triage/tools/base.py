"""Claims tools interface and typed error definition per SPEC 8."""

from typing import Protocol, runtime_checkable


class ToolError(Exception):
    """Typed tool execution error matching Project A error taxonomy."""

    def __init__(self, code: str, message: str = ""):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}" if message else code)


@runtime_checkable
class ClaimsTools(Protocol):
    """Read-only interface mirroring Project A's tools.

    Under SPEC S4, no write method may exist on this protocol or any implementation.
    """

    def get_claim(self, claim_id: str) -> dict:
        """Fetch claim joined with provider network status and member plan."""
        ...

    def list_member_claims(
        self,
        member_id: str,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """List member claims newest first."""
        ...

    def get_coverage_rule(self, plan_id: str, procedure_code: str) -> dict:
        """Fetch coverage rule for a plan and procedure."""
        ...

    def get_member_ytd_summary(self, member_id: str, year: int) -> dict:
        """Fetch year-to-date summary and remaining limit."""
        ...

    def get_claim_notes(self, claim_id: str, limit: int = 10) -> dict:
        """Fetch claim notes marked with untrusted: true."""
        ...
