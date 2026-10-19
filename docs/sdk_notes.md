# SDK notes

Pre-filled on 17 Oct 2026 by running langgraph 1.2.16, langgraph-checkpoint-sqlite 3.1.2, google-genai 2.31.0 and mcp 2.3.0 in a throwaway Python 3.13 environment. **The coding agent must re-run the probes below against the versions `uv` installs (Python 3.12) and correct this file where anything differs.** Anything not listed here is unverified: do not guess.

## LangGraph and the SQLite checkpointer (verified)
```python
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.sqlite import SqliteSaver
```
- `interrupt(value, *, response_schema=None)`: never pass `response_schema`.
- `SqliteSaver(conn: sqlite3.Connection, *, serde=None)`. For use from API threads: `sqlite3.connect(path, check_same_thread=False)`. `setup()` is called automatically; tables created: `checkpoints`, `writes`. `SqliteSaver.from_conn_string` also exists.
- Paused `graph.invoke(...)` returns the state plus `"__interrupt__": [Interrupt(value=..., id=...)]`; payload = `result["__interrupt__"][0].value`.
- `graph.get_state(config).next` is `('review',)` while paused and `()` when finished.
- **Restart safety (verified):** pause on one `SqliteSaver`, close the connection, build a *new* graph object on a *new* connection to the same file, `invoke(Command(resume={"action": "CONFIRM"}), cfg)` with the same `thread_id`: the run completed and `review` received the resume value.

## google-genai (verified)
- `types.GenerateContentConfig` has `system_instruction`, `response_mime_type`, `response_json_schema`, `response_schema`, `temperature`, `max_output_tokens`. Use `response_json_schema` with a JSON-schema dict, as SPEC 10 says.
- `response.usage_metadata` fields: `prompt_token_count`, `candidates_token_count`, `total_token_count`, `thoughts_token_count`, `cached_content_token_count` (any may be `None`; treat `None` as 0). `response.text` exists.
- `google.genai.errors`: `APIError` (`.code`, `.status`, `.message`), `ClientError` (4xx), `ServerError` (5xx).
- `gemini-3.8-flash` is listed as Stable on the official models page (17 Oct 2026). Free-tier limits: not verified.

## mcp client (verified; Project A's wire format is in A's SPEC 8)
```python
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
client = Client(streamable_http_client(url, http_client=<async HTTP client with Authorization header>))
```
- The HTTP client class the SDK itself imports is `httpx2`; confirm with the installed package before use.
- `await client.call_tool(name, args)` returns an object with `.is_error`, `.content` (text items), `.structured_content` (was `None` for a plain `dict` return on a probe server).
- Tool errors are *results* with `is_error=True` and text starting with the code. HTTP 401 would surface as a transport exception.

## Not verified
Free-tier rate limits and data-use terms for the Gemini model; retry-after behaviour on 429; how `get_state` behaves for a thread that finished before a restart; whether Project A's HTTP Bearer check needs anything extra from the client.

## Probe snippet (re-run)
```python
import sqlite3, tempfile, os
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.sqlite import SqliteSaver

class S(TypedDict, total=False):
    x: str
    review: dict

def a(s): return {"x": "a"}
def review(s): return {"review": interrupt({"packet": "p1"})}

def build(cp):
    g = StateGraph(S); g.add_node("a", a); g.add_node("review", review)
    g.add_edge(START, "a"); g.add_edge("a", "review"); g.add_edge("review", END)
    return g.compile(checkpointer=cp)

path = os.path.join(tempfile.mkdtemp(), "cp.db"); cfg = {"configurable": {"thread_id": "t-1"}}
c1 = sqlite3.connect(path, check_same_thread=False); g1 = build(SqliteSaver(c1))
r = g1.invoke({"x": "in"}, cfg); print(r["__interrupt__"][0].value, g1.get_state(cfg).next)
c1.close()
c2 = sqlite3.connect(path, check_same_thread=False); g2 = build(SqliteSaver(c2))
print(g2.invoke(Command(resume={"action": "CONFIRM"}), cfg), g2.get_state(cfg).next)
```
