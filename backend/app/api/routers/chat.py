import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.llm.chat_orchestrator import handle_chat_message
from app.llm.citations import sources_markdown
from app.llm.providers.factory import get_llm_provider
from app.recommendation.engine import BuildResult
from app.schemas import BuildRequest
from app.services.chat_sessions import get_session, save_session

router = APIRouter()


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)  # the chat_sessions key
    message: str


class NoteSource(BaseModel):
    publisher: str
    title: str
    url: str
    kind: str  # "manufacturer", "review", or "press"
    published: str | None = None


class CitedNote(BaseModel):
    """A knowledge note the reply cites as [ref]."""
    ref: int
    note_id: str
    title: str
    checked: str  # when the note's sources were last read
    sources: list[NoteSource]


class ChatResponse(BaseModel):
    reply: str  # ends with a "Sources" list when it cites notes
    state: BuildRequest
    build: BuildResult | None = None
    sources: list[CitedNote] = []


@router.post("", response_model=ChatResponse)
def chat(payload: ChatRequest, db: Session = Depends(get_db)) -> ChatResponse:
    session = get_session(db, payload.session_id)
    llm = get_llm_provider()
    try:
        result = handle_chat_message(db, llm, session, payload.message)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 429:
            raise HTTPException(503, "The language model is rate-limited right now. Wait a minute and try again.") from exc
        raise HTTPException(502, f"The language model provider returned an error ({exc.response.status_code}).") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Couldn't reach the language model provider. Check the LLM settings in .env.") from exc
    # Saved only after a whole turn succeeds, so a failed call leaves the
    # stored conversation as it was.
    save_session(db, payload.session_id, session)
    return ChatResponse(reply=result.reply + sources_markdown(result.sources), state=result.state,
                        build=result.build, sources=result.sources)
