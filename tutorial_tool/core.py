"""Core pipeline: download video, get transcript, crop frames, build PDF."""
import html
import re
import subprocess
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image, ImageChops, ImageStat

_ID_RE = re.compile(r"(?:v=|youtu\.be/|/embed/|/shorts/|/live/)([A-Za-z0-9_-]{11})")


def parse_video_id(url: str) -> str:
    m = _ID_RE.search(url)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", url.strip()):
        return url.strip()
    raise ValueError("Could not find a YouTube video id in that link")


# ---------------------------------------------------------------- download
def download_video(url: str, dest: Path) -> tuple[Path, str]:
    """Download a <=720p mp4 with yt-dlp. Returns (path, title)."""
    import yt_dlp

    dest.mkdir(parents=True, exist_ok=True)
    opts = {
        "format": "bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[height<=720][ext=mp4]/b[height<=720]/b",
        "merge_output_format": "mp4",
        "outtmpl": str(dest / "video.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    files = sorted(dest.glob("video.*"))
    if not files:
        raise RuntimeError("Video download produced no file")
    return files[0], info.get("title") or "Tutorial"


# -------------------------------------------------------------- transcript
def fetch_transcript(video_id: str, languages=("en",)) -> list[dict]:
    """Return [{start, duration, text}] from YouTube captions."""
    from youtube_transcript_api import YouTubeTranscriptApi

    langs = list(languages)
    if hasattr(YouTubeTranscriptApi, "get_transcript"):  # legacy API (<1.0)
        raw = YouTubeTranscriptApi.get_transcript(video_id, languages=langs)
        return [{"start": r["start"], "duration": r["duration"], "text": r["text"]} for r in raw]
    fetched = YouTubeTranscriptApi().fetch(video_id, languages=langs)
    return [{"start": s.start, "duration": s.duration, "text": s.text} for s in fetched]


def _ts(s: str) -> float:
    s = s.replace(",", ".")
    parts = [float(p) for p in s.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0.0)
    h, m, sec = parts
    return h * 3600 + m * 60 + sec


def load_subtitle_file(path: Path) -> list[dict]:
    """Parse an .srt/.vtt file (fallback when YouTube captions are unavailable)."""
    out = []
    text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [l for l in block.splitlines() if l.strip()]
        for i, line in enumerate(lines):
            m = re.match(r"([\d:.,]+)\s*-->\s*([\d:.,]+)", line)
            if m:
                start, end = _ts(m.group(1)), _ts(m.group(2))
                body = " ".join(re.sub(r"<[^>]+>", "", l) for l in lines[i + 1:])
                if body.strip():
                    out.append({"start": start, "duration": end - start, "text": body.strip()})
                break
    return out


def group_transcript(segments: list[dict], interval: float) -> list[dict]:
    """Merge caption lines into steps of roughly `interval` seconds."""
    steps, cur = [], None
    for seg in segments:
        text = html.unescape(seg["text"]).replace("\n", " ").strip()
        if not text:
            continue
        if cur is None or seg["start"] - cur["start"] >= interval:
            cur = {"start": seg["start"], "end": seg["start"] + seg["duration"], "text": text}
            steps.append(cur)
        else:
            cur["text"] += " " + text
            cur["end"] = seg["start"] + seg["duration"]
    return steps


# ------------------------------------------------------------------ frames
def video_duration(video: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(video)],
        capture_output=True, text=True, check=True,
    ).stdout
    return float(out.strip())


def extract_frame(video: Path, t: float, out: Path, region=None) -> Path:
    """Grab a frame at time t (seconds). region = (x, y, w, h) as 0-1 fractions."""
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{max(t, 0):.3f}", "-i", str(video),
         "-frames:v", "1", "-q:v", "2", str(out)],
        check=True,
    )
    if region:
        img = Image.open(out)
        W, H = img.size
        x, y, w, h = region
        box = (round(x * W), round(y * H), round((x + w) * W), round((y + h) * H))
        img.crop(box).save(out, quality=92)
    return out


def _similar(a: Path, b: Path, threshold=2.0) -> bool:
    ia = Image.open(a).convert("L").resize((64, 64))
    ib = Image.open(b).convert("L").resize((64, 64))
    return ImageStat.Stat(ImageChops.difference(ia, ib)).mean[0] < threshold


# --------------------------------------------------------------------- pdf
def _fmt(t: float) -> str:
    t = int(t)
    return f"{t // 3600:d}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60:d}:{t % 60:02d}"


def build_pdf(title, source_url, video, steps, region, workdir: Path, out_pdf: Path, skip_duplicates=True):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Image as RLImage
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer

    styles = getSampleStyleSheet()
    step_style = ParagraphStyle("Step", parent=styles["Heading3"], textColor="#1a56db", spaceBefore=14)
    body = ParagraphStyle("Body", parent=styles["BodyText"], fontSize=10.5, leading=15)
    doc = SimpleDocTemplate(str(out_pdf), pagesize=A4, title=title,
                            leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm, bottomMargin=2 * cm)
    max_w, max_h = A4[0] - 4 * cm, 11 * cm

    story = [Paragraph(escape(title), styles["Title"])]
    if source_url:
        story.append(Paragraph(f'Source: <a href="{escape(source_url)}" color="blue">{escape(source_url)}</a>', styles["Normal"]))
    story.append(Spacer(1, 0.5 * cm))

    frames_dir = workdir / "frames"
    prev, n = None, 0
    for i, step in enumerate(steps, 1):
        t = step["start"] + min(1.5, max(step["end"] - step["start"], 0) / 2)
        img_path = frames_dir / f"step_{i:03d}.jpg"
        extract_frame(video, t, img_path, region)
        flow = [Paragraph(f"Step {i} &nbsp;·&nbsp; {_fmt(step['start'])}", step_style)]
        if skip_duplicates and prev and _similar(prev, img_path):
            img_path.unlink()  # same picture as previous step: text only
        else:
            w, h = Image.open(img_path).size
            scale = min(max_w / w, max_h / h)
            flow.append(RLImage(str(img_path), width=w * scale, height=h * scale))
            flow.append(Spacer(1, 0.2 * cm))
            prev = img_path
            n += 1
        flow.append(Paragraph(escape(step["text"]), body))
        story.append(KeepTogether(flow))
    doc.build(story)
    return n
