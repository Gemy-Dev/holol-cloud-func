#!/bin/bash

# Setup Cloud Scheduler for notifications at 8 AM and 8 PM Iraq time (UTC+3)
# These jobs will trigger Cloud Functions to send task notifications

set -e

# Get project ID
PROJECT_ID=$(gcloud config get-value project)
REGION="us-central1"
FUNCTION_URL="https://${REGION}-${PROJECT_ID}.cloudfunctions.net/app"

echo "🕐 Setting up Cloud Scheduler jobs for Medical Advisor..."
echo "Project: $PROJECT_ID"
echo "Function URL: $FUNCTION_URL"

# Create Cloud Scheduler jobs
# Note: Times are in UTC. Iraq time (UTC+3) to UTC conversion:
# 8 AM Iraq = 5 AM UTC
# 8 PM Iraq = 5 PM UTC

echo ""
echo "📋 Creating scheduler job: notify-today-tasks (8 AM Iraq time / 5 AM UTC)..."

# Job 1: 8 AM Iraq time - Send today's tasks
gcloud scheduler jobs create http notify-today-tasks \
  --location=$REGION \
  --schedule="0 5 * * *" \
  --time-zone="Etc/UTC" \
  --uri="$FUNCTION_URL" \
  --http-method=POST \
  --message-body='{"action":"notify_today_tasks"}' \
  --headers="Content-Type=application/json" \
  --oidc-service-account-email="$(gcloud iam service-accounts list --filter='displayName:App Engine' --format='value(email)')" \
  --oidc-token-audience="$FUNCTION_URL" \
  2>/dev/null || \
  gcloud scheduler jobs update http notify-today-tasks \
  --location=$REGION \
  --schedule="0 5 * * *" \
  --time-zone="Etc/UTC" \
  --uri="$FUNCTION_URL" \
  --http-method=POST \
  --message-body='{"action":"notify_today_tasks"}' \
  --headers="Content-Type=application/json"

echo "✅ Job created: notify-today-tasks (8 AM Iraq / 5 AM UTC daily)"

echo ""
echo "📋 Creating scheduler job: notify-tomorrow-tasks (8 PM Iraq time / 5 PM UTC)..."

# Job 2: 8 PM Iraq time - Send tomorrow's tasks
gcloud scheduler jobs create http notify-tomorrow-tasks \
  --location=$REGION \
  --schedule="0 17 * * *" \
  --time-zone="Etc/UTC" \
  --uri="$FUNCTION_URL" \
  --http-method=POST \
  --message-body='{"action":"notify_tomorrow_tasks"}' \
  --headers="Content-Type=application/json" \
  --oidc-service-account-email="$(gcloud iam service-accounts list --filter='displayName:App Engine' --format='value(email)')" \
  --oidc-token-audience="$FUNCTION_URL" \
  2>/dev/null || \
  gcloud scheduler jobs update http notify-tomorrow-tasks \
  --location=$REGION \
  --schedule="0 17 * * *" \
  --time-zone="Etc/UTC" \
  --uri="$FUNCTION_URL" \
  --http-method=POST \
  --message-body='{"action":"notify_tomorrow_tasks"}' \
  --headers="Content-Type=application/json"

echo "✅ Job created: notify-tomorrow-tasks (8 PM Iraq / 5 PM UTC daily)"

echo ""
echo "📋 Verifying Cloud Scheduler API is enabled..."
gcloud services enable cloudscheduler.googleapis.com

echo ""
echo "✅ Cloud Scheduler setup complete!"
echo ""
echo "📅 Schedule Summary:"
echo "  - 8 AM Iraq time (5 AM UTC): Sends notifications for TODAY's tasks"
echo "  - 8 PM Iraq time (5 PM UTC): Sends notifications for TOMORROW's tasks"
echo ""
echo "🔍 To view scheduled jobs:"
echo "  gcloud scheduler jobs list --location=$REGION"
echo ""
echo "🧪 To manually trigger a job:"
echo "  gcloud scheduler jobs run notify-today-tasks --location=$REGION"
echo "  gcloud scheduler jobs run notify-tomorrow-tasks --location=$REGION"
echo ""
echo "⚙️  To pause a job:"
echo "  gcloud scheduler jobs pause notify-today-tasks --location=$REGION"
echo "  gcloud scheduler jobs pause notify-tomorrow-tasks --location=$REGION"
