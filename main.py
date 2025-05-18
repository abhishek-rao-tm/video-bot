"""
Slack → Hugging Face Stable Video Diffusion (img2vid-xt) → S3
Shows exact HF errors in Slack and Render logs.
"""

import os, time, logging, boto3, requests
from flask import Flask, request
from slack_sdk import WebClient
from huggingface_hub import InferenceClient

# ── ENV ───────────────────────────────────────────────────────────────────
HF_TOKEN  = os.environ["HF_TOKEN"]                       # hf_xxx
HF_MODEL  = os.environ.get("HF_MODEL",
             "stabilityai/stable-video-diffusion-img2vid-xt")
S3_BUCKET = os.environ["S3_BUCKET"]
AWS_ID    = os.environ["AWS_ID"]
AWS_KEY   = os.environ["AWS_KEY"]
SLACK_TOK = os.environ["SLACK_BOT_TOKEN"]

# ── BANNER so you know THIS build is running ──────────────────────────────
print("🚀  BOT STARTING — model:", HF_MODEL)

# ── CLIENTS ───────────────────────────────────────────────────────────────
hf     = InferenceClient(token=HF_TOKEN)
s3     = boto3.client("s3",
          aws_access_key_id=AWS_ID,
          aws_secret_access_key=AWS_KEY,
          region_name="ap-south-1")
slack  = WebClient(token=SLACK_TOK)
app    = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# ── VIDEO GENERATOR ───────────────────────────────────────────────────────
def generate_video(prompt: str):
    """Return (bytes, None) or (None, human-readable error)."""
    try:
        mp4 = hf.text_to_video(model=HF_MODEL, prompt=prompt or "hello world")
        return mp4, None

    except requests.HTTPError as e:
        resp = e.response
        status = resp.status_code if resp else "N/A"
        body   = resp.text.strip()[:300] if resp else repr(e)
        if not body:                       # make sure it's never empty
            body = "<empty response body>"
        logging.error("HF HTTPError %s → %s", status, body)
        return None, f"HF error {status}: {body}"

    except Exception as e:
        logging.exception("HF unknown error")
        return None, f"HF error: {e or 'unknown'}"

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
        txt  = ev.get("text", "")
        chan = ev.get("channel")

        vid, err = generate_video(txt)
        msg = f"⚠️ {err}" if err else upload(vid)
        slack.chat_postMessage(channel=chan, text=msg)
    return "OK", 200

@app.route("/healthz")
def health(): return "pong", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
