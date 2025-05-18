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
    r = requests.post(
      "https://api.runwayml.com/v2/generations",
      headers={"Authorization": f"Bearer {RUNWAY}"},
      json={"model":"gen-3-alpha","prompt":prompt,"duration":10}).json()
    jid = r["id"]
    while True:
        status = requests.get(
           f"https://api.runwayml.com/v2/generations/{jid}",
           headers={"Authorization": f"Bearer {RUNWAY}"}).json()
        if status["status"] == "succeeded":
            return requests.get(status["output"]["url"]).content
        time.sleep(4)

def s3_url(data, name):
    s3.put_object(Bucket=BUCKET, Key=name, Body=data,
                  ACL="public-read", ContentType="video/mp4")
    return f"https://{BUCKET}.s3.ap-south-1.amazonaws.com/{name}"

@app.route("/slack/events", methods=["POST"])
def events():
    body = request.json
    if "challenge" in body:                # URL verification handshake
        return body["challenge"]
    if body["event"]["type"] == "app_mention":
        txt = body["event"]["text"]
        chan = body["event"]["channel"]
        vid = runway_video(txt)
        link = s3_url(vid, f"video-{int(time.time())}.mp4")
        slack.chat_postMessage(channel=chan, text=f"Here you go! {link}")
    return "OK"

@app.route("/healthz")        # keep-alive probe
def healthz():
    return "pong"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
