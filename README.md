

# StreamSense — Real-Time Anomaly Detection Platform

A serverless, cloud-native platform for real-time anomaly detection across IoT, financial, and network data streams using Amazon Kinesis, SageMaker, and DynamoDB.

![Python](https://img.shields.io/badge/Python-3.11-blue?style=flat-square&logo=python)
![AWS](https://img.shields.io/badge/AWS-Kinesis%20%7C%20Lambda%20%7C%20SageMaker-orange?style=flat-square&logo=amazonaws)
![Terraform](https://img.shields.io/badge/IaC-Terraform%201.5%2B-purple?style=flat-square&logo=terraform)
![ML](https://img.shields.io/badge/ML-Isolation%20Forest-teal?style=flat-square&logo=scikitlearn)
![License](https://img.shields.io/badge/License-MIT-lightgrey?style=flat-square)
![Status](https://img.shields.io/badge/Status-Active-brightgreen?style=flat-square)


## Features

### Multi-Domain Anomaly Detection
- **IoT**: Industrial sensor telemetry (voltage, current, temperature, vibration)
- **Finance**: Transaction fraud detection (amounts, latency, merchant patterns)
- **Network**: DDoS/intrusion detection (traffic flows, SYN floods, packet analysis)

### Architecture
- **Stream Ingestion**: Amazon Kinesis Data Streams for high-throughput real-time data
- **ML Models**: Isolation Forest ensemble trained per domain profile
- **Inference**: AWS Lambda + SageMaker real-time endpoints
- **Storage**: DynamoDB for results + CloudWatch metrics
- **Alerting**: SNS for critical anomaly notifications

### Synthetic Data Generation
- Configurable anomaly injection rates
- Realistic fault patterns (spikes, dropouts, drifts)
- Multiple concurrent producer profiles

## Quick Start (Docker)

### Build the Docker Image
```bash
docker build -t streamsense:latest .
```

### Train All Models
```bash
docker run --rm streamsense:latest python3 test_local.py
```

**Output:**
- `ml/artifacts/model_iot.tar.gz` — IoT anomaly detector
- `ml/artifacts/model_finance.tar.gz` — Finance fraud detector
- `ml/artifacts/model_network.tar.gz` — Network intrusion detector

### Run Data Producer (Local Mode)
```bash
# IoT profile @ 50 records/sec
docker run --rm streamsense:latest python3 stream_producer.py --profile iot --rps 50 --local

# Finance profile @ 200 records/sec
docker run --rm streamsense:latest python3 stream_producer.py --profile finance --rps 200 --local

# Network profile @ 100 records/sec
docker run --rm streamsense:latest python3 stream_producer.py --profile network --rps 100 --local
```

## Local Development Setup

### Prerequisites
- Python 3.11+
- pip/venv or conda

### Installation
```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### Train Models Locally
```bash
# Train IoT model
python3 train_model.py --profile iot

# Train Finance model
python3 train_model.py --profile finance

# Train Network model
python3 train_model.py --profile network

# Train and deploy to SageMaker (requires AWS credentials)
python3 train_model.py --profile iot --deploy --region eu-central-1
```

### Run Data Producer Locally
```bash
# Local testing (prints anomalies to console)
python3 stream_producer.py --profile iot --rps 50 --local

# Stream to Kinesis (requires AWS credentials + Kinesis stream)
python3 stream_producer.py --profile iot --rps 50 \
  --stream streamsense-ingest --region eu-central-1
```

## AWS Deployment

### Prerequisites
- AWS Account with appropriate IAM permissions
- Kinesis Data Streams, SageMaker, DynamoDB, Lambda, SNS, CloudWatch access
- configured `~/.aws/credentials` or environment variables

### 1. Create Infrastructure (Terraform)
```bash
terraform init
terraform apply -var="region=eu-central-1" -var="environment=production"
```

### 2. Train and Deploy Models
```bash
python3 train_model.py --profile iot --deploy --region eu-central-1
python3 train_model.py --profile finance --deploy --region eu-central-1
python3 train_model.py --profile network --deploy --region eu-central-1
```

### 3. Start Stream Producer
```bash
python3 stream_producer.py --profile iot --rps 50 --stream streamsense-ingest
```

### 4. Deploy Stream Processor Lambda
Package `stream_processor.py` and deploy to AWS Lambda with:
- Trigger: Kinesis Data Streams batch
- Environment variables:
  - `SAGEMAKER_ENDPOINT_IOT=streamsense-iot-xxxxxx`
  - `SAGEMAKER_ENDPOINT_FINANCE=streamsense-finance-xxxxxx`
  - `SAGEMAKER_ENDPOINT_NETWORK=streamsense-network-xxxxxx`
  - `DYNAMODB_TABLE=streamsense-anomalies`
  - `SNS_TOPIC_ARN=arn:aws:sns:...`
  - `ANOMALY_SCORE_THRESHOLD=0.3`

## Project Structure

```
.
├── train_model.py              # ML model training and SageMaker deployment
├── stream_producer.py          # Synthetic data generation (local + Kinesis)
├── stream_processor.py         # Lambda handler (inference + storage + alerts)
├── test_local.py               # Local training test suite
├── requirements.txt            # Python dependencies
├── main.tf                     # Terraform infrastructure-as-code
├── Dockerfile                  # Containerized environment
└── ml/
    └── artifacts/              # Trained models and metadata
```

## Model Performance

Training on 5,250 samples (5,000 normal + 250 anomalies):

| Profile | ROC-AUC | Avg Precision | Best F1 |
|---------|---------|---------------|---------|
| IoT     | 1.0000  | 1.0000        | 1.0000  |
| Finance | 0.9981  | 0.9669        | 0.8990  |
| Network | 1.0000  | 1.0000        | 1.0000  |

## Environment Variables

### Training
- `CONTAMINATION` — anomaly rate (default: 0.05)

### Producer
- `--profile {iot, finance, network}` — data domain
- `--rps N` — records per second (default: 50)
- `--stream NAME` — Kinesis stream name (default: streamsense-ingest)
- `--region REGION` — AWS region (default: eu-central-1)
- `--local` — enable local mode (prints to console, no AWS)

### Processor Lambda
- `SAGEMAKER_ENDPOINT_IOT` — IoT inference endpoint
- `SAGEMAKER_ENDPOINT_FINANCE` — Finance inference endpoint
- `SAGEMAKER_ENDPOINT_NETWORK` — Network inference endpoint
- `DYNAMODB_TABLE` — Results table (default: streamsense-anomalies)
- `SNS_TOPIC_ARN` — Critical alert topic
- `ANOMALY_SCORE_THRESHOLD` — alert threshold (default: 0.3)

## Testing

### Unit Tests
```bash
docker run --rm streamsense:latest python3 test_local.py
```

### Integration Tests (AWS)
```bash
# Requires live Kinesis stream, DynamoDB table, SageMaker endpoints
python3 -m pytest tests/ -v --aws
```

## Troubleshooting

### Model not found
Ensure `ml/artifacts/` contains `.tar.gz` files after training.

### SageMaker deployment fails
- Check IAM role has `sagemaker:` and `s3:` permissions
- Verify endpoint name format: `streamsense-{profile}-YYYYMMDDHHmm`

### Lambda timeout on Kinesis data
- Increase timeout limit to 60+ seconds
- Check SageMaker endpoint is warm and responding

### Anomaly scores always 0
- Verify features match schema in `train_model.py` `FEATURE_SCHEMAS`
- Check inference script payload format in `deploy_to_sagemaker()`

## License

See [LICENSE](LICENSE) file.

## References

- [AWS Kinesis Developer Guide](https://docs.aws.amazon.com/kinesis/)
- [SageMaker Real-Time Inference](https://docs.aws.amazon.com/sagemaker/latest/dg/realtime-endpoints.html)
- [Isolation Forest Paper](https://cs.nju.edu.cn/zhouzh/zhouzh.files/publication/icdm08.pdf)
