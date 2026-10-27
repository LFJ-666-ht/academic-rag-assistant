"""Seedream adapter and durable, owner-scoped jobs for the single-process backend."""

import base64
import binascii
import json
import os
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
import pymupdf

from backend.generation import GenerationError, sources_from_chunks

MAX_RESPONSE = 32 * 1024 * 1024
MAX_IMAGE = 24 * 1024 * 1024


class ImageConflict(Exception):
    pass


class ImageBusy(Exception):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


class Seedream:
    def __init__(self):
        self.key = os.getenv("SEEDREAM_API_KEY", "").strip()
        self.model = os.getenv("SEEDREAM_MODEL", "doubao-seedream-4-0-20260415").strip()
        self.base = (
            os.getenv("SEEDREAM_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
            .strip()
            .rstrip("/")
        )

    @property
    def enabled(self):
        return bool(self.key and self.model and self.base)

    def __call__(self, prompt):
        if not self.enabled:
            raise GenerationError(
                "请在后端 .env 配置 SEEDREAM_API_KEY、SEEDREAM_MODEL 和 SEEDREAM_BASE_URL，再重启后端"
            )
        payload = {
            "model": self.model,
            "prompt": prompt,
            "size": "2K",
            "sequential_image_generation": "disabled",
            "stream": False,
            "response_format": "b64_json",
            "watermark": True,
        }
        try:
            # One request only: never retry a potentially billed generation automatically.
            started = time.monotonic()
            with httpx.Client(
                trust_env=False, timeout=httpx.Timeout(180, connect=10), follow_redirects=False
            ) as client:
                with client.stream(
                    "POST",
                    self.base + "/images/generations",
                    headers={"Authorization": f"Bearer {self.key}"},
                    json=payload,
                ) as response:
                    response.raise_for_status()
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        if time.monotonic() - started > 180:
                            raise GenerationError(
                                "Seedream 等待超时，平台可能已受理；请先检查平台调用记录，再决定是否重新生成"
                            )
                        body.extend(chunk)
                        if len(body) > MAX_RESPONSE:
                            raise GenerationError("图片响应超过大小限制；请检查平台调用记录")
            data = json.loads(body)["data"]
            if not isinstance(data, list) or len(data) != 1:
                raise ValueError("Expected one image")
            raw = base64.b64decode(data[0]["b64_json"], validate=True)
            if not raw or len(raw) > MAX_IMAGE:
                raise ValueError("Invalid image size")
            if raw.startswith(b"\x89PNG\r\n\x1a\n"):
                extension = "png"
            elif raw.startswith(b"\xff\xd8\xff"):
                extension = "jpg"
            else:
                raise ValueError("Unsupported image format")
            # Validate dimensions without trusting the MIME type supplied by the provider.
            with pymupdf.open(stream=raw, filetype=extension) as doc:
                if doc.page_count != 1 or doc[0].rect.is_empty:
                    raise ValueError("Invalid image")
            return raw, extension
        except httpx.HTTPStatusError as exc:
            messages = {
                401: "Seedream 密钥无效，请检查后端 SEEDREAM_API_KEY",
                403: "Seedream 暂无访问权限，请在方舟控制台开通该模型并检查密钥权限",
                404: "Seedream 模型或接口不存在，请核对模型 ID、服务地址和开通状态",
                429: "Seedream 配额或调用频率受限，请检查平台额度与限流后再试",
            }
            raise GenerationError(
                messages.get(
                    exc.response.status_code,
                    "Seedream 调用失败，请检查平台调用记录、余额及提示词；系统没有自动重试",
                )
            ) from None
        except httpx.TimeoutException:
            raise GenerationError(
                "Seedream 等待超时，平台可能已受理；请先检查平台调用记录，再决定是否重新生成"
            ) from None
        except GenerationError:
            raise
        except (
            httpx.HTTPError,
            ValueError,
            TypeError,
            KeyError,
            IndexError,
            binascii.Error,
            RuntimeError,
        ):
            raise GenerationError(
                "Seedream 未返回有效图片，请检查平台调用记录与网络；系统没有自动重试"
            ) from None


class ImageService:
    def __init__(self, data_dir: Path, provider=None):
        self.directory = Path(data_dir) / "images"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.provider = provider if provider is not None else Seedream()
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.directory / "jobs.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS drafts (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, prompt TEXT NOT NULL,
                evidence TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, request_id TEXT NOT NULL,
                prompt TEXT NOT NULL, draft_id TEXT, evidence TEXT NOT NULL, model TEXT NOT NULL,
                status TEXT NOT NULL, error TEXT, extension TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(owner, request_id)
            );
            CREATE INDEX IF NOT EXISTS jobs_owner_time ON jobs(owner, created_at DESC);
        """)
        with self.db:
            self.db.execute(
                "UPDATE jobs SET status='failed', error=?, updated_at=? WHERE status IN ('queued','running')",
                (
                    "后端在任务完成前停止。平台可能已受理，请检查平台调用记录后再决定是否重新生成",
                    now(),
                ),
            )
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="seedream")
        self.closed = False

    def config(self):
        return {
            "enabled": self.provider.enabled,
            "model": self.provider.model,
            "size": "2K",
            "watermark": True,
        }

    def draft(self, kb, generator, owner, question, top_k, document_ids):
        evidence = sources_from_chunks(kb.retrieve(owner, question, top_k, document_ids))
        if not evidence:
            raise ValueError("所选范围没有可检索文本，请上传论文或调整选择；也可以直接输入绘图描述")
        prompt = generator.image_prompt(question, evidence)
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 6000:
            raise GenerationError("DeepSeek 未返回有效绘图描述，请缩小范围后重试")
        draft_id = str(uuid4())
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO drafts VALUES (?,?,?,?,?)",
                (draft_id, owner, prompt.strip(), json.dumps(evidence, ensure_ascii=False), now()),
            )
            # Bounded draft history. Existing jobs retain their own evidence snapshot.
            self.db.execute(
                "DELETE FROM drafts WHERE owner=? AND id NOT IN "
                "(SELECT id FROM drafts WHERE owner=? ORDER BY created_at DESC LIMIT 50)",
                (owner, owner),
            )
        return {"draft_id": draft_id, "prompt": prompt.strip(), "evidence": evidence}

    @staticmethod
    def public(row):
        result = {
            key: row[key]
            for key in (
                "id",
                "request_id",
                "prompt",
                "draft_id",
                "model",
                "status",
                "error",
                "created_at",
                "updated_at",
            )
        }
        result.update(
            evidence=json.loads(row["evidence"]),
            size="2K",
            file_url=f"/api/images/{row['id']}/file" if row["status"] == "succeeded" else None,
        )
        return result

    def _row(self, owner, job_id):
        row = self.db.execute(
            "SELECT * FROM jobs WHERE id=? AND owner=?", (job_id, owner)
        ).fetchone()
        if row is None:
            raise KeyError(job_id)
        return row

    def get(self, owner, job_id):
        with self.lock:
            return self.public(self._row(owner, job_id))

    def list(self, owner):
        with self.lock:
            return [
                self.public(row)
                for row in self.db.execute(
                    "SELECT * FROM jobs WHERE owner=? ORDER BY created_at DESC LIMIT 30", (owner,)
                )
            ]

    def submit(self, owner, request_id, prompt, draft_id=None):
        with self.lock:
            existing = self.db.execute(
                "SELECT * FROM jobs WHERE owner=? AND request_id=?", (owner, request_id)
            ).fetchone()
            if existing:
                if existing["prompt"] != prompt or existing["draft_id"] != draft_id:
                    raise ImageConflict("该请求编号已用于另一份绘图描述，请刷新任务列表")
                return self.public(existing)
            if self.closed or not self.provider.enabled:
                raise ValueError(
                    "AI 配图尚未配置，请填写后端 SEEDREAM_API_KEY 等配置并重启；论文问答仍可正常使用"
                )
            evidence = "[]"
            if draft_id:
                draft = self.db.execute(
                    "SELECT evidence FROM drafts WHERE id=? AND owner=?", (draft_id, owner)
                ).fetchone()
                if draft is None:
                    raise KeyError(draft_id)
                evidence = draft["evidence"]
            if self.db.execute(
                "SELECT 1 FROM jobs WHERE owner=? AND status IN ('queued','running')", (owner,)
            ).fetchone():
                raise ImageBusy("你已有一张图片正在生成，请先等待或刷新任务列表")
            pending = self.db.execute(
                "SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')"
            ).fetchone()[0]
            if pending >= 8:
                raise ImageBusy("配图队列已满，请稍后再试")
            job_id = str(uuid4())
            timestamp = now()
            with self.db:
                self.db.execute(
                    "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,'queued',NULL,NULL,?,?)",
                    (
                        job_id,
                        owner,
                        request_id,
                        prompt,
                        draft_id,
                        evidence,
                        self.provider.model,
                        timestamp,
                        timestamp,
                    ),
                )
            try:
                self.executor.submit(self._work, job_id, prompt)
            except RuntimeError:
                self._update(job_id, "failed", "后端正在关闭，请稍后刷新任务列表")
            return self.public(self._row(owner, job_id))

    def _update(self, job_id, status, error=None, extension=None):
        with self.lock, self.db:
            self.db.execute(
                "UPDATE jobs SET status=?, error=?, extension=?, updated_at=? WHERE id=?",
                (status, error, extension, now(), job_id),
            )

    def _work(self, job_id, prompt):
        self._update(job_id, "running")
        temporary = self.directory / f"{job_id}.tmp"
        try:
            raw, extension = self.provider(prompt)
            if extension not in {"jpg", "png"}:
                raise GenerationError("图片格式不受支持")
            temporary.write_bytes(raw)
            temporary.replace(self.directory / f"{job_id}.{extension}")
            self._update(job_id, "succeeded", extension=extension)
        except GenerationError as exc:
            self._update(job_id, "failed", str(exc))
        except Exception:
            # No provider payloads, prompts or credentials in browser errors or logs.
            self._update(
                job_id,
                "failed",
                "图片处理或保存失败，请检查磁盘空间及平台调用记录；系统没有自动重试",
            )
        finally:
            temporary.unlink(missing_ok=True)

    def file(self, owner, job_id):
        with self.lock:
            row = self._row(owner, job_id)
            if row["status"] != "succeeded":
                raise ImageConflict("图片尚未生成成功")
            path = self.directory / f"{job_id}.{row['extension']}"
            if not path.is_file():
                raise KeyError(job_id)
            return path, "image/png" if row["extension"] == "png" else "image/jpeg"

    def close(self):
        with self.lock:
            self.closed = True
        self.executor.shutdown(wait=True)
        with self.lock:
            self.db.close()
