"""
StreamSense — Lambda Stream Processor
Triggered by Kinesis Data Streams. For each batch of records:
  1. Decode and parse records
  2. Extract feature vectors
  3. Call SageMaker endpoint for ML inference
  4. Store anomaly results to DynamoDB
  5. Publish high-severity anomalies to SNS
  6. Emit custom CloudWatch metrics

Environment variables required:
  SAGEMAKER_ENDPOINT_IOT      — endpoint name for IoT profile
  SAGEMAKER_ENDPOINT_FINANCE  — endpoint name for finance profile
  SAGEMAKER_ENDPOINT_NETWORK  — endpoint name for network profile
  DYNAMODB_TABLE              — DynamoDB table name
  SNS_TOPIC_ARN               — SNS topic ARN for critical alerts
    import base64
    import json
    import os
    import time
    import boto3
    from datetime import datetime, timezone
    from decimal import Decimal

    sagemaker_rt = boto3.client("sagemaker-runtime")
    dynamodb     = boto3.resource("dynamodb")
    sns          = boto3.client("sns")
    cloudwatch   = boto3.client("cloudwatch")

    TABLE_NAME  = os.environ.get("DYNAMODB_TABLE", "streamsense-anomalies")
    SNS_ARN     = os.environ.get("SNS_TOPIC_ARN", "")
    THRESHOLD   = float(os.environ.get("ANOMALY_SCORE_THRESHOLD", "0.3"))

    ENDPOINTS = {
        "iot":     os.environ.get("SAGEMAKER_ENDPOINT_IOT",     "streamsense-iot"),
        "finance": os.environ.get("SAGEMAKER_ENDPOINT_FINANCE", "streamsense-finance"),
        "network": os.environ.get("SAGEMAKER_ENDPOINT_NETWORK", "streamsense-network"),
    }

    table = dynamodb.Table(TABLE_NAME)


    def to_decimal(obj):
        if isinstance(obj, float):
            return Decimal(str(round(obj, 6)))
        if isinstance(obj, dict):
            return {k: to_decimal(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [to_decimal(i) for i in obj]
        return obj


    def call_sagemaker(endpoint: str, features: dict) -> dict:
        payload = json.dumps({"features": features})
        try:
            resp = sagemaker_rt.invoke_endpoint(
                EndpointName=endpoint,
                ContentType="application/json",
                Body=payload,
            )
            return json.loads(resp["Body"].read())
        except Exception as e:
            print(f"[SageMaker] ERROR on {endpoint}: {e}")
            return {"anomaly": 0, "score": 0.0, "error": str(e)}


    def store_result(record: dict, inference: dict):
        item = to_decimal({
            "stream_id":    record["stream_id"],
            "timestamp":    record["timestamp"],
            "source":       record.get("source", "unknown"),
            "device_id":    record.get("device_id", ""),
            "anomaly":      inference["anomaly"],
            "score":        inference.get("score", 0.0),
            "features":     record.get("features", {}),
            "fault_type":   record.get("fault_type"),
            "processed_at": datetime.now(timezone.utc).isoformat(),
            "ttl":          int(time.time()) + 7 * 24 * 3600,
        })
        table.put_item(Item=item)


    def emit_metric(source: str, is_anomaly: bool, score: float, latency_ms: float):
        cloudwatch.put_metric_data(
            Namespace="StreamSense",
            MetricData=[
                {
                    "MetricName": "AnomalyDetected",
                    "Dimensions": [{"Name": "Source", "Value": source}],
                    "Value": int(is_anomaly),
                    "Unit": "Count",
                },
                {
                    "MetricName": "AnomalyScore",
                    "Dimensions": [{"Name": "Source", "Value": source}],
                    "Value": score,
                    "Unit": "None",
                },
                {
                    "MetricName": "InferenceLatencyMs",
                    "Dimensions": [{"Name": "Source", "Value": source}],
                    "Value": latency_ms,
                    "Unit": "Milliseconds",
                },
            ],
        )


    def send_alert(record: dict, inference: dict):
        if not SNS_ARN:
            return
        score  = inference.get("score", 0)
        source = record.get("source", "unknown")
        msg = (
            f"StreamSense — Critical Anomaly Detected\n"
            f"{'='*45}\n"
            f"Source:     {source}\n"
            f"Stream ID:  {record['stream_id']}\n"
            f"Timestamp:  {record['timestamp']}\n"
            f"Score:      {score:.4f}  (threshold={THRESHOLD})\n"
            f"Fault type: {record.get('fault_type', 'unknown')}\n\n"
            f"Features:\n{json.dumps(record.get('features', {}), indent=2)}"
        )
        sns.publish(
            TopicArn=SNS_ARN,
            Subject=f"[CRITICAL] StreamSense anomaly — {source}",
            Message=msg,
        )


    def handler(event, context):
        records = event.get("Records", [])
        print(f"[Lambda] Processing {len(records)} Kinesis records")

        processed = anomalies = errors = 0

        for rec in records:
            try:
                raw  = base64.b64decode(rec["kinesis"]["data"]).decode("utf-8")
                data = json.loads(raw)
            except Exception as e:
                print(f"[Lambda] Decode error: {e}")
                errors += 1
                continue

            source   = data.get("source", "iot")
            features = data.get("features", {})
            endpoint = ENDPOINTS.get(source, ENDPOINTS["iot"])

            t0 = time.time()
            inference = call_sagemaker(endpoint, features)
            latency   = (time.time() - t0) * 1000

            is_anomaly = bool(inference.get("anomaly", 0))
            score      = float(inference.get("score", 0.0))

            store_result(data, inference)
            emit_metric(source, is_anomaly, score, latency)

            if is_anomaly and score > THRESHOLD * 2:
                send_alert(data, inference)

            processed += 1
            if is_anomaly:
                anomalies += 1

        print(f"[Lambda] Done — processed={processed}, anomalies={anomalies}, errors={errors}")
        return {
            "statusCode": 200,
            "body": json.dumps({
                "processed": processed,
                "anomalies": anomalies,
                "errors":    errors,
            }),
        }
  ANOMALY_SCORE_THRESHOLD     — float threshold (default 0.3)
"""

import base64
import json
import os
import time
import uuid
import boto3
from datetime import datetime, timezone
from decimal import Decimal

sagemaker_rt = boto3.client("sagemaker-runtime")
dynamodb     = boto3.resource("dynamodb")
sns          = boto3.client("sns")
cloudwatch   = boto3.client("cloudwatch")

TABLE_NAME  = os.environ.get("DYNAMODB_TABLE", "streamsense-anomalies")
SNS_ARN     = os.environ.get("SNS_TOPIC_ARN", "")
THRESHOLD   = float(os.environ.get("ANOMALY_SCORE_THRESHOLD", "0.3"))

ENDPOINTS = {
    "iot":     os.environ.get("SAGEMAKER_ENDPOINT_IOT",     "streamsense-iot"),
    "finance": os.environ.get("SAGEMAKER_ENDPOINT_FINANCE", "streamsense-finance"),
    "network": os.environ.get("SAGEMAKER_ENDPOINT_NETWORK", "streamsense-network"),
}

table = dynamodb.Table(TABLE_NAME)


def to_decimal(obj):
    if isinstance(obj, float):
        return Decimal(str(round(obj, 6)))
    if isinstance(obj, dict):
        return {k: to_decimal(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [to_decimal(i) for i in obj]
    return obj


def call_sagemaker(endpoint, features: dict) -> dict:
    payload = json.dumps({"features": features})
    try:
        resp = sagemaker_rt.invoke_endpoint(
            EndpointName=endpoint,
            ContentType="application/json",
            Body=payload,
        )
        return json.loads(resp["Body"].read())
    except Exception as e:
        print(f"[SageMaker] ERROR on {endpoint}: {e}")
        return {"anomaly": 0, "score": 0.0, "error": str(e)}


def store_result(record: dict, inference: dict):
    item = to_decimal({
        "stream_id":    record["stream_id"],
        "timestamp":    record["timestamp"],
        "source":       record.get("source", "unknown"),
        "device_id":    record.get("device_id", ""),
        "anomaly":      inference["anomaly"],
        "score":        inference.get("score", 0.0),
        "features":     record.get("features", {}),
        "fault_type":   record.get("fault_type"),
        "processed_at": datetime.now(timezone.utc).isoformat(),
        "ttl":          int(time.time()) + 7 * 24 * 3600,
    })
    table.put_item(Item=item)


def emit_metric(source: str, is_anomaly: bool, score: float, latency_ms: float):
    cloudwatch.put_metric_data(
        Namespace="StreamSense",
        MetricData=[
            {
                "MetricName": "AnomalyDetected",
                "Dimensions": [{"Name": "Source", "Value": source}],
                "Value": int(is_anomaly),
                "Unit": "Count",
            },
            {
                "MetricName": "AnomalyScore",
                "Dimensions": [{"Name": "Source", "Value": source}],
                "Value": score,
                "Unit": "None",
            },
            {
                "MetricName": "InferenceLatencyMs",
                "Dimensions": [{"Name": "Source", "Value": source}],
                "Value": latency_ms,
                "Unit": "Milliseconds",
            },
        ],
    )


def send_alert(record: dict, inference: dict):
    if not SNS_ARN:
        return
    score  = inference.get("score", 0)
    source = record.get("source", "unknown")
    msg = (
        f"StreamSense — Critical Anomaly Detected\n"
        f"{'='*45}\n"
        f"Source:     {source}\n"
        f"Stream ID:  {record['stream_id']}\n"
        f"Timestamp:  {record['timestamp']}\n"
        f"Score:      {score:.4f}  (threshold={THRESHOLD})\n"
        f"Fault type: {record.get('fault_type', 'unknown')}\n\n"
        f"Features:\n{json.dumps(record.get('features', {}), indent=2)}"
    )
    sns.publish(
        TopicArn=SNS_ARN,
        Subject=f"[CRITICAL] StreamSense anomaly — {source}",
        Message=msg,
    )


def handler(event, context):
    records = event.get("Records", [])
    print(f"[Lambda] Processing {len(records)} Kinesis records")

    processed = anomalies = errors = 0

    for rec in records:
        try:
            raw  = base64.b64decode(rec["kinesis"]["data"]).decode("utf-8")
            data = json.loads(raw)
        except Exception as e:
            print(f"[Lambda] Decode error: {e}")
            errors += 1
            continue

        source   = data.get("source", "iot")
        features = data.get("features", {})
        endpoint = ENDPOINTS.get(source, ENDPOINTS["iot"])

        t0 = time.time()
        inference = call_sagemaker(endpoint, features)
        latency   = (time.time() - t0) * 1000

        is_anomaly = bool(inference.get("anomaly", 0))
        score      = float(inference.get("score", 0.0))

        store_result(data, inference)
        emit_metric(source, is_anomaly, score, latency)

        if is_anomaly and score > THRESHOLD * 2:
            send_alert(data, inference)

        processed += 1
        if is_anomaly:
            anomalies += 1

    print(f"[Lambda] Done — processed={processed}, anomalies={anomalies}, errors={errors}")
    return {
        "statusCode": 200,
        "body": json.dumps({
            "processed": processed,
            "anomalies": anomalies,
            "errors":    errors,
        }),
    }
