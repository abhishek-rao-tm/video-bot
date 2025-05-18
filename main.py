"""
Slack bot → Hugging Face Stable Video Diffusion (open model) → S3 link
"""

import os, time, logging, boto3, requests
from flask import Flask, request
from slack_sdk import WebClient
from huggingface_hub import InferenceClient   #  ← InferenceError removed

# ── ENV ───────────────────────────────────────────────────────────────────
HF_TOKEN   = os.environ["HF_TOKEN"]
HF_MODEL   = os.environ.get("HF_MODEL",
              "cerspencer/stable-video-diffusion-img2vid-xt")
S3_BUCKET  = os.environ["S3_BUCKET"]
AWS_ID     = os.environ["AWS_ID"]
AWS_SECRET = os.environ["AWS_KEY"]
SLACK_TOK  = os.environ["SLACK_BOT_TOKEN"]

# ── CLIENTS ───────────────────────────────────────────────────────────────
hf     = InferenceClient(token=HF_TOKEN)
s3     = boto3.client("s3",
          aws_access_key_id=AWS_ID,
          aws_secret_access_key=AWS_SECRET,
          region_name="ap-south-1")
slack  = WebClient(token=SLACK_TOK)
app    = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# ── VIDEO GENERATOR ───────────────────────────────────────────────────────
def generate_video(prompt: str):
    """Return (video_bytes, None) or (None, error_text)."""
    try:
        mp4 = hf.text_to_video(model=HF_MODEL, prompt=prompt or "hello world")
        return mp4, None
    except Exception as e:                          # ← catch generic error
        return None, f"HF error: {e}"

# ── S3 UPLOAD ─────────────────────────────────────────────────────────────
def upload(video: bytes) -> str:
    key = f"video-{int(time.time())}.mp4"
    s3.put_object(Bucket=S3_BUCKET, Key=key, Body=video,
                  ACL="public-read", ContentType="video/mp4")
    return f"https://{S3_BUCKET}.s3.ap-south-1.amazonaws.com/{key}"

# ── SLACK ROUTE ───────────────────────────────────────────────────────────
@app.route("/slack/events", methods=["POST"])
def slack_events():
    body = request.json or {}
    if "challenge" in body:
        return body["challenge"]

    ev = body.get("event", {})
    if ev.get("type") == "app_mention":
        prompt, chan = ev.get("text", ""), ev.get("channel")
        vid, err     = generate_video(prompt)
        msg = f"⚠️ {err}" if err else upload(vid)
        slack.chat_postMessage(channel=chan, text=msg)
    return "OK", 200

@app.route("/healthz")
def health(): return "pong", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
