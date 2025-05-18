"""
Slack → Pollinations.ai (free images) → MoviePy cross-fade → S3 MP4
Works on Render free tier & buckets with ACLs disabled.
"""

import os, io, time, random, logging, threading
import requests, boto3, numpy as np
from PIL import Image
import moviepy.editor as mpy
import imageio_ffmpeg
from flask import Flask, request
from slack_sdk import WebClient

# ── CONFIG ────────────────────────────────────────────────────────────────
S3_BUCKET   = os.environ["S3_BUCKET"]
AWS_ID      = os.environ["AWS_ID"]
AWS_SECRET  = os.environ["AWS_KEY"]
S3_REGION   = "eu-north-1"          # <---- put your bucket region here
SLACK_TOKEN = os.environ["SLACK_BOT_TOKEN"]

IMG_COUNT  = 6
SECS_TOTAL = 3
WIDTH, HGT = 1024, 576

# ── CLIENTS ───────────────────────────────────────────────────────────────
s3    = boto3.client("s3",
         aws_access_key_id=AWS_ID,
         aws_secret_access_key=AWS_SECRET,
         region_name=S3_REGION)
slack = WebClient(token=SLACK_TOKEN)
app   = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# ── Tell MoviePy where ffmpeg lives (wheel-bundled) ───────────────────────
mpy.config.change_settings(
    {"FFMPEG_BINARY": imageio_ffmpeg.get_ffmpeg_exe()}
)

# ── HELPERS ───────────────────────────────────────────────────────────────
def pollinations_url(prompt: str) -> str:
    return f"https://image.pollinations.ai/prompt/{prompt}?seed={random.randint(0, 999999)}"

def fetch_images(prompt: str, n=IMG_COUNT):
    imgs = []
    for i in range(n):
        url   = pollinations_url(prompt)
        delay = 2
        for attempt in range(1, 5):        # 4 tries max (keeps total <30 s)
            try:
                r = requests.get(url, timeout=25)
                r.raise_for_status()
                pic = Image.open(io.BytesIO(r.content)).convert("RGB")
                pic = pic.resize((WIDTH, HGT), Image.LANCZOS)
                imgs.append(pic)
                break
            except Exception as e:
                logging.warning("Frame %d try %d failed: %s", i+1, attempt, e)
                if attempt == 4:
                    fallback = imgs[-1] if imgs else Image.new("RGB", (WIDTH, HGT), "black")
                    imgs.append(fallback)
                else:
                    time.sleep(delay)
                    delay *= 2
    return imgs

def make_video(prompt: str) -> bytes:
    frames = fetch_images(prompt or "abstract colourful shapes")
    dur    = SECS_TOTAL / len(frames)
    clips  = [mpy.ImageClip(np.asarray(img)).set_duration(dur) for img in frames]
    video  = mpy.concatenate_videoclips(clips, method="compose").crossfadein(dur/2)

    out_file = "/tmp/out.mp4"
    video.write_videofile(out_file, fps=24, codec="libx264",
                          audio=False, preset="ultrafast", logger=None)
    with open(out_file, "rb") as f:
        return f.read()

def upload(video_bytes: bytes) -> str:
    key = f"video-{int(time.time())}.mp4"
    s3.put_object(Bucket=S3_BUCKET, Key=key, Body=video_bytes,
                  ContentType="video/mp4")            # no ACL param
    return s3.generate_presigned_url("get_object",
            Params={"Bucket": S3_BUCKET, "Key": key},
            ExpiresIn=604_800)                        # 7 days

# ── ROUTES ────────────────────────────────────────────────────────────────
@app.route("/slack/events", methods=["POST"])
def slack_events():
    body = request.json or {}
    if "challenge" in body:
        return body["challenge"]

    ev = body.get("event", {})
    if ev.get("type") == "app_mention":
        raw   = ev.get("text", "")
        parts = raw.split(maxsplit=1)
        prompt = parts[1] if parts and parts[0].startswith("<@") else raw
        chan   = ev.get("channel")

        def worker():
            try:
                mp4  = make_video(prompt)
                link = upload(mp4)
                logging.info("Generation OK -> %s", link)
                slack.chat_postMessage(channel=chan, text=link)
            except Exception as e:
                logging.exception("Generation failed")
                slack.chat_postMessage(channel=chan, text=f"⚠️ {e}")

        threading.Thread(target=worker, daemon=True).start()

    return "OK", 200

@app.route("/healthz")
def health(): return "pong", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
