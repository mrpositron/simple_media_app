import pytest
from fastapi.testclient import TestClient

from simple_media_app.main import app

client = TestClient(app)


def upload(path, content_type="application/octet-stream"):
    return {"file": (path.name, path.read_bytes(), content_type)}


def test_index():
    res = client.get("/")
    assert res.status_code == 200
    assert "Simple Media App" in res.text


def test_image_compress(jpeg):
    res = client.post("/api/image/compress", files=upload(jpeg), data={"quality": "60"})
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/jpeg"
    assert "photo_compressed.jpg" in res.headers["content-disposition"]
    assert int(res.headers["x-result-size"]) < int(res.headers["x-original-size"])


def test_image_compress_to_webp(jpeg):
    res = client.post("/api/image/compress", files=upload(jpeg), data={"format": "webp"})
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/webp"


def test_image_compress_png(png):
    res = client.post("/api/image/compress", files=upload(png), data={"quality": "75"})
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/png"
    assert int(res.headers["x-result-size"]) < int(res.headers["x-original-size"])


def test_image_unsupported_type(mov):
    res = client.post("/api/image/compress", files=upload(mov))
    assert res.status_code == 400


@pytest.mark.parametrize("url, data, name", [
    ("/api/video/compress", {"crf": "30"}, "clip_compressed.mp4"),
    ("/api/video/speed", {"factor": "2"}, "clip_2x.mp4"),
    ("/api/video/trim", {"start": "1", "end": "00:00:03"}, "clip_trimmed.mp4"),
    ("/api/video/trim", {"start": "1", "end": "3", "precise": "false"}, "clip_trimmed.mp4"),
    ("/api/video/trim", {"start": "1", "end": "3", "factor": "2"}, "clip_trimmed_2x.mp4"),
    ("/api/video/trim", {"start": "1", "end": "3", "factor": "1"}, "clip_trimmed.mp4"),
])
def test_video_endpoints_return_mp4(mov, url, data, name):
    res = client.post(url, files=upload(mov), data=data)
    assert res.status_code == 200, res.text
    assert res.headers["content-type"] == "video/mp4"
    assert name in res.headers["content-disposition"]


@pytest.mark.parametrize("data, content_type, name", [
    ({"format": "webm"}, "video/webm", "clip_converted.webm"),
    ({"format": "mov", "start": "1", "end": "3"}, "video/quicktime", "clip_trimmed.mov"),
    ({"format": "mkv", "factor": "2"}, "video/x-matroska", "clip_2x.mkv"),
    ({}, "video/mp4", "clip_converted.mp4"),
])
def test_video_convert(mov, data, content_type, name):
    res = client.post("/api/video/trim", files=upload(mov), data=data)
    assert res.status_code == 200, res.text
    assert res.headers["content-type"] == content_type
    assert name in res.headers["content-disposition"]


@pytest.mark.parametrize("url, data", [
    ("/api/video/trim", {"start": "3", "end": "1"}),
    ("/api/video/trim", {"start": "abc", "end": "1"}),
])
def test_video_bad_params_400(mov, url, data):
    res = client.post(url, files=upload(mov), data=data)
    assert res.status_code == 400


@pytest.mark.parametrize("url, data", [
    ("/api/video/speed", {"factor": "10"}),
    ("/api/video/trim", {"start": "1", "end": "3", "factor": "10"}),
    ("/api/video/trim", {"format": "avi"}),
    ("/api/video/compress", {"crf": "99"}),
])
def test_video_out_of_range_422(mov, url, data):
    res = client.post(url, files=upload(mov), data=data)
    assert res.status_code == 422


def test_video_garbage_file_500(tmp_path):
    bad = tmp_path / "bad.mov"
    bad.write_bytes(b"not a video")
    res = client.post("/api/video/compress", files=upload(bad))
    assert res.status_code == 500
    assert "ffprobe failed" in res.json()["detail"]
