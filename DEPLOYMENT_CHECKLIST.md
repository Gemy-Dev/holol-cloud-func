# Deployment Checklist - Notification System

## Pre-Deployment

### 1. Verify Configuration
- [ ] Check `PROJECT_ID` in `deploy.sh`
- [ ] Check `REGION` in `deploy.sh`
- [ ] Verify Firebase project is set up
- [ ] Confirm Cloud Scheduler API is enabled

### 2. Test Locally (Optional)
```bash
# Install dependencies
pip install -r requirements.txt

# Run local tests
python test_date_range.py
python test_stats.py
```

### 3. Review Code Changes
- [ ] `modules/notifications.py` - Enhanced with task data
- [ ] `app.py` - Added test notification route
- [ ] `deploy.sh` - Added test scheduler

---

## Deployment

### Step 1: Deploy Cloud Function
```bash
./deploy.sh
```

**Expected Output:**
```
✅ Function deployed successfully
✅ Production scheduler created/updated
✅ Test scheduler created/updated
```

### Step 2: Verify Deployment
```bash
# Check function status
gcloud functions describe app --region=us-central1

# Check schedulers
gcloud scheduler jobs list --location=us-central1
```

**Expected Schedulers:**
- `daily-notifications` - ENABLED, Schedule: `*/10 * * * *`
- `test-notifications` - ENABLED, Schedule: `* * * * *`

---

## Testing

### Phase 1: Test Scheduler (Immediate)

#### Option A: Wait 1 Minute
The test scheduler will automatically trigger.

#### Option B: Trigger Manually
```bash
./manage-test-scheduler.sh trigger
```

**Expected Response:**
```json
{
  "success": true,
  "message": "Test notification sent to N users",
  "successCount": N,
  "failureCount": 0,
  "totalTokens": N,
  "timestamp": "14:30:45"
}
```

### Phase 2: Verify on Device
- [ ] Open Flutter app on test device
- [ ] Verify notification received
- [ ] Check notification title (Arabic)
- [ ] Check notification body (with timestamp)
- [ ] Verify app handles notification tap

### Phase 3: Check Logs
```bash
./manage-test-scheduler.sh logs
```

**Look for:**
- ✅ "Test notification sent to X users"
- ✅ Success count matches user count
- ❌ No error messages

### Phase 4: Test Production Scheduler
```bash
# Get function URL
FUNCTION_URL=$(gcloud functions describe app \
  --region=us-central1 \
  --format="value(serviceConfig.uri)")

# Trigger production notification
curl -X POST $FUNCTION_URL \
  -H "Content-Type: application/json" \
  -d '{"action":"daily_notifications"}'
```

**Expected Response:**
```json
{
  "success": true,
  "message": "Task notifications completed. Sent N notifications.",
  "date": "2025-12-30",
  "count": N
}
```

---

## Post-Testing

### ⚠️ CRITICAL: Pause Test Scheduler
```bash
./manage-test-scheduler.sh pause
```

**Verify:**
```bash
./manage-test-scheduler.sh status
```

**Expected:** State should show `PAUSED`

---

## Production Verification

### 1. Monitor Production Scheduler
```bash
# Check status
gcloud scheduler jobs describe daily-notifications \
  --location=us-central1 \
  --format="table(name, schedule, state, status.lastAttemptTime)"
```

### 2. Monitor Function Logs
```bash
# View recent logs
gcloud functions logs read app \
  --region=us-central1 \
  --limit=50

# Filter for notifications
gcloud functions logs read app \
  --region=us-central1 \
  --limit=50 | grep "task notifications"
```

### 3. Verify Notification Delivery
- [ ] Wait 10 minutes for next scheduled run
- [ ] Check logs for execution
- [ ] Verify users with tasks receive notifications
- [ ] Verify notification data includes taskIds

### 4. Test Flutter App Integration
- [ ] User receives notification
- [ ] Tap notification opens app
- [ ] App navigates to tasks screen
- [ ] Correct tasks are displayed
- [ ] Task count matches notification

---

## Cleanup (After Testing Complete)

### Option 1: Keep Test Scheduler Paused
```bash
./manage-test-scheduler.sh pause
```
**Use when:** You might need it again later

### Option 2: Delete Test Scheduler
```bash
./manage-test-scheduler.sh delete
```
**Use when:** Testing is complete and won't be needed

---

## Monitoring & Maintenance

### Daily Checks (First Week)
- [ ] Check scheduler execution history
- [ ] Review function logs for errors
- [ ] Verify notification delivery rates
- [ ] Monitor user feedback

### Weekly Checks (Ongoing)
- [ ] Review FCM delivery statistics
- [ ] Check for invalid/expired tokens
- [ ] Monitor function execution costs
- [ ] Review error logs

### Monthly Checks
- [ ] Analyze notification engagement
- [ ] Review and optimize schedule if needed
- [ ] Update documentation if changes made

---

## Troubleshooting

### No Notifications Received

**Check 1: Schedulers Running**
```bash
gcloud scheduler jobs list --location=us-central1
```
- Verify state is ENABLED, not PAUSED

**Check 2: Function Logs**
```bash
gcloud functions logs read app --region=us-central1 --limit=50
```
- Look for error messages

**Check 3: FCM Tokens**
- Verify users have `fcmToken` in Firestore
- Check tokens are valid (not expired)

**Check 4: Manual Trigger**
```bash
./manage-test-scheduler.sh trigger
```
- If manual works but scheduler doesn't, check scheduler permissions

### Notifications Not Showing Task Data

**Check 1: Verify Data Payload**
```bash
# Check function logs for sent data
gcloud functions logs read app --region=us-central1 --limit=50
```

**Check 2: Flutter App Handler**
- Verify `message.data` is being read
- Check for null/empty values
- Add debug logging

### Too Many Notifications

**Immediate Action:**
```bash
./manage-test-scheduler.sh pause
```

**Then Check:**
- Which scheduler is causing it?
- Is test scheduler still running?
- Are there duplicate schedulers?

### High Costs

**Check Execution Count:**
```bash
gcloud scheduler jobs describe daily-notifications \
  --location=us-central1 \
  --format="value(status.attemptCount)"
```

**Verify Schedule:**
- Production should be `*/10 * * * *` (every 10 minutes)
- Test should be PAUSED or DELETED

---

## Rollback Plan

### If Issues Occur

**Step 1: Pause Schedulers**
```bash
gcloud scheduler jobs pause daily-notifications --location=us-central1
gcloud scheduler jobs pause test-notifications --location=us-central1
```

**Step 2: Review Logs**
```bash
gcloud functions logs read app --region=us-central1 --limit=100
```

**Step 3: Fix Issues**
- Update code as needed
- Test locally if possible

**Step 4: Redeploy**
```bash
./deploy.sh
```

**Step 5: Resume Schedulers**
```bash
gcloud scheduler jobs resume daily-notifications --location=us-central1
```

---

## Success Criteria

### Deployment Success
- ✅ Function deployed without errors
- ✅ Both schedulers created
- ✅ Test notification works
- ✅ Production notification works
- ✅ Test scheduler paused/deleted

### Production Success
- ✅ Notifications sent every 10 minutes
- ✅ Only users with tasks receive notifications
- ✅ Notification data includes taskIds
- ✅ Flutter app handles notifications correctly
- ✅ No errors in logs
- ✅ Users can tap notification to view tasks

---

## Support & Documentation

### Quick Reference
- `QUICK_START_NOTIFICATIONS.md` - Fast deployment guide
- `NOTIFICATION_CHANGES_SUMMARY.md` - Complete overview
- `NOTIFICATION_FLOW.md` - Architecture and data flow
- `TEST_NOTIFICATION_SCHEDULER.md` - Test scheduler details
- `manage-test-scheduler.sh` - Management script

### Commands Cheat Sheet
```bash
# Deploy
./deploy.sh

# Test
./manage-test-scheduler.sh trigger

# Pause test
./manage-test-scheduler.sh pause

# Status
./manage-test-scheduler.sh status

# Logs
./manage-test-scheduler.sh logs

# Delete test
./manage-test-scheduler.sh delete
```

---

## Sign-Off

### Before Going Live
- [ ] All tests passed
- [ ] Test scheduler paused or deleted
- [ ] Production scheduler running every 10 minutes
- [ ] Flutter app tested and working
- [ ] Documentation reviewed
- [ ] Team trained on management scripts
- [ ] Monitoring set up
- [ ] Rollback plan understood

**Deployed by:** _________________  
**Date:** _________________  
**Verified by:** _________________  
**Date:** _________________  

---

## Notes

_Add any deployment-specific notes, issues encountered, or special configurations here:_

---




