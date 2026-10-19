from fastapi import FastAPI

from app.api.routers import build, chat, compare, compatibility, parts, search

app = FastAPI(
    title="PC Builder RAG API",
    description="RAG-powered PC part recommendation engine.",
    version="0.1.0",
)

app.include_router(parts.router, prefix="/api/parts", tags=["parts"])
app.include_router(compatibility.router, prefix="/api/compatibility", tags=["compatibility"])
app.include_router(search.router, prefix="/api/search", tags=["search"])
app.include_router(build.router, prefix="/api/build", tags=["build"])
app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
app.include_router(compare.router, prefix="/api/compare", tags=["compare"])


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}
