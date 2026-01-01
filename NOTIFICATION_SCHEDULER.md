# Task Notification Schedule

## Overview

The app sends task notifications at two specific times daily in **Iraq time (UTC+3)**:

1. **8:00 AM** - Notifies users of tasks due **today**
2. **8:00 PM** - Notifies users of tasks due **tomorrow**

This allows users to be reminded in advance (8 PM the previous day) of upcoming tasks.

## How It Works

### Time Zone Handling

- **Iraq Time (AST)**: UTC+3
- **Scheduler Time**: Cloud Scheduler uses UTC
- **Conversion**:
  - 8 AM Iraq (UTC+3) = 5 AM UTC
  - 8 PM Iraq (UTC+3) = 5 PM UTC

The notification system automatically converts times to Iraq time (UTC+3) when calculating which tasks to send.

### Task Matching Logic

```python
# Calculated in Iraq timezone
iraq_now = datetime.utcnow() + timedelta(hours=3)

# 8 AM notification (days_offset=0)
target_date = today_in_iraq_time
# Sends notifications for all tasks with targetDate == today

# 8 PM notification (days_offset=1)  
target_date = tomorrow_in_iraq_time
# Sends notifications for all tasks with targetDate == tomorrow
```

## Setup

### 1. Enable Cloud Scheduler

```bash
gcloud services enable cloudscheduler.googleapis.com
```

### 2. Create Scheduled Jobs

Run the automated setup script:

```bash
chmod +x setup-notification-scheduler.sh
./setup-notification-scheduler.sh
```

This creates two Cloud Scheduler jobs:
- `notify-today-tasks` - Runs at 5 AM UTC (8 AM Iraq)
- `notify-tomorrow-tasks` - Runs at 5 PM UTC (8 PM Iraq)

### 3. Manual Job Management

**List all scheduled jobs:**
```bash
gcloud scheduler jobs list --location=us-central1
```

**Manually trigger a job:**
```bash
gcloud scheduler jobs run notify-today-tasks --location=us-central1
gcloud scheduler jobs run notify-tomorrow-tasks --location=us-central1
```

**Pause/Resume jobs:**
```bash
gcloud scheduler jobs pause notify-today-tasks --location=us-central1
gcloud scheduler jobs resume notify-today-tasks --location=us-central1
```

**Delete a job:**
```bash
gcloud scheduler jobs delete notify-today-tasks --location=us-central1
```

## API Endpoints

### Direct API Calls

You can also trigger notifications via the API:

**8 AM (Today's tasks):**
```bash
curl -X POST https://your-region-your-project.cloudfunctions.net/app \
  -H "Content-Type: application/json" \
  -d '{"action":"notify_today_tasks"}'
```

**8 PM (Tomorrow's tasks):**
```bash
curl -X POST https://your-region-your-project.cloudfunctions.net/app \
  -H "Content-Type: application/json" \
  -d '{"action":"notify_tomorrow_tasks"}'
```

## Notification Content

### 8 AM Notification (Today's Tasks)

**Arabic:** "عندك اليوم مهمة:" (You have a task today:)
- Single task: Shows task title
- Multiple tasks: Shows count "عندك اليوم X مهام"

### 8 PM Notification (Tomorrow's Tasks)

**Arabic:** "عندك غدا مهمة:" (You have a task tomorrow:)
- Single task: Shows task title
- Multiple tasks: Shows count "عندك غدا X مهام"

## Date Format Support

The system supports multiple date formats for `targetDate` field in Firestore:

- **ISO 8601**: `2026-01-13` or `2026-01-13T00:00:00Z`
- **Firestore Timestamps**: `{seconds: 1672531200, nanoseconds: 0}`
- **ISO String with Timezone**: `2026-01-13T00:00:00+03:00`
- **Human Readable**: `Jan 13, 2026` or `01/13/2026`
- **Unix Timestamps**: Seconds or milliseconds since epoch
- **RFC-2822**: `Mon, 13 Jan 2026 00:00:00 UTC`

All dates are normalized to `YYYY-MM-DD` format internally for comparison.

## Example Firestore Task Document

```json
{
  "assignedToId": "user123",
  "title": "Check patient vitals",
  "targetDate": "2026-01-13",
  "status": "pending",
  "createdAt": {...}
}
```

## Troubleshooting

### Jobs Not Running

1. **Verify service account has permissions:**
   ```bash
   gcloud projects get-iam-policy $(gcloud config get-value project) \
     --flatten="bindings[].members" \
     --format="table(bindings.role)" \
     --filter="bindings.members:serviceAccount:*"
   ```

2. **Check Cloud Function logs:**
   ```bash
   gcloud functions logs read app --limit 50
   ```

3. **Manually test the function:**
   ```bash
   gcloud scheduler jobs run notify-today-tasks --location=us-central1
   ```

### Incorrect Time Zone

If notifications are coming at the wrong time:

1. Verify Cloud Scheduler job schedule:
   ```bash
   gcloud scheduler jobs describe notify-today-tasks --location=us-central1
   ```

2. Check function logs for timezone info:
   ```bash
   gcloud functions logs read app --limit 20 | grep "Running task notifications"
   ```

### Missing Notifications

If users aren't receiving notifications:

1. Verify users have FCM tokens registered
2. Check that tasks have `targetDate` in correct format
3. Verify Firebase Cloud Messaging is enabled
4. Check function logs for errors

## Implementation Code

### notifications.py

```python
def handle_daily_notifications(db, days_offset=0):
    """Handle task notifications for a specific date.
    
    Args:
        db: Firestore database instance
        days_offset: Days from today (0=today, 1=tomorrow, etc.)
    """
    # Calculates time in Iraq timezone (UTC+3)
    iraq_tz_offset = timedelta(hours=3)
    iraq_now = datetime.utcnow() + iraq_tz_offset
    target_date = (iraq_now.date() + timedelta(days=days_offset)).isoformat()
    
    # Sends notifications to users for tasks matching target_date
```

### app.py

```python
# Endpoints registered in route_request()
if action == "notify_today_tasks":
    return handle_daily_notifications(db, days_offset=0)

elif action == "notify_tomorrow_tasks":
    return handle_daily_notifications(db, days_offset=1)
```

## Performance Notes

- **Database Queries**: Streams all users, then queries tasks per user
- **Batch Size**: No limit; processes all users in current implementation
- **Timeout**: Cloud Functions default 60s timeout (sufficient for typical use)
- **Recommendation**: For >10K users, consider batch processing or Pub/Sub

## Future Enhancements

- [ ] User-configurable notification times
- [ ] Batch FCM message sending for better performance
- [ ] Retry logic for failed deliveries
- [ ] Notification history/analytics
- [ ] Per-user timezone support
