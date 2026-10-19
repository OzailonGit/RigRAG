"""Conversational state per chat, keyed by session_id and stored in the
chat_sessions table, so a conversation survives a server restart and is
shared by every worker.
"""

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.json_utils import to_jsonable
from app.models.chat_session import ChatSessionRecord
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


def _to_data(session: ChatSession) -> dict:
    return {
        "state": session.state.model_dump(mode="json") if session.state else None,
        # to_jsonable keeps prices numeric (model_dump's JSON mode turns
        # Decimals into strings, which break price formatting on reload).
        "last_result": to_jsonable(session.last_result.model_dump()) if session.last_result else None,
        "offered_starting_budget": session.offered_starting_budget,
        "asked_use_case": session.asked_use_case,
        "history": session.history,
        "mentioned_parts": to_jsonable(session.mentioned_parts),
    }


def _from_data(data: dict) -> ChatSession:
    return ChatSession(
        state=BuildRequest.model_validate(data["state"]) if data.get("state") else None,
        last_result=BuildResult.model_validate(data["last_result"]) if data.get("last_result") else None,
        offered_starting_budget=data.get("offered_starting_budget", False),
        asked_use_case=data.get("asked_use_case", False),
        history=data.get("history", []),
        mentioned_parts=data.get("mentioned_parts", []),
    )


def get_session(db: Session, session_id: str) -> ChatSession:
    """The stored session, or a new one for an unknown ID."""
    record = db.get(ChatSessionRecord, session_id)
    return _from_data(record.data) if record else ChatSession()


def save_session(db: Session, session_id: str, session: ChatSession) -> None:
    record = db.get(ChatSessionRecord, session_id)
    if record is None:
        db.add(ChatSessionRecord(id=session_id, data=_to_data(session)))
    else:
        record.data = _to_data(session)
    db.commit()
