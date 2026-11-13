"""Research workspace API, separate from the legacy single-turn endpoints."""

import asyncio
import json
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field, field_validator


class NoteRequest(BaseModel):
    id: UUID
    document_id: UUID
    page: int = Field(ge=1, le=500, strict=True)
    kind: Literal["note", "bookmark"] = "note"
    text: str = Field(default="", max_length=10000)
    quote: str = Field(default="", max_length=6000)
    tags: list[str] = Field(default_factory=list, max_length=10)
    revision: int = Field(default=0, ge=0)


class ConversationRequest(BaseModel):
    id: UUID
    document_ids: list[UUID] | None = Field(default=None, max_length=100)


class RenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class TurnRequest(BaseModel):
    request_id: UUID
    question: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=10, ge=1, le=30)

    @field_validator("question")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("问题不能为空")
        return value.strip()


class Term(BaseModel):
    source: str = Field(min_length=1, max_length=120)
    target: str = Field(min_length=1, max_length=160)


class GlossaryRequest(BaseModel):
    entries: list[Term] = Field(max_length=100)
    revision: int = Field(ge=0)


class AnalysisRequest(BaseModel):
    request_id: UUID
    document_ids: list[UUID] = Field(min_length=1, max_length=5)


class CardEdit(BaseModel):
    document_id: UUID
    field: str
    text: str | None = Field(default=None, max_length=6000)
    revision: int = Field(ge=0)


def register_research(app, current_owner, run):
    Owner = Annotated[str, Depends(current_owner)]

    def download(data, name):
        return Response(
            data,
            media_type="text/plain; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{name}.txt"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/api/notes")
    def notes(owner: Owner, document_id: UUID | None = None):
        return {
            "notes": run(
                lambda: app.state.research.notes(owner, str(document_id) if document_id else None)
            )
        }

    @app.post("/api/notes")
    def save_note(body: NoteRequest, owner: Owner):
        return run(
            lambda: app.state.research.save_note(
                owner,
                str(body.id),
                str(body.document_id),
                body.page,
                body.kind,
                body.text,
                body.quote,
                body.tags,
                body.revision,
            )
        )

    @app.get("/api/notes/export")
    def export_notes(owner: Owner, document_id: UUID | None = None):
        return download(
            run(
                lambda: app.state.research.export_notes(
                    owner, str(document_id) if document_id else None
                )
            ),
            "papertrail-notes",
        )

    @app.delete("/api/notes/{note_id}")
    def delete_note(note_id: UUID, owner: Owner):
        run(lambda: app.state.research.delete_note(owner, str(note_id)))
        return {"deleted": True}

    @app.get("/api/conversations")
    def conversations(owner: Owner):
        return {"conversations": run(lambda: app.state.research.conversations(owner))}

    @app.post("/api/conversations")
    def new_conversation(body: ConversationRequest, owner: Owner):
        return run(
            lambda: app.state.research.create_conversation(
                owner,
                None if body.document_ids is None else [str(d) for d in body.document_ids],
                conversation_id=str(body.id),
            )
        )

    @app.get("/api/conversations/{cid}")
    def conversation(cid: UUID, owner: Owner):
        return run(lambda: app.state.research.conversation(owner, str(cid)))

    @app.patch("/api/conversations/{cid}")
    def rename_conversation(cid: UUID, body: RenameRequest, owner: Owner):
        run(lambda: app.state.research.rename_conversation(owner, str(cid), body.title))
        return {"updated": True}

    @app.delete("/api/conversations/{cid}")
    def delete_conversation(cid: UUID, owner: Owner):
        run(lambda: app.state.research.delete_conversation(owner, str(cid)))
        return {"deleted": True}

    @app.post("/api/conversations/{cid}/stream")
    def conversation_stream(cid: UUID, body: TurnRequest, owner: Owner):
        request_id = run(
            lambda: app.state.tasks.start_chat(
                owner, str(cid), str(body.request_id), body.question, body.top_k
            )
        )

        async def events():
            emitted, tick = "", 0
            while True:
                # No model work in this iterator: browser disconnects do not cancel storage.
                row = app.state.research.turn(owner, str(cid), request_id)
                if row["status"] == "completed":
                    yield (
                        json.dumps({"type": "done", "answer": row["answer"]}, ensure_ascii=False)
                        + "\n"
                    )
                    return
                preview = row["preview"]
                if preview.startswith(emitted) and len(preview) > len(emitted):
                    yield (
                        json.dumps(
                            {"type": "delta", "text": preview[len(emitted) :]}, ensure_ascii=False
                        )
                        + "\n"
                    )
                    emitted = preview
                if row["status"] != "running":
                    yield (
                        json.dumps(
                            {"type": "error", "message": row["error"] or "回答未完成"},
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                    return
                if tick % 50 == 0:
                    yield (
                        json.dumps(
                            {
                                "type": "progress",
                                "message": "正在检索并生成回答，结果会保存到问答记录",
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                tick += 1
                await asyncio.sleep(0.2)

        return StreamingResponse(
            events(),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/glossaries/{language}")
    def glossary(language: Literal["zh", "en"], owner: Owner):
        return run(lambda: app.state.research.glossary(owner, language))

    @app.post("/api/glossaries/{language}")
    def save_glossary(language: Literal["zh", "en"], body: GlossaryRequest, owner: Owner):
        return run(
            lambda: app.state.research.save_glossary(
                owner, language, [e.model_dump() for e in body.entries], body.revision
            )
        )

    @app.post("/api/analyses", status_code=202)
    def analysis(body: AnalysisRequest, owner: Owner):
        return run(
            lambda: app.state.tasks.submit_analysis(
                owner, str(body.request_id), [str(d) for d in body.document_ids]
            )
        )

    @app.get("/api/analyses")
    def analyses(owner: Owner):
        return {"jobs": run(lambda: app.state.tasks.list(owner, "analysis"))}

    @app.get("/api/analyses/{job_id}")
    def get_analysis(job_id: UUID, owner: Owner):
        job = run(lambda: app.state.tasks.get(owner, str(job_id)))
        if job["kind"] != "analysis":
            raise HTTPException(404, "任务不存在")
        return job

    @app.post("/api/analyses/{job_id}/cancel")
    def cancel_analysis(job_id: UUID, owner: Owner):
        return run(lambda: app.state.tasks.cancel(owner, str(job_id)))

    @app.patch("/api/analyses/{job_id}")
    def edit_analysis(job_id: UUID, body: CardEdit, owner: Owner):
        return run(
            lambda: app.state.tasks.edit(
                owner, str(job_id), str(body.document_id), body.field, body.text, body.revision
            )
        )

    @app.get("/api/analyses/{job_id}/file")
    def export_analysis(job_id: UUID, owner: Owner):
        return download(
            run(lambda: app.state.tasks.export(owner, str(job_id))), f"papertrail-analysis-{job_id}"
        )

    @app.post("/api/uploads", status_code=202)
    def upload(owner: Owner, request_id: UUID = Form(...), file: UploadFile = File(...)):
        try:
            if not (file.filename or "").lower().endswith(".pdf"):
                raise HTTPException(415, "只支持 PDF 文件")
            limit = app.state.kb.settings.max_upload_mb * 1024 * 1024
            data = file.file.read(limit + 1)
            if len(data) > limit:
                raise HTTPException(413, "文件超过上传大小限制")
            return run(
                lambda: app.state.tasks.submit_upload(owner, str(request_id), file.filename, data)
            )
        finally:
            file.file.close()

    @app.get("/api/uploads")
    def uploads(owner: Owner):
        return {"jobs": run(lambda: app.state.tasks.list(owner, "upload"))}

    @app.get("/api/uploads/{job_id}")
    def get_upload(job_id: UUID, owner: Owner):
        job = run(lambda: app.state.tasks.get(owner, str(job_id)))
        if job["kind"] != "upload":
            raise HTTPException(404, "任务不存在")
        return job
