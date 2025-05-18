"""
Slack → Pollinations.ai (free images) → MoviePy cross-fade → S3 MP4
100 % free-tier, works on buckets with Object-Owner–Enforced ACLs.
"""

import os, io, time, random, logging
import requests, boto3, numpy as np
from PIL import Image
import moviepy.editor as mpy
from flask import Flask, request
from slack_sdk import WebClient

# ── CONFIG ────────────────────────────────────────────────────────────────
S3_BUCKET  = os.environ["S3_BUCKET"]
AWS_ID     = os.environ["AWS_ID"]
AWS_SECRET = os.environ["AWS_KEY"]
SLACK_TOK  = os.environ["SLACK_BOT_TOKEN"]

IMG_COUNT   = 6        # frames per clip
SECS_TOTAL  = 3        # final video length
WIDTH, HGT  = 1024, 576

# ── CLIENTS ───────────────────────────────────────────────────────────────
s3     = boto3.client("s3",
          aws_access_key_id=AWS_ID,
          aws_secret_access_key=AWS_SECRET,
          region_name="ap-south-1")
slack  = WebClient(token=SLACK_TOK)
app    = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# ── HELPERS ───────────────────────────────────────────────────────────────
def pollinations_url(prompt: str) -> str:
    seed = random.randint(0, 999_999)
    return f"https://image.pollinations.ai/prompt/{prompt}?seed={seed}"

def fetch_images(prompt: str, n: int = IMG_COUNT):
    imgs = []
    for i in range(n):
        url   = pollinations_url(prompt)
        delay = 2
        for attempt in range(1, 6):                     # 5 retries
            try:
                r = requests.get(url, timeout=60)
                r.raise_for_status()
                img = Image.open(io.BytesIO(r.content)).convert("RGB")
                img = img.resize((WIDTH, HGT), Image.LANCZOS)
                imgs.append(img)
                break                                   # success
            except Exception as e:
                logging.warning(
                    "Frame %d attempt %d failed: %s", i + 1, attempt, e)
                if attempt == 5:                        # final fail
                    fallback = (
                        imgs[-1] if imgs else Image.new("RGB", (WIDTH, HGT), "black")
                    )
                    imgs.append(fallback)
                else:
                    time.sleep(delay)
                    delay *= 2                          # back-off
    return imgs

def make_video(prompt: str):
    imgs  = fetch_images(prompt or "abstract colorful shapes")
    dur   = SECS_TOTAL / len(imgs)
    clips = [
        mpy.ImageClip(np.array(img)).set_duration(dur) for img in imgs
    ]
    video = mpy.concatenate_videoclips(clips, method="compose").crossfadein(dur / 2)

    out_path = "/tmp/out.mp4"
    video.write_videofile(out_path,
                          fps=24,
                          codec="libx264",
                          audio=False,
                          preset="ultrafast",
                          logger=None)
    with open(out_path, "rb") as f:
        return f.read()

def upload(video_bytes: bytes) -> str:
    key = f"video-{int(time.time())}.mp4"
    # bucket has Owner-Enforced ACLs → **no ACL arg**
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=key,
        Body=video_bytes,
        ContentType="video/mp4"
    )
    # 7-day presigned link
    return s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": S3_BUCKET, "Key": key},
        ExpiresIn=604_800
    )

# ── ROUTES ────────────────────────────────────────────────────────────────
import threading

@app.route("/slack/events", methods=["POST"])
def slack_events():
    body = request.json or {}
    if "challenge" in body:                      # Slack URL verification
        return body["challenge"]

    ev = body.get("event", {})
    if ev.get("type") == "app_mention":
        raw    = ev.get("text", "")
        parts  = raw.split(maxsplit=1)
        prompt = parts[1] if parts and parts[0].startswith("<@") else raw
        chan   = ev.get("channel")

        # --- run generator in a background thread ---
        def worker():
            try:
                mp4  = make_video(prompt)
                link = upload(mp4)
                slack.chat_postMessage(channel=chan, text=link)
            except Exception as e:
                logging.exception("Generation failed")
                slack.chat_postMessage(channel=chan, text=f"⚠️ {e}")

        threading.Thread(target=worker, daemon=True).start()

    # Instant ack so Slack won't retry
    return "OK", 200

