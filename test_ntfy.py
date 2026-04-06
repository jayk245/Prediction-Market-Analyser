import sys
sys.path.insert(0, "/Users/jaykumar/Documents/ProjectPredictionMarket")

from dotenv import load_dotenv
load_dotenv("/Users/jaykumar/Documents/ProjectPredictionMarket/.env")

import os
import httpx

NTFY_TOPIC = os.getenv("NTFY_TOPIC", "")
print(f"NTFY_TOPIC = '{NTFY_TOPIC}'")

if not NTFY_TOPIC:
    print("ERROR: NTFY_TOPIC is empty — add it to your .env file")
    sys.exit(1)

url = f"https://ntfy.sh/{NTFY_TOPIC}"
print(f"POSTing to {url} ...")

resp = httpx.post(
    url,
    content=b"Test notification from surveillance system",
    headers={
        "Title":    "[HIGH] TEST ALERT - polymarket",
        "Priority": "high",
        "Tags":     "warning",
    },
    timeout=10,
)
print(f"HTTP {resp.status_code}: {resp.text}")
