# main.py  —  Slack bot ▶ HuggingFace SVD-Img2Vid ▶ S3
import os, time, logging, requests, boto3
from flask import Flask, request
from slack_sdk import WebClient
from huggingface_hub import InferenceClient, InferenceError

# ── ENV ───────────────────────────────────────────────────────────────────
HF_TOKEN   = os.environ["HF_TOKEN"]                       # <-- your hf_ key
HF_MODEL   = os.environ.get("HF_MODEL",
              "cerspencer/stable-video-diffusion-img2vid-xt")
BUCKET     = os.environ["S3_BUCKET"]
AWS_ID     = os.environ["AWS_ID"]
AWS_SECRET = os.environ["AWS_KEY"]
SLACK_TOK  = os.environ["SLACK_BOT_TOKEN"]

# ── CLIENTS ───────────────────────────────────────────────────────────────
hf    = InferenceClient(token=HF_TOKEN)
s3    = boto3.client("s3",
         aws_access_key_id=AWS_ID,
         aws_secret_access_key=AWS_SECRET,
         region_name="ap-south-1")
slack = WebClient(token=SLACK_TOK)
app   = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# ── VIDEO GENERATOR ───────────────────────────────────────────────────────
def make_video(prompt: str):
    """
    Uses HuggingFace Inference API to create a ~2-sec 576×1024 MP4.
    Returns (bytes, None) on success – (None, error_text) on failure.
    """
    try:
        vid_bytes = hf.text_to_video(
            model = HF_MODEL,
            prompt = prompt or "hello world"
        )
        return vid_bytes, None
    except InferenceError as e:
        return None, f"HF error: {e}"

# ── S3 UPLOAD ─────────────────────────────────────────────────────────────
def upload(video: bytes) -> str:
    key = f"video-{int(time.time())}.mp4"
    s3.put_object(Bucket=BUCKET, Key=key, Body=video,
                  ACL="public-read", ContentType="video/mp4")
    return f"https://{BUCKET}.s3.ap-south-1.amazonaws.com/{key}"

# ── SLACK ROUTE ───────────────────────────────────────────────────────────
@app.route("/slack/events", methods=["POST"])
def slack_events():
    body = request.json or {}
    if "challenge" in body:                 # Slack URL verification
        return body["challenge"]

    ev = body.get("event", {})
    if ev.get("type") == "app_mention":
        txt, chan = ev.get("text", ""), ev.get("channel")
        vid, err  = make_video(txt)
        if err:
            slack.chat_postMessage(channel=chan, text=f"⚠️ {err}")
        else:
            slack.chat_postMessage(channel=chan, text=upload(vid))
    return "OK", 200

@app.route("/healthz")
def health(): return "pong", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
