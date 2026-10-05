# YouTube Tutorial → PDF

Paste a YouTube tutorial link, drag to select the screen region you care about,
and get a PDF with a cropped screenshot + transcript text for each step.

## Setup
Requires Python 3.10+ and `ffmpeg` (with `ffprobe`) on your PATH.

```bash
pip install -r requirements.txt
python -m tutorial_tool.app          # opens on http://127.0.0.1:5000
```

## Use
1. Paste the link, click **Load video** (downloads ≤720p video + fetches captions).
2. Scrub the slider, then drag on the preview to select the region to capture
   (no selection = full frame).
3. Choose how often a new step starts (default 30s), click **Generate PDF**.

Each step = frame at that moment (cropped to your region) + the transcript text
for that time window. Near-identical consecutive screenshots are skipped
(text only) unless you untick the option.

## Notes
- The video must have captions (manual or auto). Without them you get a
  screenshots-only PDF. Language code box defaults to `en`.
- Built-in PDF fonts only cover Latin text.
- Only download videos you have the right to use.
- Offline/testing: POST `/api/prepare` with `{"video_path": "...", "subtitle_path": "x.srt"}`.
