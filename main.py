import os
import time
import requests
import boto3
from flask import Flask, request
from slack_sdk import WebClient

# ── ENV VARS ──────────────────────────────────────────────────────────────
RUNWAY      = os.getenv("RUNWAY_API_KEY")
BUCKET      = os.getenv("S3_BUCKET")
AWS_ID      = os.getenv("AWS_ID")
AWS_KEY     = os.getenv("AWS_KEY")
SLACK_TOKEN = os.getenv("SLACK_BOT_TOKEN")

# ── CLIENTS ───────────────────────────────────────────────────────────────
s3     = boto3.client(
            "s3",
            aws_access_key_id=AWS_ID,
            aws_secret_access_key=AWS_KEY,
            region_name="ap-south-1")
slack  = WebClient(token=SLACK_TOKEN)
app    = Flask(__name__)

# ── HELPERS ───────────────────────────────────────────────────────────────
import logging
logging.basicConfig(level=logging.INFO)

def runway_video(prompt: str):
    """
    Hit Runway → return (bytes, None) on success
                → return (None, "human-readable error") on any failure.
    """
    url     = "https://api.runwayml.com/v2/generations"
    headers = {"Authorization": f"Bearer {RUNWAY}",
               "Content-Type":  "application/json"}
    payload = {
        "model": "gen-2.5-alpha",     # public model
        "prompt": prompt or "hello",  # fallback if Slack sent only a mention
        "duration": 10
    }

    resp = requests.post(url, headers=headers, json=payload)

    # 100 % guaranteed to hit Render logs
    logging.info("RUNWAY ↩ %s %s", resp.status_code, resp.text[:500])

    # If Runway says “201 Created” or “200 OK” and gives an id → continue
    if resp.status_code in (200, 201):
        data = resp.json()
        job_id = data.get("id")
        if not job_id:
            return None, f"Runway no-id error: {data}"
    else:
        # Any non-200 is an error right away
        try:
            err_msg = resp.json().get("message", resp.text)
        except ValueError:
            err_msg = resp.text
        return None, f"Runway {resp.status_code}: {err_msg}"

    # ── Poll until finished ──
    while True:
        job = requests.get(f"{url}/{job_id}", headers=headers).json()
        state = job.get("status")

        if state == "succeeded":
            vid_url = job["output"]["url"]
            return requests.get(vid_url).content, None

        if state in {"failed", "cancelled"}:
            return None, f"Runway job {state}: {job}"

        time.sleep(4)


def upload_to_s3(data: bytes, key: str) -> str:
    s3.put_object(
        Bucket      = BUCKET,
        Key         = key,
        Body        = data,
        ACL         = "public-read",
        ContentType = "video/mp4"
    )
    return f"https://{BUCKET}.s3.ap-south-1.amazonaws.com/{key}"

# ── ROUTES ────────────────────────────────────────────────────────────────
@app.route("/slack/events", methods=["POST"])
def slack_events():
    body = request.json or {}

    # Slack URL-verification handshake
    if "challenge" in body:
        return body["challenge"]

    event = body.get("event", {})
    if event.get("type") == "app_mention":
        text   = event.get("text", "")
        channel = event.get("channel")

        video_bytes, err = runway_video(text)
        if err:
            slack.chat_postMessage(channel=channel,
                                   text=f"⚠️ {err}")
            return "OK", 200

        link = upload_to_s3(video_bytes,
                            f"video-{int(time.time())}.mp4")
        slack.chat_postMessage(channel=channel,
                               text=f"Here you go! {link}")

    return "OK", 200


@app.route("/healthz")
def healthz():
    return "pong", 200

# ── ENTRYPOINT ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(host="0.0.0.0",
            port=int(os.getenv("PORT", 10000)))
