"""Media operations. Images use Pillow; videos use ffmpeg.

Compress and speed produce .mp4; trim_video can also write .mov, .mkv, or .webm.
"""

import io
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

# Re-encode settings shared by every video operation: H.264 + AAC in a web-friendly mp4.
# yuv420p keeps 10-bit/HDR sources (e.g. iPhone .mov) playable everywhere.
_MP4_VIDEO = ["-c:v", "libx264", "-preset", "medium", "-pix_fmt", "yuv420p"]
_MP4_AUDIO = ["-c:a", "aac", "-b:a", "128k"]
_MP4_CONTAINER = ["-movflags", "+faststart"]

# Codecs that can be stream-copied into an mp4 container without re-encoding.
_MP4_COPY_VIDEO = {"h264", "hevc"}
_MP4_COPY_AUDIO = {"aac", "mp3", "alac"}


@dataclass(frozen=True)
class _Format:
    """How to encode into (or stream-copy into) one output container."""
    video: list[str]
    audio: list[str]
    container: list[str]
    copy_video: frozenset[str]
    copy_audio: frozenset[str]


_H264_HQ = [*_MP4_VIDEO, "-crf", "20"]

# Output formats for trim_video, keyed by file suffix.
VIDEO_FORMATS = {
    "mp4": _Format(
        _H264_HQ, _MP4_AUDIO, _MP4_CONTAINER,
        frozenset(_MP4_COPY_VIDEO), frozenset(_MP4_COPY_AUDIO),
    ),
    "mov": _Format(
        _H264_HQ, _MP4_AUDIO, _MP4_CONTAINER,
        frozenset({*_MP4_COPY_VIDEO, "prores"}),
        frozenset({*_MP4_COPY_AUDIO, "pcm_s16le", "pcm_s24le"}),
    ),
    "mkv": _Format(
        _H264_HQ, _MP4_AUDIO, [],
        frozenset({"h264", "hevc", "vp8", "vp9", "av1"}),
        frozenset({"aac", "mp3", "opus", "vorbis", "flac", "ac3", "alac"}),
    ),
    "webm": _Format(
        # -b:v 0 makes -crf a pure quality target; -cpu-used 4 keeps VP9 reasonably fast.
        ["-c:v", "libvpx-vp9", "-crf", "31", "-b:v", "0", "-row-mt", "1",
         "-deadline", "good", "-cpu-used", "4", "-pix_fmt", "yuv420p"],
        ["-c:a", "libopus", "-b:a", "128k"], [],
        frozenset({"vp8", "vp9", "av1"}), frozenset({"opus", "vorbis"}),
    ),
}

MIN_SPEED, MAX_SPEED = 0.25, 4.0


class ProcessingError(Exception):
    pass


@dataclass
class ProbeInfo:
    duration: float
    video_codec: str | None
    audio_codec: str | None
    frame_rate: float = 30.0

    @property
    def has_audio(self) -> bool:
        return self.audio_codec is not None


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError as e:
        raise ProcessingError(f"{cmd[0]} not found. Install it with `brew install ffmpeg`.") from e
    except subprocess.CalledProcessError as e:
        tail = "\n".join(e.stderr.strip().splitlines()[-8:])
        raise ProcessingError(f"{cmd[0]} failed:\n{tail}") from e


def _ffmpeg(*args: str | Path) -> None:
    _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *map(str, args)])


def probe(src: Path) -> ProbeInfo:
    out = _run([
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(src),
    ]).stdout
    data = json.loads(out)
    streams = data.get("streams", [])

    def first_codec(kind: str) -> str | None:
        return next((s["codec_name"] for s in streams if s.get("codec_type") == kind), None)

    video_codec = first_codec("video")
    if video_codec is None:
        raise ProcessingError("The file has no video stream.")
    video = next(s for s in streams if s.get("codec_type") == "video")
    return ProbeInfo(
        duration=float(data.get("format", {}).get("duration", 0.0)),
        video_codec=video_codec,
        audio_codec=first_codec("audio"),
        frame_rate=_frame_rate(video),
    )


def _frame_rate(stream: dict) -> float:
    # Variable frame rate files (e.g. screen recordings) can report odd values; fall back to 30.
    for key in ("r_frame_rate", "avg_frame_rate"):
        num, _, den = stream.get(key, "0/0").partition("/")
        try:
            rate = float(num) / float(den or 1)
        except (ValueError, ZeroDivisionError):
            continue
        if 1 <= rate <= 120:
            return rate
    return 30.0


def compress_image(
    src: Path,
    dst: Path,
    quality: int = 75,
    max_width: int | None = None,
) -> None:
    """Re-encode an image. The output format is taken from dst's suffix."""
    with Image.open(src) as img:
        img = ImageOps.exif_transpose(img)
        if max_width and img.width > max_width:
            height = round(img.height * max_width / img.width)
            img = img.resize((max_width, height), Image.Resampling.LANCZOS)

        fmt = dst.suffix.lower().lstrip(".")
        if fmt in ("jpg", "jpeg"):
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            img.save(dst, "JPEG", quality=quality, optimize=True, progressive=True)
        elif fmt == "webp":
            img.save(dst, "WEBP", quality=quality, method=6)
        elif fmt == "png":
            _save_png(img, dst, quality)
        else:
            raise ProcessingError(f"Unsupported image output format: {fmt}")


PNG_LOSSLESS_QUALITY = 95


def _save_png(img: Image.Image, dst: Path, quality: int) -> None:
    """PNG has no quality setting, so below PNG_LOSSLESS_QUALITY reduce to a palette (like pngquant)."""
    lossless = io.BytesIO()
    img.save(lossless, "PNG", optimize=True)
    best = lossless.getvalue()

    if quality < PNG_LOSSLESS_QUALITY:
        has_alpha = img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info
        src = img.convert("RGBA" if has_alpha else "RGB")
        colors = 256 if quality >= 50 else max(16, quality * 5)
        # FASTOCTREE is the built-in method that keeps an alpha channel.
        quantized = src.quantize(colors, method=Image.Quantize.FASTOCTREE)
        if not has_alpha:
            # Pillow only dithers when remapping RGB onto a given palette.
            quantized = src.quantize(palette=quantized, dither=Image.Dither.FLOYDSTEINBERG)
        lossy = io.BytesIO()
        quantized.save(lossy, "PNG", optimize=True)
        # Already-small or palette images can grow when quantized; keep the smaller one.
        if lossy.tell() < len(best):
            best = lossy.getvalue()

    dst.write_bytes(best)


def compress_video(src: Path, dst: Path, crf: int = 28, max_height: int | None = None) -> None:
    info = probe(src)
    # Scale down only; -2 keeps width even, which libx264 requires.
    vf = ["-vf", f"scale=-2:'min({max_height},ih)'"] if max_height else []
    audio = _MP4_AUDIO if info.has_audio else ["-an"]
    _ffmpeg("-i", src, *vf, *_MP4_VIDEO, "-crf", crf, *audio, *_MP4_CONTAINER, dst)


def _atempo_chain(factor: float) -> str:
    # A single atempo filter only accepts 0.5-2.0, so chain several for larger changes.
    parts = []
    while factor > 2.0:
        parts.append(2.0)
        factor /= 2.0
    while factor < 0.5:
        parts.append(0.5)
        factor /= 0.5
    parts.append(factor)
    return ",".join(f"atempo={p:.6g}" for p in parts)


def _check_speed(factor: float) -> None:
    if not MIN_SPEED <= factor <= MAX_SPEED:
        raise ValueError(f"Speed must be between {MIN_SPEED}x and {MAX_SPEED}x.")


def _speed_args(factor: float, has_audio: bool) -> list[str]:
    """Filter, map, and audio-codec args that retime the first video (and audio) stream."""
    if has_audio:
        return [
            "-filter_complex",
            f"[0:v]setpts=PTS/{factor}[v];[0:a]{_atempo_chain(factor)}[a]",
            "-map", "[v]", "-map", "[a]", *_MP4_AUDIO,
        ]
    return ["-vf", f"setpts=PTS/{factor}", "-an"]


def _cut_filters(info: ProbeInfo, factor: float, audio: list[str]) -> list[str]:
    """Filter, map, and audio-codec args for a cut that was seeked with -noaccurate_seek.

    Decoding starts at the keyframe before the cut, so frames before it have negative
    timestamps. fps=...:start_time=0 then holds whichever frame is on screen at the cut,
    even when it was written earlier (screen recordings only write frames on change).
    An exact seek drops that frame and leaves a black gap until the next one.
    """
    retime = [f"setpts=PTS/{factor}"] if factor != 1 else []
    # Speeding up packs frames closer together; raise the rate (up to 60) so neighbouring
    # frames don't round onto the same output tick, where the later one would win.
    rate = min(info.frame_rate * max(factor, 1), max(info.frame_rate, 60))
    video = ",".join([*retime, f"fps=fps={rate:.6g}:start_time=0"])
    if not info.has_audio:
        return ["-filter_complex", f"[0:v]{video}[v]", "-map", "[v]", "-an"]
    tempo = [_atempo_chain(factor)] if factor != 1 else []
    # async=1 pads with silence (rather than shifting) if the audio starts late, keeping sync.
    audio_chain = ",".join(["atrim=start=0", "aresample=async=1:first_pts=0", *tempo])
    return [
        "-filter_complex", f"[0:v]{video}[v];[0:a]{audio_chain}[a]",
        "-map", "[v]", "-map", "[a]", *audio,
    ]


def speed_video(src: Path, dst: Path, factor: float) -> None:
    _check_speed(factor)
    info = probe(src)
    speed = _speed_args(factor, info.has_audio)
    _ffmpeg("-i", src, *speed, *_MP4_VIDEO, "-crf", 23, *_MP4_CONTAINER, dst)


def parse_time(value: str) -> float:
    """Accept seconds ("12.5"), "MM:SS" or "HH:MM:SS(.ms)"."""
    try:
        seconds = 0.0
        for part in value.strip().split(":"):
            seconds = seconds * 60 + float(part)
    except ValueError:
        raise ValueError(f"Invalid time: {value!r}") from None
    if seconds < 0:
        raise ValueError(f"Invalid time: {value!r}")
    return seconds


def trim_video(
    src: Path,
    dst: Path,
    start: float = 0.0,
    end: float | None = None,
    precise: bool = True,
    factor: float = 1.0,
) -> None:
    """Cut [start, end], optionally change its speed, and convert to dst's format, in one encode.

    end=None keeps everything up to the end of the video.
    """
    suffix = dst.suffix.lower().lstrip(".")
    fmt = VIDEO_FORMATS.get(suffix)
    if fmt is None:
        raise ValueError(f"Unsupported video output format: {dst.suffix or 'none'}")
    _check_speed(factor)
    if end is not None and end <= start:
        raise ValueError("End time must be after start time.")
    info = probe(src)
    if start >= info.duration:
        raise ValueError(f"Start time is past the end of the video ({info.duration:.2f}s).")

    can_copy = info.video_codec in fmt.copy_video and (
        not info.has_audio or info.audio_codec in fmt.copy_audio
    )
    ss = ["-ss", f"{start:.3f}"]
    if not precise and can_copy and factor == 1:
        # Fast: no re-encode, but cuts snap to the nearest keyframe.
        to = ["-to", f"{end:.3f}"] if end is not None else []
        # Apple players only play HEVC in mp4/mov when it's tagged hvc1.
        hvc1 = info.video_codec == "hevc" and suffix in ("mp4", "mov")
        tag = ["-tag:v", "hvc1"] if hvc1 else []
        _ffmpeg(*ss, *to, "-i", src, "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", *tag,
                *fmt.container, dst)
    else:
        # Re-encode (always the case when retiming). Input -to would count from the keyframe
        # under -noaccurate_seek, so limit the length on the output side instead.
        length = ["-t", f"{(end - start) / factor:.3f}"] if end is not None else []
        _ffmpeg("-noaccurate_seek", *ss, "-i", src, *_cut_filters(info, factor, fmt.audio),
                *length, *fmt.video, *fmt.container, dst)
