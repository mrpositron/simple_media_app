import argparse
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Literal

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from . import processing
from .processing import ProcessingError

STATIC_DIR = Path(__file__).parent / "static"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
IMAGE_MEDIA_TYPES = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
VIDEO_MEDIA_TYPES = {
    "mp4": "video/mp4", "mov": "video/quicktime", "mkv": "video/x-matroska", "webm": "video/webm",
}

app = FastAPI(title="Simple Media App")


def _process(
    upload: UploadFile,
    out_suffix: str,
    out_tag: str,
    media_type: str,
    work: Callable[[Path, Path], None],
) -> FileResponse:
    """Save the upload to a temp dir, run `work(src, dst)`, and stream the result back."""
    tmp = Path(tempfile.mkdtemp(prefix="media-app-"))
    cleanup = BackgroundTask(shutil.rmtree, tmp, ignore_errors=True)
    try:
        name = Path(upload.filename or "upload")
        src = tmp / f"input{name.suffix.lower()}"
        with src.open("wb") as f:
            shutil.copyfileobj(upload.file, f)
        dst = tmp / f"{name.stem}_{out_tag}{out_suffix}"
        work(src, dst)
    except ValueError as e:
        shutil.rmtree(tmp, ignore_errors=True)
        raise HTTPException(400, str(e)) from e
    except ProcessingError as e:
        shutil.rmtree(tmp, ignore_errors=True)
        raise HTTPException(500, str(e)) from e
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise

    return FileResponse(
        dst,
        media_type=media_type,
        filename=dst.name,
        background=cleanup,
        headers={
            "X-Original-Size": str(src.stat().st_size),
            "X-Result-Size": str(dst.stat().st_size),
            "Access-Control-Expose-Headers": "X-Original-Size, X-Result-Size",
        },
    )


def _video(
    upload: UploadFile, tag: str, work: Callable[[Path, Path], None], format: str = "mp4"
) -> FileResponse:
    return _process(upload, f".{format}", tag, VIDEO_MEDIA_TYPES[format], work)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/image/compress")
def image_compress(
    file: Annotated[UploadFile, File()],
    quality: Annotated[int, Form(ge=1, le=100)] = 75,
    max_width: Annotated[int | None, Form(ge=1)] = None,
    format: Annotated[Literal["same", "jpg", "webp", "png"], Form()] = "same",
) -> FileResponse:
    in_suffix = Path(file.filename or "").suffix.lower()
    if in_suffix not in IMAGE_SUFFIXES:
        raise HTTPException(400, f"Unsupported image type: {in_suffix or 'unknown'}")
    if format == "same":
        out_suffix = ".jpg" if in_suffix in (".jpeg", ".jpg") else in_suffix
        # Formats Pillow can read but that we don't write fall back to JPEG.
        if out_suffix not in IMAGE_MEDIA_TYPES:
            out_suffix = ".jpg"
    else:
        out_suffix = f".{format}"
    return _process(
        file, out_suffix, "compressed", IMAGE_MEDIA_TYPES[out_suffix],
        lambda src, dst: processing.compress_image(src, dst, quality, max_width),
    )


@app.post("/api/video/compress")
def video_compress(
    file: Annotated[UploadFile, File()],
    crf: Annotated[int, Form(ge=18, le=40)] = 28,
    max_height: Annotated[int | None, Form(ge=144)] = None,
) -> FileResponse:
    return _video(
        file, "compressed", lambda src, dst: processing.compress_video(src, dst, crf, max_height)
    )


@app.post("/api/video/speed")
def video_speed(
    file: Annotated[UploadFile, File()],
    factor: Annotated[float, Form(ge=processing.MIN_SPEED, le=processing.MAX_SPEED)] = 2.0,
) -> FileResponse:
    return _video(
        file, f"{factor:g}x", lambda src, dst: processing.speed_video(src, dst, factor)
    )


@app.post("/api/video/trim")
def video_trim(
    file: Annotated[UploadFile, File()],
    start: Annotated[str | None, Form()] = None,
    end: Annotated[str | None, Form()] = None,
    precise: Annotated[bool, Form()] = True,
    factor: Annotated[float, Form(ge=processing.MIN_SPEED, le=processing.MAX_SPEED)] = 1.0,
    format: Annotated[Literal["mp4", "mov", "mkv", "webm"], Form()] = "mp4",
) -> FileResponse:
    """Trim, change speed, and/or convert. Without start/end the whole video is kept."""
    try:
        start_s = processing.parse_time(start) if start else 0.0
        end_s = processing.parse_time(end) if end else None
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    parts = []
    if start or end:
        parts.append("trimmed")
    if factor != 1:
        parts.append(f"{factor:g}x")
    tag = "_".join(parts) or "converted"
    return _video(
        file, tag,
        lambda src, dst: processing.trim_video(src, dst, start_s, end_s, precise, factor),
        format,
    )


def run() -> None:
    parser = argparse.ArgumentParser(description="Run the Simple Media App web server.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)
