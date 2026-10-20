"""Run with one worker: python -m uvicorn backend.main:app --host 127.0.0.1."""
"""v0.2.1"""
from contextlib import asynccontextmanager
import json
import logging
import os
from typing import Annotated
from uuid import UUID

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field, field_validator

from backend.core import KnowledgeBase, Settings
from backend.generation import DeepSeek, GenerationError, answer_question

load_dotenv()
logger = logging.getLogger(__name__)


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=10)
    document_ids: list[UUID] | None = Field(default=None, max_length=100)

    @field_validator("question")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("问题不能为空")
        return value.strip()


def create_app(kb=None, generator=None, api_tokens=None):
    @asynccontextmanager
    async def lifespan(app):
        token_map = api_tokens if api_tokens is not None else json.loads(os.getenv("API_TOKENS_JSON", "{}"))
        if not isinstance(token_map, dict) or not token_map or any(
            not isinstance(k, str) or len(k) < 16 or not isinstance(v, str) or not v.strip()
            for k, v in token_map.items()
        ):
            raise RuntimeError("API_TOKENS_JSON 必须是 {至少16位随机令牌: 用户标识} 的非空映射")
        app.state.tokens = token_map
        app.state.kb = kb or KnowledgeBase(Settings.from_env())
        app.state.generator = generator or DeepSeek()
        try:
            yield
        finally:
            if kb is None:
                app.state.kb.close()

    app = FastAPI(title="RAG 学术文献助手", version="0.2.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(","),
        allow_methods=["GET", "POST", "DELETE"], allow_headers=["Content-Type", "X-API-Key"],
    )
    key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

    def current_owner(key: str | None = Depends(key_header)):
        owner = app.state.tokens.get(key)
        if owner is None:
            raise HTTPException(401, "请提供有效的 X-API-Key")
        return owner

    Owner = Annotated[str, Depends(current_owner)]

    def run(action):
        try:
            return action()
        except KeyError:
            raise HTTPException(404, "论文不存在或无访问权限") from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        except GenerationError as exc:
            raise HTTPException(502, str(exc)) from None
        except Exception:
            logger.exception("RAG operation failed")
            raise HTTPException(500, "处理失败，请查看后端日志") from None

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": "0.2.0"}

    @app.post("/api/documents")
    def upload(owner: Owner, file: UploadFile = File(...)):
        try:
            if not (file.filename or "").lower().endswith(".pdf"):
                raise HTTPException(415, "只支持 PDF 文件")
            limit = app.state.kb.settings.max_upload_mb * 1024 * 1024
            data = file.file.read(limit + 1)
            if len(data) > limit:
                raise HTTPException(413, "文件超过上传大小限制")
            return run(lambda: app.state.kb.ingest(owner, file.filename, data))
        finally:
            file.file.close()

    @app.get("/api/documents")
    def documents(owner: Owner):
        return {"documents": run(lambda: app.state.kb.documents(owner))}

    @app.delete("/api/documents/{document_id}")
    def delete(document_id: UUID, owner: Owner):
        run(lambda: app.state.kb.delete(owner, str(document_id)))
        return {"deleted": True}

    @app.get("/api/documents/{document_id}/file")
    def original(document_id: UUID, owner: Owner):
        doc = run(lambda: app.state.kb.document(owner, str(document_id)))
        return FileResponse(app.state.kb.pdf_dir / f"{document_id}.pdf", media_type="application/pdf",
                            filename=doc["filename"], content_disposition_type="inline")

    @app.post("/api/search")
    def search(body: Question, owner: Owner):
        return {"chunks": run(lambda: app.state.kb.retrieve(owner, body.question, body.top_k,
                None if body.document_ids is None else [str(i) for i in body.document_ids]))}

    @app.post("/api/chat")
    def chat(body: Question, owner: Owner):
        return run(lambda: answer_question(app.state.kb, app.state.generator, owner, body.question,
                   body.top_k, None if body.document_ids is None else [str(i) for i in body.document_ids]))

    return app


app = create_app()
