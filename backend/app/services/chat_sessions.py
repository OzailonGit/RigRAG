"""In-memory conversational state, keyed by session_id.

A process-local dict, not persisted to the `builds` table or shared
across workers. Fine for a single dev-server process; a production
deployment would move this to Postgres or Redis.
"""

from dataclasses import dataclass

from app.recommendation.engine import BuildResult
from app.schemas import BuildRequest


@dataclass
class ChatSession:
    state: BuildRequest | None = None
    last_result: BuildResult | None = None


_SESSIONS: dict[str, ChatSession] = {}


def get_session(session_id: str) -> ChatSession:
    if session_id not in _SESSIONS:
        _SESSIONS[session_id] = ChatSession()
    return _SESSIONS[session_id]
