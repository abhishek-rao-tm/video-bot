"""
Slack bot → Pollinations (free image) → MoviePy cross-fade → S3 MP4
Completely key-free and 100 % free-tier.
"""

import os, io, time, logging, random
import requests, boto3
from PIL import Image
import moviepy.editor as mpy
from flask import Flask, request
from slack_sdk import WebClient

# ── ENV ───────────────────────────────────────────────────────────────────
S3_BUCKET  = os.environ["S3_BUCKET"]
AWS_ID     = os.environ["AWS_ID"]
AWS_SECRET = os.environ["AWS_KEY"]
SLACK_TOK  = os.environ["SLACK_BOT_TOKEN"]

# ── CLIENTS ───────────────────────────────────────────────────────────────
s3     = boto3.client("s3",
          aws_access_key_id=AWS_ID,
          aws_secret_access_key=AWS_SECRET,
          region_name="ap-south-1")
slack  = WebClient(token=SLACK_TOK)
app    = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# ── CONSTANTS ─────────────────────────────────────────────────────────────
IMG_COUNT   = 6           # frames per clip
SECS_TOTAL  = 3           # total video length
WIDTH, HGT  = 1024, 576   # 16:9 720 p-ish

# ── HELPERS ───────────────────────────────────────────────────────────────
def pollinations_url(prompt: str) -> str:
    seed = random.randint(0, 999999)
    return f"https://image.pollinations.ai/prompt/{prompt}?seed={seed}"

import time, random

def fetch_images(prompt: str, n=IMG_COUNT):
    imgs = []
    for i in range(n):
        url   = pollinations_url(prompt)
        delay = 2
        for attempt in range(1, 6):          # ← 5 tries instead of 3
            try:
                r = requests.get(url, timeout=60)
                r.raise_for_status()
                img = Image.open(io.BytesIO(r.content)).convert("RGB")
                img = img.resize((WIDTH, HGT), Image.LANCZOS)
                imgs.append(img)
                break
            except Exception as e:
                logging.warning(
                    "Frame %d attempt %d failed: %s", i+1, attempt, e)
                if attempt == 5:
                    fallback = (imgs[-1] if imgs else
                                Image.new("RGB", (WIDTH, HGT), "black"))
                    imgs.append(fallback)
                else:
                    time.sleep(delay)
                    delay *= 2
    return imgs


import numpy as np   # ← add at the imports

def make_video(prompt: str):
    imgs   = fetch_images(prompt or "abstract colorful shapes")
    dur    = SECS_TOTAL / len(imgs)
    clips  = []

    for img in imgs:
        frame = np.array(img)                # ← convert PIL → ndarray
        clip  = mpy.ImageClip(frame).set_duration(dur)
        clips.append(clip)

    video = mpy.concatenate_videoclips(clips, method="compose").crossfadein(dur/2)

    video.write_videofile("/tmp/out.mp4",
                          fps=24, codec="libx264",
                          audio=False, preset="ultrafast",
                          logger=None)
    with open("/tmp/out.mp4", "rb") as f:
        return f.read()


def upload(video: bytes) -> str:
    key = f"video-{int(time.time())}.mp4"
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=key,
        Body=video,
        ContentType="video/mp4"
        # ACL parameter removed – bucket doesn’t allow ACLs
    )
    # presigned URL stays valid 7 days (default)
    return s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": S3_BUCKET, "Key": key},
        ExpiresIn=604800
    )

# ── SLACK ROUTE ───────────────────────────────────────────────────────────
@app.route("/slack/events", methods=["POST"])
def slack_events():
    body = request.json or {}
    if "challenge" in body:
        return body["challenge"]

    ev = body.get("event", {})
    if ev.get("type") == "app_mention":
        prompt = ev.get("text", "")
        chan   = ev.get("channel")

        try:
            mp4   = make_video(prompt)
            link  = upload(mp4)
            slack.chat_postMessage(channel=chan, text=link)
        except Exception as e:
            logging.exception("Generation failed")
            slack.chat_postMessage(channel=chan, text=f"⚠️ {e}")

    return "OK", 200

@app.route("/healthz")
def health(): return "pong", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
