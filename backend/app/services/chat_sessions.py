"""In-memory conversational state, keyed by session_id.

A process-local dict, not persisted to the `builds` table or shared
across workers. Fine for a single dev-server process; a production
deployment would move this to Postgres or Redis.
"""

from dataclasses import dataclass, field

from app.recommendation.engine import BuildResult
from app.schemas import BuildRequest

# Enough for "switch to it" and "the cheaper one" to resolve against the
# last couple of exchanges, without sending the whole chat on every call.
HISTORY_MESSAGES = 6
HISTORY_REPLY_CHARS = 800
MAX_MENTIONED_PARTS = 8


@dataclass
class ChatSession:
    state: BuildRequest | None = None
    last_result: BuildResult | None = None
    # Set once the user has been quoted a starting budget, so a second
    # budget-less message gets that build instead of the same question.
    offered_starting_budget: bool = False
    # Set once the user has been asked what the PC is for, so a second
    # message without a use case builds for general use instead.
    asked_use_case: bool = False
    # Recent {"role", "content"} messages, oldest first. Long replies are
    # cut short: references point at part names, which come early.
    history: list[dict] = field(default_factory=list)
    # Parts that follow-up answers named, newest first, as
    # {"id", "category", "name", "price_php"}; the intent parser can pin them.
    mentioned_parts: list[dict] = field(default_factory=list)

    def remember(self, message: str, reply: str) -> None:
        self.history.append({"role": "user", "content": message})
        self.history.append({"role": "assistant", "content": reply[:HISTORY_REPLY_CHARS]})
        del self.history[:-HISTORY_MESSAGES]

    def remember_parts(self, parts: list[dict]) -> None:
        new_ids = {p["id"] for p in parts}
        self.mentioned_parts = (parts + [p for p in self.mentioned_parts if p["id"] not in new_ids])[:MAX_MENTIONED_PARTS]


_SESSIONS: dict[str, ChatSession] = {}


def get_session(session_id: str) -> ChatSession:
    if session_id not in _SESSIONS:
        _SESSIONS[session_id] = ChatSession()
    return _SESSIONS[session_id]
