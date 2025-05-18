import os, time, logging, requests, boto3
from flask import Flask, request
from slack_sdk import WebClient

# ── ENV ───────────────────────────────────────────────────────────────────
RUNWAY_KEY  = os.getenv("RUNWAY_API_KEY")
BUCKET      = os.getenv("S3_BUCKET")
AWS_ID      = os.getenv("AWS_ID")
AWS_SECRET  = os.getenv("AWS_KEY")
SLACK_TOKEN = os.getenv("SLACK_BOT_TOKEN")

# ── CLIENTS ───────────────────────────────────────────────────────────────
s3    = boto3.client("s3",
         aws_access_key_id=AWS_ID,
         aws_secret_access_key=AWS_SECRET,
         region_name="ap-south-1")
slack = WebClient(token=SLACK_TOKEN)
app   = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# ── CONSTANTS ─────────────────────────────────────────────────────────────
HEADERS = {
    "Authorization": f"Bearer {RUNWAY_KEY}",
    "Content-Type":  "application/json"
}
ENDPOINTS = [                         # we’ll probe in this order
    "https://api.runwayml.com/v1/generations",
    "https://api.runwayml.com/v2/generations",
    "https://api.runwayml.com/generations"
]

# ── RUNWAY HELPER ─────────────────────────────────────────────────────────
def runway_video(prompt: str):
    payload = {
        "model":   "gen-2.5-alpha",   # change if you have another model
        "prompt":  prompt or "hello",
        "duration": 10
    }

    # try each endpoint until one accepts the request
    for base in ENDPOINTS:
        r = requests.post(base, headers=HEADERS, json=payload)
        logging.info("RUNWAY ↩ %s (%s) %s",
                     r.status_code, base, r.text[:200])

        if r.status_code in (200, 201):
            job_id = r.json().get("id")
            if not job_id:
                return None, f"Runway gave no id on {base}"
            # poll
            while True:
                job = requests.get(f"{base}/{job_id}", headers=HEADERS).json()
                state = job.get("status")
                if state == "succeeded":
                    vid = requests.get(job["output"]["url"]).content
                    return vid, None
                if state in {"failed", "cancelled"}:
                    return None, f"Runway job {state}: {job}"
                time.sleep(4)

        # if 400 Invalid-API-Version keep looping to next base URL
        if "Invalid API Version" not in r.text:
            # any other 4xx/5xx → bubble up immediately
            try:
                msg = r.json().get("message", r.text)
            except ValueError:
                msg = r.text
            return None, f"Runway {r.status_code}: {msg}"

    return None, "Runway rejected every known endpoint (API version mismatch)"

# ── S3 HELPER ─────────────────────────────────────────────────────────────
def upload_to_s3(data: bytes, key: str) -> str:
    s3.put_object(Bucket=BUCKET, Key=key, Body=data,
                  ACL="public-read", ContentType="video/mp4")
    return f"https://{BUCKET}.s3.ap-south-1.amazonaws.com/{key}"

# ── FLASK ROUTES ──────────────────────────────────────────────────────────
@app.route("/slack/events", methods=["POST"])
def slack_events():
    body = request.json or {}
    if "challenge" in body:                 # Slack handshake
        return body["challenge"]

    ev = body.get("event", {})
    if ev.get("type") == "app_mention":
        txt, chan = ev.get("text", ""), ev.get("channel")
        vid, err  = runway_video(txt)
        if err:
            slack.chat_postMessage(channel=chan, text=f"⚠️ {err}")
            return "OK", 200
        link = upload_to_s3(vid, f"video-{int(time.time())}.mp4")
        slack.chat_postMessage(channel=chan,
                               text=f"Here you go! {link}")
    return "OK", 200

@app.route("/healthz")
def health():
    return "pong", 200

# ── ENTRYPOINT ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
