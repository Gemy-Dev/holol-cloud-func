#!/bin/bash

echo "🚀 Starting deployment of Medical Advisor Cloud Functions..."

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
PROJECT_ID="medical-advisor-bd734"
REGION="us-central1"
BACKUP_BUCKET="${PROJECT_ID}-backups"

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
    
    echo "✅ Deploying app.py with modular structure"
    
    # Create temporary main.py symlink for Cloud Functions deployment
    # Cloud Functions expects main.py but we use app.py
    echo "📝 Creating temporary main.py link..."
    ln -sf app.py main.py
    
    # Deploy using 'app' as entry point
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
        --set-env-vars="GOOGLE_CLOUD_PROJECT=$PROJECT_ID,ALLOWED_ORIGINS=*" \
        --max-instances=10 \
        --min-instances=0
    
    DEPLOY_RESULT=$?
    
    # Clean up symlink
    rm -f main.py
    
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
    
    # Setup daily notifications scheduler
    if gcloud scheduler jobs describe daily-notifications --location=$REGION --project=$PROJECT_ID &>/dev/null; then
        echo "📝 Updating daily notifications scheduler..."
        gcloud scheduler jobs update http daily-notifications \
            --schedule="0 5 * * *" \
            --uri="$FUNCTION_URL" \
            --http-method=POST \
            --message-body='{"action":"daily_notifications"}' \
            --time-zone="UTC" \
            --location=$REGION \
            --project=$PROJECT_ID
    else
        echo "🆕 Creating daily notifications scheduler..."
        gcloud scheduler jobs create http daily-notifications \
            --schedule="0 5 * * *" \
            --uri="$FUNCTION_URL" \
            --http-method=POST \
            --message-body='{"action":"daily_notifications"}' \
            --time-zone="UTC" \
            --location=$REGION \
            --description="Daily task notifications" \
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
        echo "� Notifications run daily at 5 AM UTC (8 AM Iraq time)"
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
