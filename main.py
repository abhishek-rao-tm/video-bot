"""
Slack bot → Hugging Face Stable Video Diffusion (img2vid) → S3 link
Author: ChatGPT – May 2025
"""

import os
import time
import logging
import boto3
import requests
from flask import Flask, request
from slack_sdk import WebClient
from huggingface_hub import InferenceClient, InferenceError

# ────────────────────────────── ENV ───────────────────────────────────────
HF_TOKEN   = os.environ["HF_TOKEN"]                      # hf_...
HF_MODEL   = os.environ.get("HF_MODEL",
               "cerspencer/stable-video-diffusion-img2vid-xt")
S3_BUCKET  = os.environ["S3_BUCKET"]
AWS_ID     = os.environ["AWS_ID"]
AWS_SECRET = os.environ["AWS_KEY"]
SLACK_TOK  = os.environ["SLACK_BOT_TOKEN"]

# ─────────────────────────── CLIENTS ──────────────────────────────────────
hf     = InferenceClient(token=HF_TOKEN)
s3     = boto3.client("s3",
          aws_access_key_id=AWS_ID,
          aws_secret_access_key=AWS_SECRET,
          region_name="ap-south-1")
slack  = WebClient(token=SLACK_TOK)
app    = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# ──────────────────── HUGGING FACE CALL (1 shot) ──────────────────────────
def generate_video(prompt: str):
    """
    Returns (mp4_bytes, None) on success,
            (None, error_message) on failure.
    """
    try:
        # This model returns binary MP4 directly
        mp4_bytes = hf.text_to_video(
            model   = HF_MODEL,
            prompt  = prompt or "hello world"
        )
        return mp4_bytes, None
    except InferenceError as e:
        return None, f"HF error: {e}"

# ────────────────────────── S3 UPLOAD ─────────────────────────────────────
def upload_to_s3(data: bytes) -> str:
    key = f"video-{int(time.time())}.mp4"
    s3.put_object(Bucket=S3_BUCKET, Key=key, Body=data,
                  ACL="public-read", ContentType="video/mp4")
    return f"https://{S3_BUCKET}.s3.ap-south-1.amazonaws.com/{key}"

# ────────────────────────── SLACK ROUTES ──────────────────────────────────
@app.route("/slack/events", methods=["POST"])
def slack_events():
    body = request.json or {}
    if "challenge" in body:             # Slack URL verification
        return body["challenge"]

    ev = body.get("event", {})
    if ev.get("type") == "app_mention":
        prompt = ev.get("text", "")
        channel = ev.get("channel")

        video, err = generate_video(prompt)
        if err:
            slack.chat_postMessage(channel=channel, text=f"⚠️ {err}")
        else:
            link = upload_to_s3(video)
            slack.chat_postMessage(channel=channel, text=link)

    return "OK", 200

@app.route("/healthz")
def health():            # for uptime pings
    return "pong", 200

# ────────────────────────── ENTRYPOINT ────────────────────────────────────
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
