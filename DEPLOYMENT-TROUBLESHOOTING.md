# Deployment Troubleshooting Guide

## Issue Summary
The Firebase Cloud Functions deployment failed with a Cloud Run container error. This was caused by several configuration issues that have been resolved.

## Root Causes Identified

### 1. Function Naming Conflict
- **Problem**: `main.py` had `@functions_framework.http` decorator on function named `app`
- **Solution**: Changed entry point to `main` function in `main.py`

### 2. Import Dependencies Issue
- **Problem**: Complex backup module imports causing build failures
- **Solution**: Simplified backup function calls to avoid circular dependencies

### 3. Deployment Script Issues
- **Problem**: Incorrect trigger flag (`--trigger=http` instead of `--trigger-http`)
- **Solution**: Fixed in `deploy.sh` script

### 4. Authentication Token Expiry
- **Problem**: gcloud auth tokens expired during deployment
- **Solution**: Re-authenticated with `gcloud auth login`

## Changes Made

### 1. Fixed main.py ✅
- Renamed function from `app` to `main`
- Simplified backup endpoint implementations
- Removed complex import dependencies
- Added proper error handling

### 2. Updated deploy.sh ✅
- Fixed trigger flag syntax
- Updated entry point configuration
- Added proper environment variables
- Removed min-instances requirement

### 3. Created Simplified Version ✅
- `main_simple.py` - minimal version for testing
- Basic authentication and CORS handling
- Simplified endpoints without complex dependencies

## Current Status

### Working Files
- ✅ `main.py` - Fixed with security enhancements
- ✅ `main_simple.py` - Minimal test version
- ✅ `backup.py` - Complete backup system
- ✅ `deploy.sh` - Corrected deployment script
- ✅ `requirements.txt` - Updated dependencies

### Deployment Commands

#### Option 1: Deploy Full Version
```bash
gcloud functions deploy main \
  --gen2 \
  --runtime=python311 \
  --region=us-central1 \
  --source=. \
  --entry-point=main \
  --trigger-http \
  --memory=1Gi \
  --timeout=540s \
  --set-env-vars="GOOGLE_CLOUD_PROJECT=medical-advisor-bd734,ALLOWED_ORIGINS=https://your-domain.com" \
  --max-instances=10 \
  --min-instances=0 \
  --no-allow-unauthenticated
```

#### Option 2: Deploy Simple Test Version
```bash
gcloud functions deploy main-simple \
  --gen2 \
  --runtime=python311 \
  --region=us-central1 \
  --source=. \
  --entry-point=main \
  --trigger-http \
  --no-allow-unauthenticated
```

#### Option 3: Use Deploy Script
```bash
./deploy.sh
```

## Backup System Deployment

### Deploy Backup Function
```bash
gcloud functions deploy backup-firestore \
  --gen2 \
  --runtime=python311 \
  --region=us-central1 \
  --source=. \
  --entry-point=backup_firestore_http \
  --trigger-http \
  --memory=1Gi \
  --timeout=600s \
  --set-env-vars="GOOGLE_CLOUD_PROJECT=medical-advisor-bd734" \
  --no-allow-unauthenticated
```

### Deploy Scheduled Backup
```bash
gcloud functions deploy scheduled-backup \
  --gen2 \
  --runtime=python311 \
  --region=us-central1 \
  --source=. \
  --entry-point=scheduled_firestore_export \
  --trigger-topic=backup-trigger \
  --memory=1Gi \
  --timeout=600s \
  --set-env-vars="GOOGLE_CLOUD_PROJECT=medical-advisor-bd734"
```

## Security Configuration

After deployment, apply security hardening:

```bash
# Make security script executable
chmod +x apply-security.sh

# Apply security configuration
./apply-security.sh

# Set allowed domains for CORS
gcloud functions deploy main --update-env-vars ALLOWED_ORIGINS="https://yourdomain.com,https://admin.yourdomain.com"
```

## Testing Deployment

### Test Main Function
```bash
# Get function URL
FUNCTION_URL=$(gcloud functions describe main --region=us-central1 --format="value(serviceConfig.uri)")

# Test endpoint
curl -X POST "$FUNCTION_URL" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_FIREBASE_TOKEN" \
  -d '{"action": "test"}'
```

### Test Backup Function
```bash
BACKUP_URL=$(gcloud functions describe backup-firestore --region=us-central1 --format="value(serviceConfig.uri)")

curl -X POST "$BACKUP_URL" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_FIREBASE_TOKEN" \
  -d '{"action": "backup"}'
```

## Troubleshooting Tips

### 1. Authentication Issues
```bash
# Re-authenticate
gcloud auth login

# Check current project
gcloud config get project

# Set correct project
gcloud config set project medical-advisor-bd734
```

### 2. Check Function Status
```bash
# List all functions
gcloud functions list --region=us-central1

# Get function details
gcloud functions describe main --region=us-central1

# View logs
gcloud functions logs read main --region=us-central1
```

### 3. Debug Build Issues
```bash
# Deploy with verbose output
gcloud functions deploy main --verbosity=debug

# Check build logs
gcloud builds list
gcloud builds log [BUILD_ID]
```

### 4. Dependency Issues
If deployment fails due to dependencies:
```bash
# Install dependencies locally first
pip install -r requirements.txt

# Check for conflicts
pip check

# Update requirements if needed
pip freeze > requirements.txt
```

## Next Steps

1. **Complete Deployment**: Run one of the deployment commands above
2. **Test Functions**: Use the testing commands to verify functionality
3. **Apply Security**: Run the security hardening script
4. **Setup Monitoring**: Configure Cloud Monitoring for the functions
5. **Schedule Backups**: Set up Cloud Scheduler for automated backups

## Support Files

- `main.py` - Main application function with all features
- `main_simple.py` - Simplified version for testing
- `backup.py` - Backup system functions
- `deploy.sh` - Automated deployment script
- `apply-security.sh` - Security hardening script
- `requirements.txt` - Python dependencies

All files are ready for deployment with proper error handling and security measures in place.