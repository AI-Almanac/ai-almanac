import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .routers import chat, config, datasets, jobs, regions
from .services.storage import get_storage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create local directories only when running with the local storage backend.
    storage = get_storage()
    if storage.is_local:
        Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
        Path(settings.job_outputs_dir).mkdir(parents=True, exist_ok=True)

    yield


app = FastAPI(title="AI Almanac API", lifespan=lifespan)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "%s %s %d %.1fms",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.cors_allow_all else [settings.frontend_url],
    allow_credentials=not settings.cors_allow_all,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router)
app.include_router(config.router)
app.include_router(datasets.router)
app.include_router(jobs.router)
app.include_router(regions.router)


@app.get("/health")
async def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Local file upload endpoint
#
# Only registered when STORAGE_BACKEND=local. In production, clients upload
# directly to GCS via signed URLs — this endpoint is never used.
# ---------------------------------------------------------------------------

storage = get_storage()
if storage.is_local:

    @app.put("/upload/{storage_key:path}", status_code=status.HTTP_200_OK)
    async def local_upload(storage_key: str, request: Request):
        dest = Path(settings.upload_dir) / storage_key
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("wb") as f:
            async for chunk in request.stream():
                f.write(chunk)
        return {"stored": str(dest)}
