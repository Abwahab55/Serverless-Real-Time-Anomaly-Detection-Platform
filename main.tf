# StreamSense — Terraform Infrastructure
# Provisions all AWS resources in a single apply.
#
# Usage:
#   terraform init
#   terraform plan -var="alert_email=your@email.com"
#   terraform apply -var="alert_email=your@email.com"
#   terraform destroy

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# ── Variables ─────────────────────────────────────────────────────────────────

variable "aws_region"    { default = "eu-central-1" }
variable "project"       { default = "streamsense" }
variable "alert_email"   { default = "" }
variable "environment"   { default = "dev" }
variable "retention_days"{ default = 7 }

locals {
  tags = {
    Project     = var.project
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

# ── Kinesis Data Stream ───────────────────────────────────────────────────────

resource "aws_kinesis_stream" "ingest" {
  name             = "${var.project}-ingest"
  shard_count      = 2
  retention_period = 24

  stream_mode_details {
    stream_mode = "PROVISIONED"
  }

  tags = local.tags
}

# ── Kinesis Firehose → S3 ─────────────────────────────────────────────────────

resource "aws_s3_bucket" "data_lake" {
  bucket        = "${var.project}-data-lake-${data.aws_caller_identity.current.account_id}"
  force_destroy = true
  tags          = local.tags
}

resource "aws_s3_bucket_versioning" "data_lake" {
  bucket = aws_s3_bucket.data_lake.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_lifecycle_configuration" "data_lake" {
  bucket = aws_s3_bucket.data_lake.id
  rule {
    id     = "archive-raw"
    status = "Enabled"
    transition {
      days          = 30
      storage_class = "STANDARD_IA"
    }
    transition {
      days          = 90
      storage_class = "GLACIER"
    }
  }
}

resource "aws_kinesis_firehose_delivery_stream" "to_s3" {
  name        = "${var.project}-firehose"
  destination = "extended_s3"

  extended_s3_configuration {
    role_arn            = aws_iam_role.firehose.arn
    bucket_arn          = aws_s3_bucket.data_lake.arn
    prefix              = "raw/year=!{timestamp:yyyy}/month=!{timestamp:MM}/day=!{timestamp:dd}/"
    error_output_prefix = "errors/!{firehose:error-output-type}/"
    buffering_size      = 64
    buffering_interval  = 60
    compression_format  = "GZIP"
  }

  tags = local.tags
}

# ── DynamoDB (anomaly results) ────────────────────────────────────────────────

resource "aws_dynamodb_table" "anomalies" {
  name           = "${var.project}-anomalies"
  billing_mode   = "PAY_PER_REQUEST"
  hash_key       = "stream_id"
  range_key      = "timestamp"

  attribute {
    name = "stream_id"
    type = "S"
  }
  attribute {
    name = "timestamp"
    type = "S"
  }
  attribute {
    name = "source"
    type = "S"
  }

  global_secondary_index {
    name            = "source-timestamp-index"
    hash_key        = "source"
    range_key       = "timestamp"
    projection_type = "ALL"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  point_in_time_recovery { enabled = true }
  tags = local.tags
}

# ── SNS Topic ─────────────────────────────────────────────────────────────────

resource "aws_sns_topic" "alerts" {
  name = "${var.project}-alerts"
  tags = local.tags
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alert_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# ── IAM Role — Lambda ─────────────────────────────────────────────────────────

data "aws_caller_identity" "current" {}

resource "aws_iam_role" "lambda" {
  name = "${var.project}-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.tags
}

resource "aws_iam_role_policy_attachment" "lambda_basic" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "lambda_custom" {
  name = "${var.project}-lambda-policy"
  role = aws_iam_role.lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["kinesis:GetRecords", "kinesis:GetShardIterator",
                    "kinesis:DescribeStream", "kinesis:ListStreams",
                    "kinesis:ListShards"]
        Resource = aws_kinesis_stream.ingest.arn
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem", "dynamodb:GetItem", "dynamodb:Query"]
        Resource = aws_dynamodb_table.anomalies.arn
      },
      {
        Effect   = "Allow"
        Action   = ["sagemaker:InvokeEndpoint"]
        Resource = "arn:aws:sagemaker:${var.aws_region}:${data.aws_caller_identity.current.account_id}:endpoint/${var.project}-*"
      },
      {
        Effect   = "Allow"
        Action   = ["sns:Publish"]
        Resource = aws_sns_topic.alerts.arn
      },
      {
        Effect   = "Allow"
        Action   = ["cloudwatch:PutMetricData"]
        Resource = "*"
      }
    ]
  })
}

# ── Lambda Function — Stream Processor ───────────────────────────────────────

data "archive_file" "stream_processor" {
  type        = "zip"
  source_file = "${path.module}/../lambda/stream_processor.py"
  output_path = "/tmp/stream_processor.zip"
}

resource "aws_lambda_function" "stream_processor" {
  function_name    = "${var.project}-stream-processor"
  filename         = data.archive_file.stream_processor.output_path
  source_code_hash = data.archive_file.stream_processor.output_base64sha256
  handler          = "stream_processor.handler"
  runtime          = "python3.11"
  role             = aws_iam_role.lambda.arn
  timeout          = 60
  memory_size      = 512

  environment {
    variables = {
      DYNAMODB_TABLE              = aws_dynamodb_table.anomalies.name
      SNS_TOPIC_ARN               = aws_sns_topic.alerts.arn
      ANOMALY_SCORE_THRESHOLD     = "0.3"
      SAGEMAKER_ENDPOINT_IOT      = "${var.project}-iot"
      SAGEMAKER_ENDPOINT_FINANCE  = "${var.project}-finance"
      SAGEMAKER_ENDPOINT_NETWORK  = "${var.project}-network"
    }
  }

  tags = local.tags
}

# ── Kinesis → Lambda Trigger ──────────────────────────────────────────────────

resource "aws_lambda_event_source_mapping" "kinesis_trigger" {
  event_source_arn              = aws_kinesis_stream.ingest.arn
  function_name                 = aws_lambda_function.stream_processor.arn
  starting_position             = "LATEST"
  batch_size                    = 100
  parallelization_factor        = 4
  bisect_batch_on_function_error = true

  destination_config {
    on_failure {
      destination_arn = aws_sns_topic.alerts.arn
    }
  }
}

# ── IAM Role — Firehose ───────────────────────────────────────────────────────

resource "aws_iam_role" "firehose" {
  name = "${var.project}-firehose-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "firehose.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "firehose_s3" {
  name = "${var.project}-firehose-s3"
  role = aws_iam_role.firehose.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:PutObject", "s3:GetBucketLocation", "s3:ListBucket"]
      Resource = [aws_s3_bucket.data_lake.arn, "${aws_s3_bucket.data_lake.arn}/*"]
    }]
  })
}

# ── Glue Database + Crawler (Athena queries) ──────────────────────────────────

resource "aws_glue_catalog_database" "streamsense" {
  name = var.project
}

resource "aws_iam_role" "glue_crawler" {
  name = "${var.project}-glue-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "glue.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "glue_service" {
  role       = aws_iam_role.glue_crawler.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSGlueServiceRole"
}

resource "aws_glue_crawler" "raw_data" {
  name          = "${var.project}-raw-crawler"
  role          = aws_iam_role.glue_crawler.arn
  database_name = aws_glue_catalog_database.streamsense.name
  schedule      = "cron(0 */6 * * ? *)"

  s3_target {
    path = "s3://${aws_s3_bucket.data_lake.bucket}/raw/"
  }

  tags = local.tags
}

# ── CloudWatch Dashboard ──────────────────────────────────────────────────────

resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = var.project
  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric", x = 0, y = 0, width = 12, height = 6,
        properties = {
          title   = "Anomalies Detected"
          metrics = [
            ["StreamSense", "AnomalyDetected", "Source", "iot"],
            ["StreamSense", "AnomalyDetected", "Source", "finance"],
            ["StreamSense", "AnomalyDetected", "Source", "network"],
          ]
          period = 60, stat = "Sum", view = "timeSeries"
        }
      },
      {
        type = "metric", x = 12, y = 0, width = 12, height = 6,
        properties = {
          title   = "Inference Latency (ms)"
          metrics = [
            ["StreamSense", "InferenceLatencyMs", "Source", "iot"],
            ["StreamSense", "InferenceLatencyMs", "Source", "finance"],
          ]
          period = 60, stat = "Average", view = "timeSeries"
        }
      }
    ]
  })
}

# ── Outputs ───────────────────────────────────────────────────────────────────

output "kinesis_stream_name" { value = aws_kinesis_stream.ingest.name }
output "s3_bucket"           { value = aws_s3_bucket.data_lake.bucket }
output "dynamodb_table"      { value = aws_dynamodb_table.anomalies.name }
output "sns_topic_arn"       { value = aws_sns_topic.alerts.arn }
output "lambda_function"     { value = aws_lambda_function.stream_processor.function_name }
output "cloudwatch_dashboard"{ value = "https://${var.aws_region}.console.aws.amazon.com/cloudwatch/home?region=${var.aws_region}#dashboards:name=${var.project}" }
