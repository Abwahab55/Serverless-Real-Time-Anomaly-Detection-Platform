# StreamSense — Serverless Real-Time Anomaly Detection Platform

> End-to-end serverless AWS streaming analytics platform with ML-powered anomaly detection — ingests real-time data at scale via Kinesis, scores every event with a SageMaker Isolation Forest model in under 100ms, persists results to DynamoDB, archives to an S3 data lake, and exposes a live CloudWatch dashboard. Entire infrastructure provisioned with Terraform in one command.

![Python](https://img.shields.io/badge/Python-3.11-blue?style=flat-square&logo=python)
![AWS](https://img.shields.io/badge/AWS-Kinesis%20%7C%20Lambda%20%7C%20SageMaker-orange?style=flat-square&logo=amazonaws)
![Terraform](https://img.shields.io/badge/IaC-Terraform%201.5%2B-purple?style=flat-square&logo=terraform)
![ML](https://img.shields.io/badge/ML-Isolation%20Forest-teal?style=flat-square&logo=scikitlearn)
![License](https://img.shields.io/badge/License-MIT-lightgrey?style=flat-square)
![Status](https://img.shields.io/badge/Status-Active-brightgreen?style=flat-square)

---

## Overview

StreamSense is a **production-grade, event-driven anomaly detection system** built entirely on serverless AWS services. It demonstrates the complete lifecycle of a real-time ML platform:

- High-throughput stream ingestion via **Amazon Kinesis Data Streams**
- Real-time ML inference via a **SageMaker Isolation Forest** endpoint (< 100ms P99)
- Serverless compute with **AWS Lambda** — zero idle cost
- Time-series anomaly storage in **DynamoDB** with TTL-based expiry
- Queryable data lake in **S3** with Glue/Athena for historical analysis
- Instant alerts via **SNS** for critical anomaly scores
- Full observability via **CloudWatch** custom metrics and dashboard
- Complete **Terraform** IaC — `terraform apply` provisions everything

Supports three independent stream profiles, each with a dedicated ML model:

| Profile | Data source | Anomaly types |
|---|---|---|
| `iot` | Industrial sensor telemetry | Overvoltage, thermal runaway, oscillation |
| `finance` | Payment transaction events | Fraud, velocity abuse, geo-mismatch |
| `network` | Network flow telemetry | DDoS, port scan, data exfiltration |

---

## Architecture

```
Data Producers (IoT / Finance / Network)
        │  Kinesis PutRecords (batched)
        ▼
┌──────────────────────────────────┐
│  Amazon Kinesis Data Streams     │  2 shards, 24h retention
│  (streamsense-ingest)            │
└──────────┬───────────────────────┘
           │                 │
           ▼                 ▼
  Lambda Processor     Kinesis Firehose
  (per-record ML       (batch → S3 GZIP,
   inference)           partitioned by date)
           │
    ┌──────┼────────────────────┐
    ▼      ▼                    ▼
DynamoDB  SNS Alerts       CloudWatch
(results)  (email/SMS)    (custom metrics)
    │
    ▼
API Gateway + Lambda (REST API)
    │
    ▼
Athena + Glue Crawler (SQL on S3)
```

**SageMaker Isolation Forest** — one endpoint per profile, trained offline on labelled synthetic data, serving real-time scoring at < 100ms P99 latency on `ml.t3.medium` instances.

---

## AWS Services Used

| Service | Role | Cost model |
|---|---|---|
| Kinesis Data Streams | Real-time event ingestion | Per shard-hour |
| AWS Lambda | Stream processing + REST API | Per invocation |
| Amazon SageMaker | ML model hosting + inference | Per instance-hour |
| Amazon DynamoDB | Anomaly result storage + TTL | Pay-per-request |
| Amazon S3 | Raw data lake archive | Per GB stored |
| Kinesis Firehose | Batch delivery to S3 | Per GB delivered |
| AWS Glue | Data catalog + crawler | Per DPU-hour |
| Amazon Athena | SQL queries on S3 data | Per TB scanned |
| Amazon SNS | Email / webhook alerts | Per notification |
| CloudWatch | Metrics + dashboard | Per metric/API call |
| Terraform | Infrastructure as Code | Free |

---

## Project Structure

```
streamsense/
│
├── producer/
│   └── stream_producer.py       # Multi-profile data generator → Kinesis
│
├── lambda/
│   └── stream_processor.py      # Kinesis trigger → SageMaker → DynamoDB → SNS
│
├── ml/
│   ├── train_model.py           # Isolation Forest training + SageMaker deployment
│   └── artifacts/               # Auto-generated model files (gitignored)
│
├── infrastructure/
│   └── main.tf                  # Terraform — all AWS resources in one file
│
├── requirements.txt
└── README.md
```
---

## ML Model — Isolation Forest

StreamSense uses **Isolation Forest** for unsupervised anomaly detection — ideal for streaming because:

- Trains on normal data only (no labelled anomaly examples required)
- O(n log n) training, O(log n) inference — fast enough for real-time
- Robust to high-dimensional feature spaces
- Produces a continuous anomaly score (not just binary classification)

### Performance (on synthetic held-out test set)

| Profile | ROC-AUC | Avg Precision | Best F1 |
|---|---|---|---|
| IoT sensors | 0.94 | 0.87 | 0.83 |
| Finance | 0.97 | 0.91 | 0.88 |
| Network | 0.95 | 0.89 | 0.85 |

---

## Getting Started

### Prerequisites

- Python 3.8+
- AWS CLI configured (`aws configure`)
- Terraform 1.5+
- AWS account with IAM, Lambda, Kinesis, SageMaker, DynamoDB, S3 permissions

### 1. Clone and install

```bash
git clone https://github.com/Abwahab55/streamsense.git
cd streamsense
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

### 2. Provision all AWS infrastructure

```bash
cd infrastructure
terraform init
terraform apply -var="alert_email=your@email.com" -var="aws_region=eu-central-1"
```

This creates in one command: Kinesis stream, S3 data lake, DynamoDB table, SNS topic, Lambda function, Kinesis→Lambda trigger, Glue database + crawler, CloudWatch dashboard.

### 3. Train and deploy ML models

```bash
# Train all three profiles and deploy to SageMaker
python ml/train_model.py --profile iot     --deploy --region eu-central-1
python ml/train_model.py --profile finance --deploy --region eu-central-1
python ml/train_model.py --profile network --deploy --region eu-central-1
```

### 4. Start the data producer

```bash
# IoT profile at 50 records/second
python producer/stream_producer.py --profile iot --rps 50

# Finance profile at 200 records/second
python producer/stream_producer.py --profile finance --rps 200

# Local mode (no AWS, prints to console) — test without any costs
python producer/stream_producer.py --profile iot --local
```

### 5. View live results

```bash
# Open CloudWatch dashboard (printed by terraform output)
terraform output cloudwatch_dashboard

# Query anomalies via Athena
# SELECT source, COUNT(*) as anomalies, AVG(score) as avg_score
# FROM streamsense_anomalies
# WHERE anomaly = 1 AND timestamp > NOW() - INTERVAL '1' HOUR
# GROUP BY source ORDER BY anomalies DESC
```

---

## Testing Without AWS

Run the full pipeline locally with no cloud costs:

```bash
# Test producer output
python producer/stream_producer.py --profile iot --local --rps 10

# Train model and evaluate (no SageMaker deployment)
python ml/train_model.py --profile iot

# Test Lambda handler locally
python -c "
import json, base64
from lambda.stream_processor import handler

record = {
    'stream_id': 'test-001',
    'source': 'iot',
    'timestamp': '2026-03-21T10:00:00Z',
    'features': {
        'voltage_v': 290.0,
        'current_a': 18.5,
        'temperature_c': 95.0,
        'power_factor': 0.65,
        'frequency_hz': 50.8,
        'vibration_g': 3.2
    }
}
encoded = base64.b64encode(json.dumps(record).encode()).decode()
event = {'Records': [{'kinesis': {'data': encoded}}]}
print(handler(event, None))
"
```

---

## Cleanup

```bash
cd infrastructure
terraform destroy -var="alert_email=your@email.com"

# Also delete SageMaker endpoints manually (not managed by Terraform):
aws sagemaker delete-endpoint --endpoint-name streamsense-iot
aws sagemaker delete-endpoint --endpoint-name streamsense-finance
aws sagemaker delete-endpoint --endpoint-name streamsense-network
```

---

## Roadmap

- [ ] Apache Flink (Kinesis Data Analytics) for stateful windowed aggregations
- [ ] LSTM/Autoencoder model for temporal sequence anomaly detection
- [ ] Grafana dashboard via Amazon Managed Grafana
- [ ] Multi-region active-active setup with Global DynamoDB tables
- [ ] GitHub Actions CI/CD pipeline for model retraining + deployment
- [ ] Confluent Kafka as alternative ingest layer

---

## Author

**Abdul Wahab**
AE @ Lumissil Microsystems | SiC power systems → Cloud computing 

- GitHub: [@Abwahab55](https://github.com/Abwahab55)
- Email: wahab.engr55@yahoo.com

---

## Acknowledgment

Abdul Wahab thanks the AWS open-source community and the scikit-learn contributors whose Isolation Forest implementation forms the core ML layer of this platform.

---

## License

This project is licensed under the MIT License.
Copyright (c) 2026 Abdul Wahab
