# main.py  —  Slack bot → Replicate Stable Video Diffusion → S3
import os, time, logging, requests, boto3
from flask import Flask, request
from slack_sdk import WebClient
import replicate                      # NEW

# ── ENV ───────────────────────────────────────────────────────────────────
REPL_TOKEN  = os.environ["REPLICATE_API_TOKEN"]   # r8_… key
S3_BUCKET   = os.environ["S3_BUCKET"]
AWS_ID      = os.environ["AWS_ID"]
AWS_SECRET  = os.environ["AWS_KEY"]
SLACK_TOKEN = os.environ["SLACK_BOT_TOKEN"]

# ── CLIENTS ───────────────────────────────────────────────────────────────
rep = replicate.Client(api_token=REPL_TOKEN)
s3  = boto3.client("s3",
        aws_access_key_id=AWS_ID,
        aws_secret_access_key=AWS_SECRET,
        region_name="ap-south-1")
slack = WebClient(token=SLACK_TOKEN)
app   = Flask(__name__)
logging.basicConfig(level=logging.INFO)

MODEL  = "stability-ai/sdxl-video-generator"      # SV-Diff 1.1
DUR    = 4                                        # seconds

# ── VIDEO GENERATOR ───────────────────────────────────────────────────────
def make_video(prompt: str):
    """Return (mp4_bytes, None) or (None, error_text)."""
    try:
        pred = rep.run(
            MODEL,
            input={
                "prompt": prompt or "hello world",
                "seconds": DUR,
                "seed": 42
            }
        )
    except replicate.exceptions.ReplicateError as e:
        return None, f"Replicate error: {e}"

    # pred is a list of URLs; pick the first ending with .mp4
    url = next((u for u in pred if u.endswith(".mp4")), None)
    if not url:
        return None, "No video URL returned"
    video = requests.get(url).content
    return video, None

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
        vid, err = make_video(txt)
        if err:
            slack.chat_postMessage(channel=chan, text=f"⚠️ {err}")
        else:
            slack.chat_postMessage(channel=chan, text=upload(vid))
    return "OK", 200

@app.route("/healthz")
def health(): return "pong", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
