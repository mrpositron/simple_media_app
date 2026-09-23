import io
import subprocess

import pytest
from PIL import Image, ImageChops, ImageStat

from simple_media_app import processing
from simple_media_app.processing import ProcessingError, probe


def assert_video(path, duration, vcodec="h264", acodec: str | None = "aac", tol=0.25):
    info = probe(path)
    assert info.video_codec == vcodec
    assert info.audio_codec == acodec
    assert info.duration == pytest.approx(duration, abs=tol)


def assert_mp4(path, duration, audio=True, tol=0.25):
    assert_video(path, duration, acodec="aac" if audio else None, tol=tol)


def test_compress_image_smaller(jpeg, tmp_path):
    dst = tmp_path / "out.jpg"
    processing.compress_image(jpeg, dst, quality=60)
    assert dst.stat().st_size < jpeg.stat().st_size


def test_compress_image_resize_and_convert(jpeg, tmp_path):
    dst = tmp_path / "out.webp"
    processing.compress_image(jpeg, dst, quality=70, max_width=400)
    with Image.open(dst) as img:
        assert img.format == "WEBP"
        assert img.size == (400, 300)


def test_compress_png_lossy(png, tmp_path):
    dst = tmp_path / "out.png"
    processing.compress_image(png, dst, quality=75)
    assert dst.stat().st_size < 0.6 * png.stat().st_size
    with Image.open(dst) as img:
        assert img.format == "PNG"
        assert img.convert("RGBA").getpixel((0, 0))[3] == 0
        assert img.convert("RGBA").getpixel((300, 200))[3] == 255


def test_compress_png_rgb_lossy(png, tmp_path):
    rgb = tmp_path / "rgb.png"
    with Image.open(png) as img:
        img.convert("RGB").save(rgb)
    dst = tmp_path / "out.png"
    processing.compress_image(rgb, dst, quality=75)
    assert dst.stat().st_size < 0.6 * rgb.stat().st_size


def test_compress_png_lossless_at_high_quality(png, tmp_path):
    dst = tmp_path / "out.png"
    processing.compress_image(png, dst, quality=100)
    with Image.open(png) as a, Image.open(dst) as b:
        assert a.convert("RGBA").tobytes() == b.convert("RGBA").tobytes()


def test_compress_png_never_grows(tmp_path):
    src = tmp_path / "tiny.png"
    Image.new("P", (64, 64), 3).save(src, optimize=True)
    dst = tmp_path / "out.png"
    processing.compress_image(src, dst, quality=50)
    assert dst.stat().st_size <= src.stat().st_size


def test_compress_video_mov_to_mp4(mov, tmp_path):
    dst = tmp_path / "out.mp4"
    processing.compress_video(mov, dst, crf=30)
    assert_mp4(dst, 4)


def test_compress_video_max_height(mov, tmp_path):
    dst = tmp_path / "out.mp4"
    processing.compress_video(mov, dst, max_height=120)
    assert_mp4(dst, 4)


def test_compress_video_without_audio(silent_mov, tmp_path):
    dst = tmp_path / "out.mp4"
    processing.compress_video(silent_mov, dst)
    assert_mp4(dst, 4, audio=False)


@pytest.mark.parametrize("factor, duration", [(2, 2), (4, 1), (0.5, 8), (1.5, 4 / 1.5)])
def test_speed_video(mov, tmp_path, factor, duration):
    dst = tmp_path / "out.mp4"
    processing.speed_video(mov, dst, factor)
    assert_mp4(dst, duration)


def test_speed_video_without_audio(silent_mov, tmp_path):
    dst = tmp_path / "out.mp4"
    processing.speed_video(silent_mov, dst, 2)
    assert_mp4(dst, 2, audio=False)


def test_speed_out_of_range(mov, tmp_path):
    with pytest.raises(ValueError):
        processing.speed_video(mov, tmp_path / "out.mp4", 10)


def test_trim_precise(mov, tmp_path):
    dst = tmp_path / "out.mp4"
    processing.trim_video(mov, dst, 1, 3, precise=True)
    assert_mp4(dst, 2, tol=0.1)


def test_trim_fast_copies_into_mp4(mov, tmp_path):
    dst = tmp_path / "out.mp4"
    processing.trim_video(mov, dst, 1, 3, precise=False)
    # Stream copy snaps to keyframes (every 1 s in the fixture), so allow slack.
    assert_mp4(dst, 2, tol=1.0)


def test_trim_fast_falls_back_for_non_mp4_codecs(prores_mov, tmp_path):
    dst = tmp_path / "out.mp4"
    processing.trim_video(prores_mov, dst, 1, 3, precise=False)
    assert_mp4(dst, 2, tol=0.1)


@pytest.mark.parametrize("factor, duration", [(2, 1), (0.5, 4)])
def test_trim_and_speed(mov, tmp_path, factor, duration):
    dst = tmp_path / "out.mp4"
    processing.trim_video(mov, dst, 1, 3, factor=factor)
    assert_mp4(dst, duration, tol=0.15)


def test_trim_and_speed_without_audio(silent_mov, tmp_path):
    dst = tmp_path / "out.mp4"
    processing.trim_video(silent_mov, dst, 1, 3, factor=2)
    assert_mp4(dst, 1, audio=False, tol=0.15)


def test_trim_and_speed_ignores_fast_mode(mov, tmp_path):
    dst = tmp_path / "out.mp4"
    processing.trim_video(mov, dst, 1, 3, precise=False, factor=2)
    assert_mp4(dst, 1, tol=0.15)


def test_trim_speed_out_of_range(mov, tmp_path):
    with pytest.raises(ValueError):
        processing.trim_video(mov, tmp_path / "out.mp4", 1, 3, factor=10)


@pytest.mark.parametrize("fmt, vcodec, acodec", [
    ("mp4", "h264", "aac"), ("mov", "h264", "aac"), ("mkv", "h264", "aac"), ("webm", "vp9", "opus"),
])
def test_trim_converts_to_format(mov, tmp_path, fmt, vcodec, acodec):
    dst = tmp_path / f"out.{fmt}"
    processing.trim_video(mov, dst, 1, 3)
    assert_video(dst, 2, vcodec, acodec, tol=0.1)


def test_convert_whole_video(prores_mov, tmp_path):
    dst = tmp_path / "out.mkv"
    processing.trim_video(prores_mov, dst)
    assert_video(dst, 4, tol=0.1)


def test_convert_trim_and_speed_to_webm(mov, tmp_path):
    dst = tmp_path / "out.webm"
    processing.trim_video(mov, dst, 1, 3, factor=2)
    assert_video(dst, 1, "vp9", "opus", tol=0.15)


def test_convert_webm_without_audio(silent_mov, tmp_path):
    dst = tmp_path / "out.webm"
    processing.trim_video(silent_mov, dst, 1, 3)
    assert_video(dst, 2, "vp9", None, tol=0.1)


def test_fast_convert_copies_when_codecs_fit(mov, tmp_path):
    dst = tmp_path / "out.mkv"
    processing.trim_video(mov, dst, precise=False)
    assert_video(dst, 4, tol=0.1)


def test_fast_convert_reencodes_when_codecs_dont_fit(mov, tmp_path):
    dst = tmp_path / "out.webm"
    processing.trim_video(mov, dst, 1, 3, precise=False)
    assert_video(dst, 2, "vp9", "opus", tol=0.1)


def test_convert_unsupported_format(mov, tmp_path):
    with pytest.raises(ValueError):
        processing.trim_video(mov, tmp_path / "out.avi")


def first_video_pts(path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time",
         "-of", "csv=p=0", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout
    return min(float(t) for t in out.split())


def frame_at(path, t: float) -> Image.Image:
    png = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1",
         "-f", "image2pipe", "-c:v", "png", "-"],
        check=True, capture_output=True,
    ).stdout
    return Image.open(io.BytesIO(png)).convert("RGB")


def frame_diff(a: Image.Image, b: Image.Image) -> float:
    return sum(ImageStat.Stat(ImageChops.difference(a, b)).mean)


@pytest.mark.parametrize("fmt", ["mp4", "webm"])
@pytest.mark.parametrize("factor", [1, 2])
def test_trim_vfr_starts_with_frame_on_screen(screen_mov, tmp_path, fmt, factor):
    # The source has frames at 1.0 s and 1.5 s, so at 1.2 s the 1.0 s frame is on screen.
    # Dropping it (as an exact seek does) leaves a black gap until the 1.5 s frame.
    dst = tmp_path / f"out.{fmt}"
    processing.trim_video(screen_mov, dst, 1.2, 3.2, factor=factor)
    assert first_video_pts(dst) < 0.05
    assert probe(dst).duration == pytest.approx(2 / factor, abs=0.1)
    first = frame_at(dst, 0)
    assert frame_diff(first, frame_at(screen_mov, 1.0)) < frame_diff(first, frame_at(screen_mov, 1.5))


def test_trim_invalid_range(mov, tmp_path):
    with pytest.raises(ValueError):
        processing.trim_video(mov, tmp_path / "out.mp4", 3, 1)
    with pytest.raises(ValueError):
        processing.trim_video(mov, tmp_path / "out.mp4", 10, 12)


@pytest.mark.parametrize("value, seconds", [
    ("12.5", 12.5), ("01:30", 90), ("00:01:02.25", 62.25), ("1:00:00", 3600),
])
def test_parse_time(value, seconds):
    assert processing.parse_time(value) == seconds


@pytest.mark.parametrize("value", ["abc", "", "-5", "1:xx"])
def test_parse_time_invalid(value):
    with pytest.raises(ValueError):
        processing.parse_time(value)


def test_probe_rejects_non_video(jpeg, tmp_path):
    txt = tmp_path / "not_video.txt"
    txt.write_text("hello")
    with pytest.raises(ProcessingError):
        probe(txt)
