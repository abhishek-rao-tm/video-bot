import os
import time
import logging
import requests
import boto3
from flask import Flask, request
from slack_sdk import WebClient

# ── CONFIG ────────────────────────────────────────────────────────────────
RUNWAY_KEY  = os.getenv("RUNWAY_API_KEY")
BUCKET      = os.getenv("S3_BUCKET")
AWS_ID      = os.getenv("AWS_ID")
AWS_SECRET  = os.getenv("AWS_KEY")
SLACK_TOKEN = os.getenv("SLACK_BOT_TOKEN")

# ── CLIENTS ───────────────────────────────────────────────────────────────
s3     = boto3.client("s3",
          aws_access_key_id=AWS_ID,
          aws_secret_access_key=AWS_SECRET,
          region_name="ap-south-1")
slack  = WebClient(token=SLACK_TOKEN)
app    = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# ── RUNWAY HELPER ─────────────────────────────────────────────────────────
RUNWAY_URL   = "https://api.runwayml.com/v1/generations"    # ← switched to v1
HEADERS      = {"Authorization": f"Bearer {RUNWAY_KEY}",
                "Content-Type":  "application/json"}

def runway_video(prompt: str):
    payload = {
        "model":   "gen-2.5-alpha",
        "prompt":  prompt or "hello",
        "duration": 10
    }

    r = requests.post(RUNWAY_URL, headers=HEADERS, json=payload)
    logging.info("RUNWAY ↩ %s %s", r.status_code, r.text[:300])

    if r.status_code not in (200, 201):
        try:
            msg = r.json().get("message", r.text)
        except ValueError:
            msg = r.text
        return None, f"Runway {r.status_code}: {msg}"

    jid = r.json().get("id")
    if not jid:
        return None, f"Runway response missing id: {r.text}"

    # poll
    while True:
        job = requests.get(f"{RUNWAY_URL}/{jid}", headers=HEADERS).json()
        state = job.get("status")

        if state == "succeeded":
            vid_url = job["output"]["url"]
            return requests.get(vid_url).content, None
        if state in {"failed", "cancelled"}:
            return None, f"Runway job {state}: {job}"
        time.sleep(4)

# ── S3 HELPER ─────────────────────────────────────────────────────────────
def upload_to_s3(data: bytes, key: str) -> str:
    s3.put_object(Bucket=BUCKET, Key=key, Body=data,
                  ACL="public-read", ContentType="video/mp4")
    return f"https://{BUCKET}.s3.ap-south-1.amazonaws.com/{key}"

# ── FLASK ROUTES ──────────────────────────────────────────────────────────
@app.route("/slack/events", methods=["POST"])
def slack_events():
    body = request.json or {}
    if "challenge" in body:                       # Slack URL verification
        return body["challenge"]

    event = body.get("event", {})
    if event.get("type") == "app_mention":
        text    = event.get("text", "")
        channel = event.get("channel")

        vid, err = runway_video(text)
        if err:
            slack.chat_postMessage(channel=channel, text=f"⚠️ {err}")
            return "OK", 200

        link = upload_to_s3(vid, f"video-{int(time.time())}.mp4")
        slack.chat_postMessage(channel=channel,
                               text=f"Here you go! {link}")

    return "OK", 200

@app.route("/healthz")
def health():
    return "pong", 200

# ── ENTRYPOINT ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
