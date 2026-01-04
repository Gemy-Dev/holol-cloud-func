# Quick Start - Notification System

## 🚀 Deploy Everything

```bash
./deploy.sh
```

This creates:
- ✅ Cloud Function with notification handlers
- ✅ Production scheduler (every 10 minutes)
- ✅ Test scheduler (every 1 minute)

---

## 🧪 Test Immediately

### Option 1: Wait 1 Minute
The test scheduler will automatically send notifications every minute.

### Option 2: Trigger Now
```bash
./manage-test-scheduler.sh trigger
```

---

## ⏸️ Pause Test Scheduler (After Testing)

```bash
./manage-test-scheduler.sh pause
```

**⚠️ IMPORTANT**: The test scheduler sends notifications **every minute** to all users. Always pause or delete it after testing!

---

## 📊 Check Status

```bash
./manage-test-scheduler.sh status
```

---

## 🗑️ Delete Test Scheduler (When Done)

```bash
./manage-test-scheduler.sh delete
```

---

## 📱 What Users Receive

### Production Notifications (Every 10 Minutes)
**When**: User has tasks due today  
**Title**: تذكير بالمهام  
**Body**: 
- 1 task: "عندك اليوم مهمة: زيارة عميل"
- Multiple: "عندك اليوم 3 مهام"

**Data**:
```json
{
  "taskCount": "3",
  "taskIds": "task1,task2,task3",
  "date": "2025-12-30",
  "action": "daily_tasks"
}
```

### Test Notifications (Every 1 Minute)
**When**: Every minute (all users)  
**Title**: اختبار الإشعارات  
**Body**: "رسالة اختبارية - الوقت: 14:30:45"

**Data**:
```json
{
  "timestamp": "14:30:45",
  "action": "test_notification",
  "testId": "1234"
}
```

---

## 🎯 Common Commands

```bash
# Deploy
./deploy.sh

# Test now
./manage-test-scheduler.sh trigger

# Pause test
./manage-test-scheduler.sh pause

# Resume test
./manage-test-scheduler.sh resume

# Delete test
./manage-test-scheduler.sh delete

# View status
./manage-test-scheduler.sh status

# View logs
./manage-test-scheduler.sh logs
```

---

## ✅ Pre-Production Checklist

- [ ] Deploy: `./deploy.sh`
- [ ] Test notifications work
- [ ] Verify Flutter app receives data
- [ ] **Pause test scheduler**: `./manage-test-scheduler.sh pause`
- [ ] Verify production scheduler runs every 10 minutes
- [ ] Monitor logs for errors

---

## 📚 Full Documentation

- `NOTIFICATION_CHANGES_SUMMARY.md` - Complete overview
- `NOTIFICATION_SCHEDULE_UPDATE.md` - Production scheduler
- `TEST_NOTIFICATION_SCHEDULER.md` - Test scheduler details
- `manage-test-scheduler.sh` - Management script

---

## 🆘 Help

### No notifications?
```bash
./manage-test-scheduler.sh trigger
./manage-test-scheduler.sh logs
```

### Too many notifications?
```bash
./manage-test-scheduler.sh pause
```

### Need to start over?
```bash
./manage-test-scheduler.sh delete
./deploy.sh
```




