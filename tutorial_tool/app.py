"""Local web UI: paste a YouTube link, drag a region, get a tutorial PDF."""
import re
import threading
import uuid
from pathlib import Path

from flask import Flask, jsonify, request, send_file

from . import core

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "workdir"
app = Flask(__name__, static_folder="static", static_url_path="")
JOBS: dict[str, dict] = {}


def _prepare(job_id, url, lang, video_path, subtitle_path):
    job = JOBS[job_id]
    try:
        d = WORK / job_id
        d.mkdir(parents=True, exist_ok=True)
        if video_path:
            job.update(message="Reading local video…")
            video, title = Path(video_path), Path(video_path).stem
            segments = core.load_subtitle_file(Path(subtitle_path)) if subtitle_path else []
        else:
            vid = core.parse_video_id(url)
            job.update(message="Fetching transcript…")
            segments = core.fetch_transcript(vid, [lang, "en"])
            job.update(message="Downloading video (this can take a minute)…")
            video, title = core.download_video(url, d)
        job.update(video=str(video), title=title, segments=segments, source=url,
                   duration=core.video_duration(video), state="ready", message="Ready")
    except Exception as e:  # surfaced to the UI
        job.update(state="error", message=f"{type(e).__name__}: {e}")


@app.get("/")
def index():
    return app.send_static_file("index.html")


@app.post("/api/prepare")
def prepare():
    data = request.get_json(force=True)
    url = (data.get("url") or "").strip()
    if not url and not data.get("video_path"):
        return jsonify(error="Paste a YouTube link"), 400
    job_id = uuid.uuid4().hex[:10]
    JOBS[job_id] = {"state": "working", "message": "Starting…"}
    threading.Thread(
        target=_prepare,
        args=(job_id, url, data.get("lang") or "en", data.get("video_path"), data.get("subtitle_path")),
        daemon=True,
    ).start()
    return jsonify(job=job_id)


@app.get("/api/job/<job_id>")
def job_status(job_id):
    j = JOBS.get(job_id)
    if not j:
        return jsonify(error="unknown job"), 404
    return jsonify({k: j.get(k) for k in ("state", "message", "title", "duration")} |
                   {"segments": len(j.get("segments") or [])})


@app.get("/api/frame/<job_id>")
def frame(job_id):
    j = JOBS.get(job_id)
    if not j or j["state"] != "ready":
        return "not ready", 404
    t = float(request.args.get("t", 0))
    out = WORK / job_id / "preview.jpg"
    core.extract_frame(Path(j["video"]), min(t, max(j["duration"] - 0.5, 0)), out)
    return send_file(out, mimetype="image/jpeg", max_age=0)


@app.post("/api/generate/<job_id>")
def generate(job_id):
    j = JOBS.get(job_id)
    if not j or j["state"] != "ready":
        return jsonify(error="job not ready"), 400
    data = request.get_json(force=True)
    r = data.get("region")
    region = None
    if r:
        x, y, w, h = (min(max(float(r[k]), 0.0), 1.0) for k in ("x", "y", "w", "h"))
        if w < 0.01 or h < 0.01:
            return jsonify(error="Selected region is too small"), 400
        region = (x, y, min(w, 1 - x), min(h, 1 - y))
    interval = max(float(data.get("interval") or 30), 5)
    segments = j["segments"]
    if not segments:  # no transcript: still produce screenshot-only steps
        segments = [{"start": t, "duration": interval, "text": ""}
                    for t in range(0, int(j["duration"]), int(interval))]
    steps = core.group_transcript(segments, interval) or core.group_transcript(
        [{"start": 0, "duration": j["duration"], "text": "(no transcript)"}], interval)
    out = WORK / job_id / "tutorial.pdf"
    core.build_pdf(j["title"], j["source"], Path(j["video"]), steps, region, WORK / job_id, out,
                   skip_duplicates=bool(data.get("skip_duplicates", True)))
    name = re.sub(r"[^\w\- ]+", "", j["title"]).strip()[:60] or "tutorial"
    return send_file(out, mimetype="application/pdf", as_attachment=True, download_name=f"{name}.pdf")


def main():
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=5000)
    args = p.parse_args()
    print(f"Open http://127.0.0.1:{args.port}")
    app.run(host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
