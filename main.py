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
def runway_video(prompt: str):
    """
    Send prompt to Runway, poll until finished.
    Returns (video_bytes | None, error_message | None)
    """
    url     = "https://api.runwayml.com/v2/generations"
    headers = {"Authorization": f"Bearer {RUNWAY}",
               "Content-Type":  "application/json"}
    payload = {
        "model":   "gen-2.5-alpha",      # swap to gen-3-alpha if you’re whitelisted
        "prompt":  prompt,
        "duration": 10
    }

    resp = requests.post(url, headers=headers, json=payload)
    print("RUNWAY_STATUS", resp.status_code)
    print("RUNWAY_HEADERS", dict(resp.headers))        # NEW
    print("RUNWAY_BODY", resp.text)                    # NEW — full body
    print("-" * 60)


    try:
        data = resp.json()
    except ValueError:
        return None, f"Runway sent a non-JSON response ({resp.status_code})"

    job_id = data.get("id")
    if not job_id:
        return None, data.get("message", "Runway rejected the request")

    # ── Poll every 4 s until the job is ready ──
    while True:
        job = requests.get(f"{url}/{job_id}", headers=headers).json()
        state = job.get("status")

        if state == "succeeded":
            video_url   = job["output"]["url"]
            video_bytes = requests.get(video_url).content
            return video_bytes, None

        if state in {"failed", "cancelled"}:
            return None, f"Runway job failed: {job}"

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
