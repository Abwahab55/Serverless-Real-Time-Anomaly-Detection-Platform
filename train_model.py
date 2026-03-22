"""
StreamSense — ML Model Training & SageMaker Deployment
Trains an Isolation Forest anomaly detector on synthetic data,
evaluates it, saves the model artifact, and deploys to a
SageMaker real-time inference endpoint.

Usage:
    python ml/train_model.py --profile iot --deploy
    python ml/train_model.py --profile finance --no-deploy --evaluate
"""

import argparse
import json
import os
import pickle
import tarfile
import tempfile
import numpy as np
from datetime import datetime

import boto3
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.metrics import (
    classification_report, roc_auc_score,
    precision_recall_curve, average_precision_score,
)

try:
    import sagemaker
    from sagemaker.sklearn import SKLearnModel
    SAGEMAKER_AVAILABLE = True
except ImportError:
    SAGEMAKER_AVAILABLE = False
    print("[WARN] sagemaker SDK not installed — deployment disabled")


# ── Feature schemas per profile ───────────────────────────────────────────────

FEATURE_SCHEMAS = {
    "iot": [
        "voltage_v", "current_a", "temperature_c",
        "power_factor", "frequency_hz", "vibration_g",
    ],
    "finance": [
        "amount_usd", "latency_ms", "hour_of_day",
        "transactions_1h", "amount_zscore",
        "country_mismatch", "new_device",
    ],
    "network": [
        "bytes_per_sec", "packets_per_sec", "connections",
        "error_rate", "syn_flood_ratio",
        "unique_src_ips", "avg_packet_size", "duration_sec",
    ],
}


# ── Synthetic training data ───────────────────────────────────────────────────

def generate_training_data(profile, n_normal=5000, n_anomaly=250, seed=42):
    """Generates labelled training data matching each stream profile."""
    rng = np.random.default_rng(seed)

    if profile == "iot":
        normal = np.column_stack([
            rng.normal(230, 2, n_normal),
            rng.normal(10, 0.5, n_normal),
            rng.normal(45, 1.5, n_normal),
            rng.normal(0.92, 0.02, n_normal),
            rng.normal(50, 0.05, n_normal),
            np.abs(rng.normal(0.5, 0.1, n_normal)),
        ])
        anomaly = np.column_stack([
            rng.choice([rng.normal(290, 5, n_anomaly), rng.normal(150, 10, n_anomaly)],
                       axis=0).flatten()[:n_anomaly],
            rng.normal(16, 2, n_anomaly),
            rng.normal(80, 5, n_anomaly),
            rng.normal(0.70, 0.05, n_anomaly),
            rng.normal(50, 0.3, n_anomaly),
            rng.normal(2.5, 0.5, n_anomaly),
        ])

    elif profile == "finance":
        normal = np.column_stack([
            rng.exponential(150, n_normal),
            rng.exponential(80, n_normal),
            rng.integers(0, 24, n_normal),
            rng.integers(1, 50, n_normal),
            rng.normal(0, 1, n_normal),
            rng.binomial(1, 0.05, n_normal),
            rng.binomial(1, 0.10, n_normal),
        ])
        anomaly = np.column_stack([
            rng.uniform(5000, 50000, n_anomaly),
            rng.uniform(1, 5, n_anomaly),
            rng.integers(0, 24, n_anomaly),
            rng.integers(100, 300, n_anomaly),
            rng.normal(0, 4, n_anomaly),
            rng.binomial(1, 0.7, n_anomaly),
            rng.binomial(1, 0.6, n_anomaly),
        ])

    elif profile == "network":
        normal = np.column_stack([
            rng.exponential(50000, n_normal),
            rng.integers(10, 5000, n_normal),
            rng.integers(1, 100, n_normal),
            rng.uniform(0, 0.05, n_normal),
            rng.uniform(0, 0.10, n_normal),
            rng.integers(1, 20, n_normal),
            rng.integers(200, 1500, n_normal),
            rng.exponential(30, n_normal),
        ])
        anomaly = np.column_stack([
            rng.uniform(500000, 5000000, n_anomaly),
            rng.integers(10000, 100000, n_anomaly),
            rng.integers(500, 5000, n_anomaly),
            rng.uniform(0.5, 1.0, n_anomaly),
            rng.uniform(0.6, 1.0, n_anomaly),
            rng.integers(200, 1000, n_anomaly),
            rng.integers(20, 100, n_anomaly),
            rng.exponential(5, n_anomaly),
        ])

    X = np.vstack([normal, anomaly])
    y = np.array([0] * n_normal + [1] * n_anomaly)
    idx = rng.permutation(len(X))
    return X[idx], y[idx]


# ── Model training ────────────────────────────────────────────────────────────

def train(profile, contamination=0.05):
    print(f"\n[Train] Profile: {profile}")
    X, y = generate_training_data(profile)
    X_train = X[y == 0]

    pipeline = Pipeline([
        ("scaler", StandardScaler()),
        ("detector", IsolationForest(
            n_estimators=200,
            contamination=contamination,
            max_features=1.0,
            bootstrap=True,
            random_state=42,
            n_jobs=-1,
        )),
    ])

    print(f"[Train] Fitting on {len(X_train):,} normal samples...")
    pipeline.fit(X_train)

    raw_scores = pipeline.decision_function(X)
    preds = (pipeline.predict(X) == -1).astype(int)

    auc   = roc_auc_score(y, -raw_scores)
    ap    = average_precision_score(y, -raw_scores)
    prec, rec, _ = precision_recall_curve(y, -raw_scores)
    f1_scores = 2 * prec * rec / (prec + rec + 1e-8)
    best_f1 = float(np.max(f1_scores))

    print(f"\n[Metrics] ROC-AUC={auc:.4f} | Avg Precision={ap:.4f} | Best F1={best_f1:.4f}")
    print("\n[Report]\n", classification_report(y, preds, target_names=["normal", "anomaly"]))

    metadata = {
        "profile":       profile,
        "features":      FEATURE_SCHEMAS[profile],
        "trained_at":    datetime.utcnow().isoformat(),
        "n_train":       len(X_train),
        "contamination": contamination,
        "metrics": {
            "roc_auc":         round(auc, 4),
            "avg_precision":   round(ap, 4),
            "best_f1":         round(best_f1, 4),
        },
    }

    return pipeline, metadata


# ── Save artifact ─────────────────────────────────────────────────────────────

def save_artifact(pipeline, metadata, output_dir="ml/artifacts"):
    os.makedirs(output_dir, exist_ok=True)
    profile = metadata["profile"]

    model_path = os.path.join(output_dir, f"model_{profile}.pkl")
    meta_path  = os.path.join(output_dir, f"metadata_{profile}.json")

    with open(model_path, "wb") as f:
        pickle.dump(pipeline, f)
    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2)

    tar_path = os.path.join(output_dir, f"model_{profile}.tar.gz")
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(model_path, arcname="model.pkl")
        tar.add(meta_path,  arcname="metadata.json")

    print(f"[Save] Artifact: {tar_path}")
    return tar_path


# ── SageMaker deployment ──────────────────────────────────────────────────────

INFERENCE_SCRIPT = '''
import pickle
import json
import numpy as np
import os

def model_fn(model_dir):
    with open(os.path.join(model_dir, "model.pkl"), "rb") as f:
        return pickle.load(f)

def input_fn(request_body, content_type="application/json"):
    data = json.loads(request_body)
    features = data.get("features", data)
    if isinstance(features, dict):
        keys = sorted(features.keys())
        return np.array([[features[k] for k in keys]])
    return np.array(features)

def predict_fn(input_data, model):
    score = float(-model.decision_function(input_data)[0])
    pred  = int(model.predict(input_data)[0] == -1)
    return {"anomaly": pred, "score": round(score, 6), "threshold": 0.0}

def output_fn(prediction, accept="application/json"):
    return json.dumps(prediction), "application/json"
'''


def deploy_to_sagemaker(tar_path, profile, region="eu-central-1", instance="ml.t3.medium"):
    if not SAGEMAKER_AVAILABLE:
        print("[Deploy] sagemaker SDK not available — skipping.")
        return None

    sess       = sagemaker.Session(boto3.Session(region_name=region))
    role       = sagemaker.get_execution_role()
    bucket     = sess.default_bucket()
    s3_key     = f"streamsense/models/{profile}/model.tar.gz"

    print(f"[Deploy] Uploading model to s3://{bucket}/{s3_key}")
    boto3.client("s3", region_name=region).upload_file(tar_path, bucket, s3_key)

    script_path = f"/tmp/inference_{profile}.py"
    with open(script_path, "w") as f:
        f.write(INFERENCE_SCRIPT)

    model = SKLearnModel(
        model_data=f"s3://{bucket}/{s3_key}",
        role=role,
        entry_point=script_path,
        framework_version="1.2-1",
        sagemaker_session=sess,
    )

    endpoint_name = f"streamsense-{profile}-{datetime.utcnow().strftime('%Y%m%d%H%M')}"
    print(f"[Deploy] Deploying endpoint: {endpoint_name} on {instance}...")
    predictor = model.deploy(
        initial_instance_count=1,
        instance_type=instance,
        endpoint_name=endpoint_name,
    )
    print(f"[Deploy] Endpoint live: {endpoint_name}")
    return endpoint_name


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="StreamSense — Model Training")
    parser.add_argument("--profile",      choices=["iot", "finance", "network"], default="iot")
    parser.add_argument("--contamination",type=float, default=0.05)
    parser.add_argument("--deploy",       action="store_true")
    parser.add_argument("--region",       default="eu-central-1")
    parser.add_argument("--instance",     default="ml.t3.medium")
    args = parser.parse_args()

    pipeline, metadata = train(args.profile, args.contamination)
    tar_path = save_artifact(pipeline, metadata)

    if args.deploy:
        endpoint = deploy_to_sagemaker(tar_path, args.profile, args.region, args.instance)
        if endpoint:
            metadata["endpoint_name"] = endpoint
            with open(f"ml/artifacts/metadata_{args.profile}.json", "w") as f:
                json.dump(metadata, f, indent=2)
            print(f"\n[Done] Endpoint: {endpoint}")
    else:
        print("\n[Done] Model saved locally. Use --deploy to push to SageMaker.")


if __name__ == "__main__":
    main()
