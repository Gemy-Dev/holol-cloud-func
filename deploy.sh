#!/bin/bash

echo "🚀 Starting deployment of Medical Advisor Cloud Functions..."

# Add gcloud to PATH if installed via Homebrew
export PATH="/opt/homebrew/share/google-cloud-sdk/bin:$PATH"

# Check if gcloud is installed
if ! command -v gcloud &> /dev/null; then
    echo "❌ Error: Google Cloud SDK (gcloud) is not installed"
    echo "📥 Please install it from: https://cloud.google.com/sdk/docs/install"
    echo "   Or run: brew install google-cloud-sdk"
    exit 1
fi

# Check if gsutil is installed
if ! command -v gsutil &> /dev/null; then
    echo "❌ Error: gsutil is not installed"
    echo "📥 Please install Google Cloud SDK"
    exit 1
fi

# Set your project ID (replace with your actual project ID)
PROJECT_ID="test-medical-80e1b"
REGION="us-central1"
BACKUP_BUCKET="${PROJECT_ID}-firestore-backups"

# The moment the reviewers' morning summary starts counting (spec 2038): reports
# and visits created before it are never reminded about. The default is far in the
# future, which keeps the summary silent. Set the go-live time (Iraq time, the
# contract's date format) here or in the environment BEFORE deploying: every deploy
# replaces ALL env vars with the list below, so a value left out resets to the
# silent default.
REVIEW_REMINDER_SINCE="${REVIEW_REMINDER_SINCE:-2099-01-01T00:00:00.000}"

echo "🔧 Setting project configuration..."
gcloud config set project $PROJECT_ID

# Function to check if APIs are enabled
check_apis() {
    echo "📋 Checking required APIs..."
    
    REQUIRED_APIS=(
        "cloudfunctions.googleapis.com"
        "cloudscheduler.googleapis.com"
        "firestore.googleapis.com"
        "storage.googleapis.com"
        "cloudresourcemanager.googleapis.com"
        "pubsub.googleapis.com"
    )
    
    for api in "${REQUIRED_APIS[@]}"; do
        if gcloud services list --enabled --filter="name:$api" --format="value(name)" | grep -q "$api"; then
            echo "✅ $api is enabled"
        else
            echo "⚠️ Enabling $api..."
            gcloud services enable "$api"
        fi
    done
}

# Function to create backup bucket
setup_backup_bucket() {
    echo "🪣 Setting up backup bucket..."
    
    if gsutil ls -b "gs://$BACKUP_BUCKET" &>/dev/null; then
        echo "✅ Backup bucket already exists: gs://$BACKUP_BUCKET"
    else
        echo "🆕 Creating backup bucket: gs://$BACKUP_BUCKET"
        gsutil mb -p "$PROJECT_ID" -c STANDARD -l "$REGION" "gs://$BACKUP_BUCKET"
        
        # Set bucket lifecycle to automatically delete old files (backup retention)
        cat > lifecycle.json << EOF
{
  "lifecycle": {
    "rule": [
      {
        "action": {
          "type": "Delete"
        },
        "condition": {
          "age": 35,
          "matchesPrefix": ["firestore-backups/"]
        }
      }
    ]
  }
}
EOF
        
        gsutil lifecycle set lifecycle.json "gs://$BACKUP_BUCKET"
        rm lifecycle.json
        echo "✅ Backup bucket created with 35-day lifecycle policy"
    fi
}

# Function to deploy app function
deploy_main_function() {
    echo "📦 Deploying app function..."
    
    # Verify app.py exists
    if [ ! -f "app.py" ]; then
        echo "❌ app.py not found!"
        return 1
    fi
    
    echo "✅ Deploying with modular structure"
    
    # main.py already exists with the entry point, no symlink needed
    if [ ! -f "main.py" ]; then
        echo "📝 Creating main.py symlink..."
        ln -sf app.py main.py
        CLEANUP_MAIN=true
    else
        CLEANUP_MAIN=false
    fi
    
    DEPLOY_RESULT=0
    gcloud functions deploy app \
        --gen2 \
        --runtime=python311 \
        --region=$REGION \
        --source=. \
        --entry-point=app \
        --trigger-http \
        --allow-unauthenticated \
        --memory=1Gi \
        --timeout=540s \
        --set-env-vars="GOOGLE_CLOUD_PROJECT=$PROJECT_ID,ALLOWED_ORIGINS=*,EMAIL_SMTP_PASSWORD=dvgtizshxpxxefxn,REVIEW_REMINDER_SINCE=$REVIEW_REMINDER_SINCE" \
        --max-instances=10 \
        --min-instances=0
    
    DEPLOY_RESULT=$?
    
    # Clean up symlink only if we created it
    if [ "$CLEANUP_MAIN" = true ]; then
        rm -f main.py
    fi
    
    if [ $DEPLOY_RESULT -eq 0 ]; then
        echo "✅ App function deployed successfully!"
        return 0
    else
        echo "❌ App function deployment failed!"
        return 1
    fi
}





# Function to setup schedulers
setup_schedulers() {
    echo "⏰ Setting up schedulers..."
    
    # Get app function URL  
    FUNCTION_URL=$(gcloud functions describe app --region=$REGION --project=$PROJECT_ID --format="value(serviceConfig.uri)")
    
    if [ -z "$FUNCTION_URL" ]; then
        echo "❌ Failed to get app function URL"
        return 1
    fi
    
    echo "🔗 App Function URL: $FUNCTION_URL"
    
    # Task notifications: once a day at 05:00 UTC = 08:00 Iraq time, matching
    # handle_daily_notifications' documented intent. The handler skips a
    # reminder already recorded under its id, so a repeated run does not push
    # twice; the schedule still stays once a day.
    #
    # The Content-Type header is load-bearing: without it gcloud sends
    # Content-Type: application/octet-stream, and main.py's request.get_json()
    # raises 415, which the generic handler turns into a 500. That silently
    # broke notifications entirely.
    #
    # `create` spells it --headers and `update` spells it --update-headers.
    # Getting that wrong fails only the update branch, so it stays invisible
    # until the day a redeploy has to move the jobs to a new function URL.
    if gcloud scheduler jobs describe daily-notifications --location=$REGION --project=$PROJECT_ID &>/dev/null; then
        echo "📝 Updating task notifications scheduler..."
        gcloud scheduler jobs update http daily-notifications \
            --schedule="0 5 * * *" \
            --uri="$FUNCTION_URL" \
            --http-method=POST \
            --update-headers="Content-Type=application/json" \
            --message-body='{"action":"daily_notifications"}' \
            --time-zone="UTC" \
            --location=$REGION \
            --project=$PROJECT_ID
    else
        echo "🆕 Creating task notifications scheduler..."
        gcloud scheduler jobs create http daily-notifications \
            --schedule="0 5 * * *" \
            --uri="$FUNCTION_URL" \
            --http-method=POST \
            --headers="Content-Type=application/json" \
            --message-body='{"action":"daily_notifications"}' \
            --time-zone="UTC" \
            --location=$REGION \
            --description="Task notifications daily at 08:00 Iraq time" \
            --project=$PROJECT_ID
    fi
    
    # Tomorrow's tasks: 17:00 UTC = 20:00 Iraq time, the second half of
    # handle_daily_notifications' documented schedule (days_offset=1).
    # Same guard as above, and the same once-a-day schedule.
    if gcloud scheduler jobs describe notify-tomorrow-tasks --location=$REGION --project=$PROJECT_ID &>/dev/null; then
        echo "📝 Updating tomorrow-tasks scheduler..."
        gcloud scheduler jobs update http notify-tomorrow-tasks \
            --schedule="0 17 * * *" \
            --uri="$FUNCTION_URL" \
            --http-method=POST \
            --update-headers="Content-Type=application/json" \
            --message-body='{"action":"notify_tomorrow_tasks"}' \
            --time-zone="UTC" \
            --location=$REGION \
            --project=$PROJECT_ID
    else
        echo "🆕 Creating tomorrow-tasks scheduler..."
        gcloud scheduler jobs create http notify-tomorrow-tasks \
            --schedule="0 17 * * *" \
            --uri="$FUNCTION_URL" \
            --http-method=POST \
            --headers="Content-Type=application/json" \
            --message-body='{"action":"notify_tomorrow_tasks"}' \
            --time-zone="UTC" \
            --location=$REGION \
            --description="Tomorrow's task notifications daily at 20:00 Iraq time" \
            --project=$PROJECT_ID
    fi
    
    # Reviewers' summary of pending reviews and undecided special requests
    # (spec 2038): 05:00 UTC = 08:00 Iraq time. Each reviewer's summary is
    # claimed under a per-day id, so a repeated run sends nothing. Same
    # Content-Type and create/update header rules as above.
    if gcloud scheduler jobs describe review-reminders --location=$REGION --project=$PROJECT_ID &>/dev/null; then
        echo "📝 Updating review reminders scheduler..."
        gcloud scheduler jobs update http review-reminders \
            --schedule="0 5 * * *" \
            --uri="$FUNCTION_URL" \
            --http-method=POST \
            --update-headers="Content-Type=application/json" \
            --message-body='{"action":"review_reminders"}' \
            --time-zone="UTC" \
            --location=$REGION \
            --project=$PROJECT_ID
    else
        echo "🆕 Creating review reminders scheduler..."
        gcloud scheduler jobs create http review-reminders \
            --schedule="0 5 * * *" \
            --uri="$FUNCTION_URL" \
            --http-method=POST \
            --headers="Content-Type=application/json" \
            --message-body='{"action":"review_reminders"}' \
            --time-zone="UTC" \
            --location=$REGION \
            --description="Pending review summary daily at 08:00 Iraq time" \
            --project=$PROJECT_ID
    fi

    return $?
}

# Function to grant necessary permissions
setup_permissions() {
    echo "🔐 Setting up IAM permissions..."
    
    # Get the default compute service account
    COMPUTE_SA=$(gcloud iam service-accounts list --filter="email~compute@developer.gserviceaccount.com" --format="value(email)")
    
    if [ -n "$COMPUTE_SA" ]; then
        echo "🔑 Granting permissions to: $COMPUTE_SA"
        
        # Grant Firestore export permissions
        gcloud projects add-iam-policy-binding "$PROJECT_ID" \
            --member="serviceAccount:$COMPUTE_SA" \
            --role="roles/datastore.importExportAdmin" \
            --quiet
        
        # Grant Storage admin permissions for backup bucket
        gsutil iam ch "serviceAccount:$COMPUTE_SA:objectAdmin" "gs://$BACKUP_BUCKET"
        
        echo "✅ Permissions granted"
    else
        echo "⚠️ Could not find compute service account"
    fi
}

# Main deployment flow
main() {
    check_apis
    setup_backup_bucket
    
    echo "📦 Deploying app function..."
    if deploy_main_function; then
        echo "✅ App function deployed successfully!"
        echo "🔒 Note: App function includes integrated backup functionality"
    else
        echo "❌ App function deployment failed!"
        exit 1
    fi
    
    setup_permissions
    
    if setup_schedulers; then
        echo "🎉 Deployment completed successfully!"
        echo ""
        echo "📱 Your app API URL: https://us-central1-$PROJECT_ID.cloudfunctions.net/app"
        echo "  Notifications run daily at 5 AM UTC (8 AM Iraq time)"
        echo "🪣 Backup bucket: gs://$BACKUP_BUCKET"
        echo ""
        echo "🔐 Security: All functions require authentication"
        echo ""
        echo " Test your app function:"
        echo "curl -X POST $FUNCTION_URL -H \"Content-Type: application/json\" -d '{\"action\":\"daily_notifications\"}'"
        echo ""
        echo "🔍 Available backup actions (integrated in app function):"
        echo "  - manualBackup: Trigger manual backup"
        echo "  - backupStatus: Get backup status"
        echo "  - listBackups: List all backups"
        echo ""
        echo "🧪 Test backup functionality:"
        echo "curl -X POST $FUNCTION_URL -H \"Content-Type: application/json\" -H \"Authorization: Bearer YOUR_TOKEN\" -d '{\"action\":\"backupStatus\"}'"
    else
        echo "⚠️ Function deployed but scheduler setup failed"
        echo "You can manually create the scheduler jobs later"
    fi
}

# Run main function
main
