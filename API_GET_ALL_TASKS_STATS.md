# Quick Reference: get_all_tasks_stats

## 🎯 Purpose

Get statistics for **ALL tasks** in the system, grouped by date. This is an admin function with no user filter.

## 📝 Request

```bash
curl -X POST https://your-function-url \
  -H "Content-Type: application/json" \
  -d '{"action":"getAllTasksStats"}'
```

## 📊 Response

```json
{
  "success": true,
  "data": [
    {"date": "2025-12-25", "count": 15},
    {"date": "2025-12-26", "count": 23},
    {"date": "2025-12-27", "count": 18},
    {"date": "2025-12-30", "count": 32}
  ]
}
```

## 🆚 Comparison

| Feature | get_task_stats | get_all_tasks_stats |
|---------|---------------|---------------------|
| **Scope** | User's tasks only | **ALL tasks** |
| **Auth Required** | ✅ Yes | ❌ No |
| **Route** | `getStats` | `getAllTasksStats` |
| **Parameters** | User ID | None |
| **Use Case** | User dashboard | **Admin overview** |

## 📱 Use Cases

### 1. Admin Dashboard
Show overall task distribution across all users.

### 2. Analytics & Reports
Generate reports on task creation patterns.

### 3. Capacity Planning
Identify peak task days for resource allocation.

## 🔒 Security

⚠️ **Admin Only** - No authentication required, so restrict access:

```bash
# IAM restriction example
gcloud functions add-iam-policy-binding app \
  --region=us-central1 \
  --member=user:admin@yourdomain.com \
  --role=roles/cloudfunctions.invoker
```

## 🧪 Testing

```bash
# Test locally (if deployed)
curl -X POST https://your-function-url \
  -H "Content-Type: application/json" \
  -d '{"action":"getAllTasksStats"}'
```

## 📚 Documentation

Full details in: `ALL_TASKS_STATS.md`

## 📦 Code Location

- **Function**: `modules/tasks.py` → `get_all_tasks_stats()`
- **Route**: `app.py` → `getAllTasksStats`
- **Lines**: ~1291-1360 in `tasks.py`

## 🎨 Data Format

Handles various `targetDate` formats:
- ISO dates: `"2025-12-30"`
- Timestamps: `1735554000000`
- DateTime objects
- RFC-2822 dates
- And more...

See `ALL_TASKS_STATS.md` for complete list.

---

## ✅ Quick Test

```javascript
// JavaScript example
fetch('https://your-function-url', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ action: 'getAllTasksStats' })
})
.then(r => r.json())
.then(data => console.log('Stats:', data.data));
```

