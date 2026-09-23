# Simple Media App

A small local web app for everyday media tasks:

- **Compress images:** JPEG, PNG, WebP. Optionally resize or convert to another format.
- **Compress videos:** H.264 at a chosen quality, with an optional maximum resolution.
- **Trim and change speed in one step:** pick the part to keep by dragging handles on a
  timeline, and optionally speed it up or slow it down (0.25× to 4×, audio pitch kept).
  Cuts are frame-accurate. At 1× there is also a fast mode that does not re-encode.
- **Convert videos:** the same step saves as MP4, MOV, MKV (H.264 + AAC) or WebM (VP9 + Opus).
  Keep the whole video at 1× to only convert it.

Every video operation accepts .mov, .mp4, or any other format ffmpeg can read.
Compress saves .mp4; trim, speed & convert saves the format you pick.

All processing runs on your machine with ffmpeg and Pillow. Files are never uploaded anywhere else.

## Setup

Requirements: [uv](https://docs.astral.sh/uv/) and ffmpeg.

```sh
brew install ffmpeg
uv sync
```

## Run

```sh
uv run media-app
```

Then open http://127.0.0.1:8765. Use `--port` to pick a different port.

## Test

```sh
uv run pytest
```
