import os, time, logging, requests, boto3
from flask import Flask, request
from slack_sdk import WebClient

# ── CONFIG ────────────────────────────────────────────────────────────────
RUNWAY_KEY  = os.environ["RUNWAY_API_KEY"]
BUCKET      = os.environ["S3_BUCKET"]
AWS_ID      = os.environ["AWS_ID"]
AWS_SECRET  = os.environ["AWS_KEY"]
SLACK_TOKEN = os.environ["SLACK_BOT_TOKEN"]

s3     = boto3.client("s3",
          aws_access_key_id=AWS_ID,
          aws_secret_access_key=AWS_SECRET,
          region_name="ap-south-1")
slack  = WebClient(token=SLACK_TOKEN)
app    = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# ── ENDPOINT CANDIDATES ───────────────────────────────────────────────────
CANDIDATES = [
    {   # 1️⃣  Stable API (May/Jun 2025 rollout)
        "url":    "https://api.runwayml.com/v1/videos",
        "json":   lambda prompt: {
            "prompt": prompt or "hello",
            "duration": 10,
            "model": "gen-2.5-alpha"
        },
        "headers": {"Authorization": f"Bearer {RUNWAY_KEY}",
                    "Content-Type": "application/json"}
    },
    {   # 2️⃣  /generations with explicit version header
        "url":    "https://api.runwayml.com/v1/generations",
        "json":   lambda prompt: {
            "prompt": prompt or "hello",
            "duration": 10,
            "model": "gen-2.5-alpha"
        },
        "headers": {"Authorization": f"Bearer {RUNWAY_KEY}",
                    "Content-Type": "application/json",
                    "X-Runway-Version": "2024-11-15"}
    },
    {   # 3️⃣  legacy v2
        "url":    "https://api.runwayml.com/v2/generations",
        "json":   lambda prompt: {
            "prompt": prompt or "hello",
            "duration": 10,
            "model": "gen-2.5-alpha"
        },
        "headers": {"Authorization": f"Bearer {RUNWAY_KEY}",
                    "Content-Type": "application/json"}
    },
    {   # 4️⃣  root
        "url":    "https://api.runwayml.com/generations",
        "json":   lambda prompt: {
            "prompt": prompt or "hello",
            "duration": 10,
            "model": "gen-2.5-alpha"
        },
        "headers": {"Authorization": f"Bearer {RUNWAY_KEY}",
                    "Content-Type": "application/json"}
    },
]

# ── RUNWAY CALL ───────────────────────────────────────────────────────────
def runway_video(prompt: str):
    for cfg in CANDIDATES:
        r = requests.post(cfg["url"], headers=cfg["headers"],
                          json=cfg["json"](prompt))
        logging.info("TRY %s -> %s %s", cfg["url"], r.status_code, r.text[:120])

        if r.status_code in (200, 201):
            data   = r.json()
            job_id = data.get("id") or data.get("job_id")
            if not job_id:
                return None, f"Runway success but no id: {data}"

            # poll
            while True:
                job = requests.get(f'{cfg["url"]}/{job_id}',
                                   headers=cfg["headers"]).json()
                state = job.get("status")
                if state == "succeeded":
                    vid_url = (job.get("output") or job).get("url")
                    video   = requests.get(vid_url).content
                    return video, None
                if state in {"failed", "cancelled"}:
                    return None, f"Runway job {state}: {job}"
                time.sleep(4)

        # If we got "Invalid API Version" keep trying next candidate
        if "Invalid API Version" not in r.text:
            # Stop on any *other* error
            try:
                msg = r.json().get("message", r.text)
            except ValueError:
                msg = r.text
            return None, f"Runway {r.status_code}: {msg}"

    return None, "Runway rejected every known endpoint/version"

# ── AWS S3 ────────────────────────────────────────────────────────────────
def upload(video: bytes) -> str:
    key = f"video-{int(time.time())}.mp4"
    s3.put_object(Bucket=BUCKET, Key=key, Body=video,
                  ACL="public-read", ContentType="video/mp4")
    return f"https://{BUCKET}.s3.ap-south-1.amazonaws.com/{key}"

# ── SLACK ROUTE ───────────────────────────────────────────────────────────
@app.route("/slack/events", methods=["POST"])
def events():
    body = request.json or {}
    if "challenge" in body:
        return body["challenge"]

    ev = body.get("event", {})
    if ev.get("type") == "app_mention":
        text, chan = ev.get("text", ""), ev.get("channel")
        vid, err   = runway_video(text)
        if err:
            slack.chat_postMessage(channel=chan, text=f"⚠️ {err}")
        else:
            slack.chat_postMessage(channel=chan, text=upload(vid))
    return "OK", 200

@app.route("/healthz")
def health():  return "pong", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
