"""
Kafka consumer — reads HIGH/CRITICAL alerts from the surveillance-alerts topic
and pushes them to your phone via ntfy.sh.

Run in a second terminal alongside `python3 main.py watch`:
    python3 kafka_notifier.py

Requires in .env:
    KAFKA_BOOTSTRAP_SERVERS=<bootstrap-url>:9092
    KAFKA_API_KEY=<sasl-username>
    KAFKA_API_SECRET=<sasl-password>
    KAFKA_TOPIC=surveillance-alerts          # optional, defaults to this
    NTFY_TOPIC=<your-unique-ntfy-topic>      # e.g. my-surveillance-abc123
"""

import json
import os
import sys

import certifi
import httpx
from confluent_kafka import Consumer, KafkaError
from dotenv import load_dotenv

load_dotenv()

KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "")
KAFKA_API_KEY            = os.getenv("KAFKA_API_KEY", "")
KAFKA_API_SECRET         = os.getenv("KAFKA_API_SECRET", "")
KAFKA_TOPIC              = os.getenv("KAFKA_TOPIC", "surveillance-alerts")
NTFY_TOPIC               = os.getenv("NTFY_TOPIC", "")

NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}"

PRIORITY = {
    "CRITICAL": "urgent",
    "HIGH":     "high",
}


def _check_config():
    missing = [k for k, v in {
        "KAFKA_BOOTSTRAP_SERVERS": KAFKA_BOOTSTRAP_SERVERS,
        "KAFKA_API_KEY":           KAFKA_API_KEY,
        "KAFKA_API_SECRET":        KAFKA_API_SECRET,
        "NTFY_TOPIC":              NTFY_TOPIC,
    }.items() if not v]
    if missing:
        print(f"[error] Missing env vars: {', '.join(missing)}")
        print("Set them in your .env file and restart.")
        sys.exit(1)


def _send_notification(alert: dict):
    severity  = alert.get("severity", "HIGH")
    signal    = alert.get("signal", "unknown").replace("_", " ")
    source    = alert.get("_source", "?")
    market_id = alert.get("market_id", "?")
    desc      = alert.get("description", "")
    fired_at  = alert.get("_fired_at", "")

    trades      = alert.get("triggering_trades", [])
    market_name = (trades[0].get("market_name") if trades else None) or market_id

    title   = f"[{severity}] {signal.upper()} — {source}"
    message = f"{market_name}\n{desc}" + (f"\n{fired_at} UTC" if fired_at else "")

    try:
        resp = httpx.post(
            NTFY_URL,
            content=message.encode("utf-8"),
            headers={
                "Title":    title,
                "Priority": PRIORITY.get(severity, "default"),
                "Tags":     f"warning,{source}",
            },
            timeout=10,
        )
        resp.raise_for_status()
        print(f"[ntfy] sent: {title}")
    except Exception as e:
        print(f"[ntfy] error: {e}")


def main():
    _check_config()

    consumer = Consumer({
        "bootstrap.servers":  KAFKA_BOOTSTRAP_SERVERS,
        "security.protocol":  "SASL_SSL",
        "sasl.mechanism":     "SCRAM-SHA-256",
        "sasl.username":      KAFKA_API_KEY,
        "sasl.password":      KAFKA_API_SECRET,
        "ssl.ca.location":    certifi.where(),
        "group.id":           "surveillance-notifier",
        "auto.offset.reset":  "latest",
    })

    consumer.subscribe([KAFKA_TOPIC])
    print(f"Connecting to Kafka at {KAFKA_BOOTSTRAP_SERVERS} ...")
    print(f"Listening on topic '{KAFKA_TOPIC}' — ntfy topic: {NTFY_TOPIC}")
    print("Waiting for HIGH/CRITICAL alerts... (Ctrl-C to stop)\n")

    try:
        while True:
            msg = consumer.poll(timeout=1.0)
            if msg is None:
                continue
            if msg.error():
                if msg.error().code() != KafkaError._PARTITION_EOF:
                    print(f"[kafka] error: {msg.error()}")
                continue
            alert = json.loads(msg.value().decode("utf-8"))
            _send_notification(alert)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        consumer.close()


if __name__ == "__main__":
    main()
