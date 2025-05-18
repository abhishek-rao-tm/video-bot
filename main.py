import os, time, json, requests, boto3
from flask import Flask, request
from slack_sdk import WebClient

# 1. ENV
RUNWAY = os.getenv("RUNWAY_API_KEY")
ELEVEN = os.getenv("ELEVEN_API_KEY")
BUCKET = os.getenv("S3_BUCKET")
AWS_ID = os.getenv("AWS_ID")
AWS_KEY = os.getenv("AWS_KEY")
SLACK_BOT = os.getenv("SLACK_BOT_TOKEN")

s3 = boto3.client("s3",
      aws_access_key_id=AWS_ID,
      aws_secret_access_key=AWS_KEY,
      region_name="ap-south-1")
slack = WebClient(token=SLACK_BOT)
app = Flask(__name__)

def runway_video(prompt):
    url = "https://api.runwayml.com/v2/generations"
    headers = {"Authorization": f"Bearer {RUNWAY}",
               "Content-Type": "application/json"}
    payload = {
        # 👇 Swap model if you don’t have Gen-3 access
        "model": "gen-2.5-alpha",
        "prompt": prompt,
        "duration": 10
    }

    resp = requests.post(url, headers=headers, json=payload)

    # NEW — small log dump so Render logs show the real issue
    print("RUNWAY_STATUS", resp.status_code)
    print("RUNWAY_BODY", resp.text[:300])  # first 300 chars

    data = resp.json()
    if "id" not in data:
        # Optional: reply gracefully instead of crashing
        return None, data.get("message", "Runway error")

    jid = data["id"]

    # Poll every 4 s until finished
    while True:
        status = requests.get(
            f"{url}/{jid}",
            headers=headers).json()

        if status.get("status") == "succeeded":
            video_bytes = requests.get(status["output"]["url"]).content
            return video_bytes, None
        time.sleep(4)


def s3_url(data, name):
    s3.put_object(Bucket=BUCKET, Key=name, Body=data,
                  ACL="public-read", ContentType="video/mp4")
    return f"https://{BUCKET}.s3.ap-south-1.amazonaws.com/{name}"

@app.route("/slack/events", methods=["POST"])
def events():
    if body["event"]["type"] == "app_mention":
    txt  = body["event"]["text"]
    chan = body["event"]["channel"]

    vid, err = runway_video(txt)
    if err:
        slack.chat_postMessage(channel=chan, text=f"⚠️ {err}")
        return "OK"

    link = s3_url(vid, f"video-{int(time.time())}.mp4")
    slack.chat_postMessage(channel=chan, text=f"Here you go! {link}")

    return "OK"

@app.route("/healthz")        # keep-alive probe
def healthz():
    return "pong"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
