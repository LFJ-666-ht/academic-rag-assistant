"""Start the cached-model backend from the existing project/venv."""

import os
from pathlib import Path


def main() -> None:
    # Preserve ./data even when launched with PyCharm's Run button or from a
    # different working directory. Never copy .env or recreate the database.
    project_root = Path(__file__).resolve().parent
    os.chdir(project_root)
    from dotenv import load_dotenv

    load_dotenv(project_root / ".env")
    # Override stale terminal flags only in this process and its children.
    # These flags affect Hugging Face, not the separate model-provider HTTP call.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    import uvicorn

    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, workers=1)


if __name__ == "__main__":
    main()
