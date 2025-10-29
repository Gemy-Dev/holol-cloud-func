#!/bin/bash

echo "🔧 Setting up Firebase Backup System..."

# Configuration
PROJECT_ID="medical-advisor-bd734"
REGION="us-central1"
BACKUP_BUCKET="${PROJECT_ID}-backups"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    case $1 in
        "error") echo -e "${RED}❌ $2${NC}" ;;
        "success") echo -e "${GREEN}✅ $2${NC}" ;;
        "warning") echo -e "${YELLOW}⚠️ $2${NC}" ;;
        "info") echo -e "${BLUE}ℹ️ $2${NC}" ;;
        *) echo "$2" ;;
    esac
}

# Check if user is authenticated
check_auth() {
    print_status "info" "Checking authentication..."
    if ! gcloud auth list --filter=status:ACTIVE --format="value(account)" | head -n1 > /dev/null; then
        print_status "error" "No active gcloud authentication found"
        echo "Please run: gcloud auth login"
        exit 1
    fi
    
    ACTIVE_ACCOUNT=$(gcloud auth list --filter=status:ACTIVE --format="value(account)" | head -n1)
    print_status "success" "Authenticated as: $ACTIVE_ACCOUNT"
}

# Set project
set_project() {
    print_status "info" "Setting project to: $PROJECT_ID"
    gcloud config set project $PROJECT_ID
    
    if [ $? -eq 0 ]; then
        print_status "success" "Project set successfully"
    else
        print_status "error" "Failed to set project"
        exit 1
    fi
}

# Enable required APIs
enable_apis() {
    print_status "info" "Enabling required APIs..."
    
    REQUIRED_APIS=(
        "cloudfunctions.googleapis.com"
        "cloudscheduler.googleapis.com"
        "firestore.googleapis.com"
        "storage.googleapis.com"
        "cloudresourcemanager.googleapis.com"
        "pubsub.googleapis.com"
        "cloudresourcemanager.googleapis.com"
    )
    
    for api in "${REQUIRED_APIS[@]}"; do
        print_status "info" "Checking $api..."
        if gcloud services list --enabled --filter="name:$api" --format="value(name)" | grep -q "$api"; then
            print_status "success" "$api is already enabled"
        else
            print_status "warning" "Enabling $api..."
            gcloud services enable "$api"
            if [ $? -eq 0 ]; then
                print_status "success" "$api enabled successfully"
            else
                print_status "error" "Failed to enable $api"
                exit 1
            fi
        fi
    done
}

# Create backup bucket
create_backup_bucket() {
    print_status "info" "Setting up backup bucket: $BACKUP_BUCKET"
    
    if gsutil ls -b "gs://$BACKUP_BUCKET" &>/dev/null; then
        print_status "success" "Backup bucket already exists"
        return 0
    fi
    
    print_status "info" "Creating backup bucket..."
    gsutil mb -p "$PROJECT_ID" -c STANDARD -l "$REGION" "gs://$BACKUP_BUCKET"
    
    if [ $? -eq 0 ]; then
        print_status "success" "Backup bucket created"
        
        # Set bucket lifecycle policy
        print_status "info" "Setting up bucket lifecycle policy..."
        cat > /tmp/lifecycle.json << EOF
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
        
        gsutil lifecycle set /tmp/lifecycle.json "gs://$BACKUP_BUCKET"
        rm /tmp/lifecycle.json
        
        if [ $? -eq 0 ]; then
            print_status "success" "Lifecycle policy applied (35-day retention)"
        else
            print_status "warning" "Failed to set lifecycle policy, but bucket is created"
        fi
    else
        print_status "error" "Failed to create backup bucket"
        exit 1
    fi
}

# Create Pub/Sub topic
create_pubsub_topic() {
    print_status "info" "Setting up Pub/Sub topic for backup triggers..."
    
    if gcloud pubsub topics describe firestore-backup-trigger &>/dev/null; then
        print_status "success" "Pub/Sub topic already exists"
    else
        print_status "info" "Creating Pub/Sub topic..."
        gcloud pubsub topics create firestore-backup-trigger
        
        if [ $? -eq 0 ]; then
            print_status "success" "Pub/Sub topic created"
        else
            print_status "error" "Failed to create Pub/Sub topic"
            exit 1
        fi
    fi
}

# Setup IAM permissions
setup_iam() {
    print_status "info" "Setting up IAM permissions..."
    
    # Get the default compute service account
    COMPUTE_SA=$(gcloud iam service-accounts list --filter="email~compute@developer.gserviceaccount.com" --format="value(email)")
    
    if [ -n "$COMPUTE_SA" ]; then
        print_status "info" "Found compute service account: $COMPUTE_SA"
        
        # Grant Firestore export permissions
        print_status "info" "Granting Firestore import/export permissions..."
        gcloud projects add-iam-policy-binding "$PROJECT_ID" \
            --member="serviceAccount:$COMPUTE_SA" \
            --role="roles/datastore.importExportAdmin" \
            --quiet
        
        # Grant Storage admin permissions for backup bucket
        print_status "info" "Granting Storage permissions..."
        gsutil iam ch "serviceAccount:$COMPUTE_SA:objectAdmin" "gs://$BACKUP_BUCKET"
        
        print_status "success" "IAM permissions configured"
    else
        print_status "warning" "Could not find default compute service account"
        print_status "info" "You may need to manually configure IAM permissions"
    fi
}

# Test backup system
test_backup() {
    print_status "info" "Testing backup system setup..."
    
    # Check if backup bucket is accessible
    if gsutil ls "gs://$BACKUP_BUCKET" &>/dev/null; then
        print_status "success" "Backup bucket is accessible"
    else
        print_status "error" "Backup bucket is not accessible"
        return 1
    fi
    
    # Check if Pub/Sub topic exists
    if gcloud pubsub topics describe firestore-backup-trigger &>/dev/null; then
        print_status "success" "Pub/Sub topic is ready"
    else
        print_status "error" "Pub/Sub topic is not ready"
        return 1
    fi
    
    print_status "success" "Backup system setup is complete!"
}

# Generate deployment summary
generate_summary() {
    echo ""
    echo "=============================================="
    echo "🎉 BACKUP SYSTEM SETUP SUMMARY"
    echo "=============================================="
    echo ""
    print_status "info" "Project ID: $PROJECT_ID"
    print_status "info" "Region: $REGION"
    print_status "info" "Backup Bucket: gs://$BACKUP_BUCKET"
    print_status "info" "Pub/Sub Topic: firestore-backup-trigger"
    print_status "info" "Retention Period: 30 days (managed by lifecycle policy)"
    echo ""
    echo "📋 NEXT STEPS:"
    echo "1. Run './deploy.sh' to deploy the functions"
    echo "2. Backup will run automatically daily at 2 AM UTC"
    echo "3. Use API endpoints for manual backup management"
    echo ""
    echo "🔧 BACKUP API ENDPOINTS (after deployment):"
    echo "- POST /app with {\"action\": \"manualBackup\"} - Trigger backup"
    echo "- POST /app with {\"action\": \"backupStatus\"} - Get status"
    echo "- POST /app with {\"action\": \"listBackups\"} - List backups"
    echo "- POST /app with {\"action\": \"cleanupBackups\"} - Clean old backups"
    echo ""
    echo "⏰ SCHEDULE:"
    echo "- Daily Firestore backup: 2:00 AM UTC"
    echo "- Automatic cleanup: 35 days (bucket lifecycle)"
    echo "- Application cleanup: 30 days (in backup function)"
    echo ""
}

# Main execution
main() {
    echo "🚀 Starting Firebase Backup System Setup..."
    echo ""
    
    check_auth
    set_project
    enable_apis
    create_backup_bucket
    create_pubsub_topic
    setup_iam
    test_backup
    generate_summary
    
    print_status "success" "Setup completed successfully!"
    print_status "info" "You can now run './deploy.sh' to deploy the functions"
}

# Run main function
main "$@"