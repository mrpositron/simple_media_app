import subprocess
from pathlib import Path

import pytest
from PIL import Image


@pytest.fixture(scope="session")
def media_dir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("media")


@pytest.fixture(scope="session")
def jpeg(media_dir) -> Path:
    path = media_dir / "photo.jpg"
    Image.effect_noise((800, 600), 64).convert("RGB").save(path, quality=100)
    return path


@pytest.fixture(scope="session")
def png(media_dir) -> Path:
    """A smooth RGBA gradient (many colors) with a fully transparent corner."""
    path = media_dir / "graphic.png"
    w, h = 600, 400
    img = Image.new("RGBA", (w, h))
    img.putdata([
        (x * 255 // w, y * 255 // h, (x + y) * 255 // (w + h), 0 if x < 50 and y < 50 else 255)
        for y in range(h) for x in range(w)
    ])
    img.save(path)
    return path


def _make_video(path: Path, *codec_args: str, audio: bool = True) -> Path:
    inputs = ["-f", "lavfi", "-i", "testsrc=duration=4:size=320x240:rate=30"]
    if audio:
        inputs += ["-f", "lavfi", "-i", "sine=frequency=440:duration=4"]
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", *inputs, *codec_args, "-shortest", str(path)],
        check=True,
    )
    return path


@pytest.fixture(scope="session")
def mov(media_dir) -> Path:
    """A 4 s H.264/AAC .mov, like a typical phone recording."""
    return _make_video(media_dir / "clip.mov", "-c:v", "libx264", "-g", "30", "-c:a", "aac")


@pytest.fixture(scope="session")
def silent_mov(media_dir) -> Path:
    return _make_video(media_dir / "silent.mov", "-c:v", "libx264", audio=False)


@pytest.fixture(scope="session")
def screen_mov(media_dir) -> Path:
    """Variable frame rate like a screen recording: a new frame only every 0.5 s."""
    return _make_video(
        media_dir / "screen.mov",
        "-vf", "select='not(mod(n,15))'", "-fps_mode", "vfr", "-c:v", "libx264", "-g", "8",
        "-c:a", "aac",
    )


@pytest.fixture(scope="session")
def prores_mov(media_dir) -> Path:
    """ProRes/PCM can't be stream-copied into mp4, so fast trim must re-encode."""
    return _make_video(media_dir / "prores.mov", "-c:v", "prores_ks", "-c:a", "pcm_s16le")
