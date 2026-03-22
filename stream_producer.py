"""
StreamSense — Real-Time Data Producer
Simulates multiple IoT/financial/network data sources and publishes
records to Amazon Kinesis Data Streams at configurable throughput.

Supports three stream profiles:
  - iot        : industrial sensor telemetry (voltage, current, temp)
  - finance    : transaction events (amount, latency, fraud signals)
  - network    : packet/flow telemetry (bytes, connections, error rates)

Usage:
    python producer/stream_producer.py --profile iot --rps 50
    python producer/stream_producer.py --profile finance --rps 200 --local
"""

import json
import time
import random
import uuid
import argparse
import math
from datetime import datetime, timezone

try:
    import boto3
    AWS_AVAILABLE = True
except ImportError:
    AWS_AVAILABLE = False


# ── Data Generators ───────────────────────────────────────────────────────────

class IoTSensorGenerator:
    """Industrial sensor telemetry with realistic drift and fault injection."""

    def __init__(self):
        self.t = 0
        self.device_ids = [f"sensor-{i:03d}" for i in range(1, 11)]

    def _anomaly(self, prob=0.04):
        return random.random() < prob

    def generate(self):
        self.t += 1
        device = random.choice(self.device_ids)
        anomaly = self._anomaly()

        base_voltage  = 230.0 + 5 * math.sin(self.t / 50)
        base_current  = 10.0  + 2 * math.sin(self.t / 30 + 1)
        base_temp     = 45.0  + 3 * math.sin(self.t / 80)

        if anomaly:
            fault = random.choice(["spike", "dropout", "drift", "oscillation"])
            if fault == "spike":
                base_voltage *= random.uniform(1.2, 1.4)
                base_current *= random.uniform(1.3, 1.6)
            elif fault == "dropout":
                base_voltage *= random.uniform(0.5, 0.7)
            elif fault == "drift":
                base_temp   += random.uniform(20, 40)
            elif fault == "oscillation":
                base_voltage += 30 * math.sin(self.t * 0.8)
        else:
            fault = None

        return {
            "stream_id":    str(uuid.uuid4()),
            "source":       "iot",
            "device_id":    device,
            "timestamp":    datetime.now(timezone.utc).isoformat(),
            "features": {
                "voltage_v":      round(base_voltage + random.gauss(0, 0.5), 3),
                "current_a":      round(base_current  + random.gauss(0, 0.1), 3),
                "temperature_c":  round(base_temp     + random.gauss(0, 0.3), 2),
                "power_factor":   round(random.gauss(0.92, 0.02), 4),
                "frequency_hz":   round(50.0 + random.gauss(0, 0.05), 3),
                "vibration_g":    round(abs(random.gauss(0.5, 0.1 if not anomaly else 0.8)), 3),
            },
            "label":        int(anomaly),
            "fault_type":   fault,
        }


class FinancialGenerator:
    """Transaction stream with fraud injection."""

    def __init__(self):
        self.merchant_ids = [f"merch-{i:04d}" for i in range(1, 501)]

    def generate(self):
        fraud = random.random() < 0.03
        amount = (
            random.uniform(5000, 50000) if fraud
            else random.expovariate(1 / 150)
        )
        return {
            "stream_id":    str(uuid.uuid4()),
            "source":       "finance",
            "timestamp":    datetime.now(timezone.utc).isoformat(),
            "features": {
                "amount_usd":          round(amount, 2),
                "latency_ms":          round(random.expovariate(1/80) if not fraud else random.uniform(1, 5), 1),
                "merchant_id":         random.choice(self.merchant_ids),
                "hour_of_day":         datetime.now().hour,
                "transactions_1h":     random.randint(1, 50 if not fraud else 200),
                "amount_zscore":       round(random.gauss(0, 1 if not fraud else 4), 3),
                "country_mismatch":    int(fraud and random.random() < 0.7),
                "new_device":          int(fraud and random.random() < 0.6),
            },
            "label":        int(fraud),
            "fault_type":   "fraud" if fraud else None,
        }


class NetworkGenerator:
    """Network flow telemetry with DDoS/intrusion injection."""

    def __init__(self):
        self.src_ips = [f"192.168.{random.randint(0,255)}.{random.randint(1,254)}" for _ in range(100)]

    def generate(self):
        attack = random.random() < 0.05
        return {
            "stream_id":    str(uuid.uuid4()),
            "source":       "network",
            "timestamp":    datetime.now(timezone.utc).isoformat(),
            "features": {
                "bytes_per_sec":       round(random.expovariate(1/50000) * (20 if attack else 1), 1),
                "packets_per_sec":     random.randint(10, 50000 if attack else 5000),
                "connections":         random.randint(1, 1000 if attack else 100),
                "error_rate":          round(random.uniform(0, 0.8 if attack else 0.05), 4),
                "syn_flood_ratio":     round(random.uniform(0.5, 1.0) if attack else random.uniform(0, 0.1), 4),
                "unique_src_ips":      random.randint(1, 500 if attack else 20),
                "avg_packet_size":     random.randint(20 if attack else 200, 1500),
                "duration_sec":        round(random.expovariate(1/30), 2),
            },
            "label":        int(attack),
            "fault_type":   "ddos" if attack else None,
        }


GENERATORS = {
    "iot":     IoTSensorGenerator,
    "finance": FinancialGenerator,
    "network": NetworkGenerator,
}


# ── Kinesis Publisher ─────────────────────────────────────────────────────────

class KinesisProducer:

    def __init__(self, stream_name, region="eu-central-1"):
        self.stream_name = stream_name
        self.client = boto3.client("kinesis", region_name=region)
        self._batch = []

    def send(self, record):
        self._batch.append({
            "Data":         json.dumps(record).encode(),
            "PartitionKey": record.get("device_id", record["stream_id"]),
        })
        if len(self._batch) >= 500:
            self._flush()

    def _flush(self):
        if not self._batch:
            return
        resp = self.client.put_records(
            StreamName=self.stream_name,
            Records=self._batch,
        )
        failed = resp.get("FailedRecordCount", 0)
        if failed:
            print(f"[Kinesis] WARNING: {failed} records failed")
        self._batch = []

    def close(self):
        self._flush()


# ── Main ──────────────────────────────────────────────────────────────────────

def run(profile, rps, stream_name, region, local_mode):
    generator = GENERATORS[profile]()
    producer  = None if local_mode else KinesisProducer(stream_name, region)

    interval  = 1.0 / rps
    sent = anomalies = 0
    start = time.time()

    print(f"[StreamSense] Profile={profile} | RPS={rps} | Mode={'LOCAL' if local_mode else 'AWS'}")
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            record = generator.generate()
            if local_mode:
                if record["label"]:
                    anomalies += 1
                    print(f"[ANOMALY] {record['source']} | {record['fault_type']} | {json.dumps(record['features'])}")
            else:
                producer.send(record)
                if record["label"]:
                    anomalies += 1

            sent += 1
            if sent % 1000 == 0:
                elapsed = time.time() - start
                print(f"[{profile}] Sent={sent:,} | Anomalies={anomalies} ({100*anomalies/sent:.1f}%) | "
                      f"Actual RPS={sent/elapsed:.0f}")

            time.sleep(interval)

    except KeyboardInterrupt:
        if producer:
            producer.close()
        elapsed = time.time() - start
        print(f"\n[Done] Sent={sent:,} | Anomalies={anomalies} | Duration={elapsed:.1f}s")


def main():
    parser = argparse.ArgumentParser(description="StreamSense — Data Producer")
    parser.add_argument("--profile",     choices=["iot", "finance", "network"], default="iot")
    parser.add_argument("--rps",         type=int, default=50, help="Records per second")
    parser.add_argument("--stream",      default="streamsense-ingest")
    parser.add_argument("--region",      default="eu-central-1")
    parser.add_argument("--local",       action="store_true")
    args = parser.parse_args()

    if not args.local and not AWS_AVAILABLE:
        print("[WARN] boto3 not available — switching to local mode")
        args.local = True

    run(args.profile, args.rps, args.stream, args.region, args.local)


if __name__ == "__main__":
    main()
