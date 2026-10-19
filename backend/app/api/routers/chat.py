import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.llm.chat_orchestrator import handle_chat_message
from app.llm.providers.factory import get_llm_provider
from app.recommendation.engine import BuildResult
from app.schemas import BuildRequest
from app.services.chat_sessions import get_session

router = APIRouter()


class ChatRequest(BaseModel):
    session_id: str
    message: str


class ChatResponse(BaseModel):
    reply: str
    state: BuildRequest
    build: BuildResult | None = None


@router.post("", response_model=ChatResponse)
def chat(payload: ChatRequest, db: Session = Depends(get_db)) -> ChatResponse:
    session = get_session(payload.session_id)
    llm = get_llm_provider()
    try:
        result = handle_chat_message(db, llm, session, payload.message)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 429:
            raise HTTPException(503, "The language model is rate-limited right now. Wait a minute and try again.") from exc
        raise HTTPException(502, f"The language model provider returned an error ({exc.response.status_code}).") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Couldn't reach the language model provider. Check the LLM settings in .env.") from exc
    return ChatResponse(reply=result.reply, state=result.state, build=result.build)
