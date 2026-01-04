# Get All Tasks Stats API

## Overview

This is an admin function that retrieves statistics for **ALL tasks** in the system, aggregated by date. Unlike `get_task_stats` which filters by user ID, this function returns task counts for all tasks regardless of assignment.

## Function Details

**Function Name:** `get_all_tasks_stats`

**Route:** `getAllTasksStats`

**Authentication:** Not required (admin function)

## Request

### Endpoint
```
POST /app
```

### Body
```json
{
  "action": "getAllTasksStats"
}
```

## Response

### Success Response (200)
```json
{
  "success": true,
  "data": [
    {
      "date": "2025-12-25",
      "count": 15
    },
    {
      "date": "2025-12-26",
      "count": 23
    },
    {
      "date": "2025-12-27",
      "count": 18
    },
    {
      "date": "2025-12-30",
      "count": 32
    }
  ]
}
```

### Error Response (500)
```json
{
  "error": "Failed to get stats: <error message>",
  "success": false
}
```

## Data Format Handling

The function handles various `targetDate` formats:

### Supported Formats

1. **Python datetime/date objects**
   ```python
   datetime(2025, 12, 30, 10, 30, 0)
   date(2025, 12, 30)
   ```

2. **ISO Format Strings**
   ```
   "2025-12-30T10:30:00"
   "2025-12-30T10:30:00Z"
   "2025-12-30"
   ```

3. **Timestamp (Integer)**
   - Milliseconds: `1735554000000`
   - Seconds: `1735554000`

4. **RFC-2822/HTTP Date**
   ```
   "Mon, 30 Dec 2025 10:30:00 GMT"
   ```

5. **Common String Formats**
   ```
   "2025-12-30 10:30:00"
   "30/12/2025"
   "12/30/2025"
   "Dec 30, 2025"
   "December 30, 2025"
   ```

## Comparison with get_task_stats

| Feature | get_task_stats | get_all_tasks_stats |
|---------|---------------|---------------------|
| **Filter** | Only tasks for specific user | ALL tasks in system |
| **Requires Auth** | Yes (user must be logged in) | No (admin function) |
| **Use Case** | User dashboard | Admin overview |
| **Parameters** | `decoded_token`, `db` | `db` only |
| **Query** | `where("assignedToId", "==", uid)` | No where clause (all documents) |

## Use Cases

### 1. Admin Dashboard
Display overall task distribution across all users and dates:

```javascript
const response = await fetch(FUNCTION_URL, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ action: 'getAllTasksStats' })
});

const stats = await response.json();
renderAdminChart(stats.data);
```

### 2. Analytics & Reporting
Generate reports on task creation patterns:

```javascript
const stats = await fetchStats();

// Calculate totals
const totalTasks = stats.data.reduce((sum, item) => sum + item.count, 0);
const busiestDay = stats.data.reduce((max, item) => 
  item.count > max.count ? item : max, stats.data[0]
);

console.log(`Total tasks: ${totalTasks}`);
console.log(`Busiest day: ${busiestDay.date} with ${busiestDay.count} tasks`);
```

### 3. Capacity Planning
Identify peak task days for resource allocation:

```javascript
const stats = await fetchStats();
const sortedByCount = stats.data.sort((a, b) => b.count - a.count);

// Get top 10 busiest days
const top10 = sortedByCount.slice(0, 10);
console.log('Top 10 busiest days:', top10);
```

## Example Usage

### cURL
```bash
curl -X POST https://your-function-url \
  -H "Content-Type: application/json" \
  -d '{"action":"getAllTasksStats"}'
```

### JavaScript/Fetch
```javascript
async function getAllTasksStats() {
  try {
    const response = await fetch('https://your-function-url', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ action: 'getAllTasksStats' })
    });
    
    const result = await response.json();
    
    if (result.success) {
      console.log('Task statistics:', result.data);
      return result.data;
    } else {
      console.error('Error:', result.error);
      return null;
    }
  } catch (error) {
    console.error('Request failed:', error);
    return null;
  }
}
```

### Python/Requests
```python
import requests

def get_all_tasks_stats():
    url = "https://your-function-url"
    data = {"action": "getAllTasksStats"}
    
    try:
        response = requests.post(url, json=data)
        result = response.json()
        
        if result.get("success"):
            return result.get("data")
        else:
            print(f"Error: {result.get('error')}")
            return None
    except Exception as e:
        print(f"Request failed: {e}")
        return None

# Usage
stats = get_all_tasks_stats()
if stats:
    for stat in stats:
        print(f"{stat['date']}: {stat['count']} tasks")
```

## Security Considerations

Since this function doesn't require authentication and returns data for all tasks:

1. **Admin Only**: Ensure only admin users access this endpoint
2. **Network Security**: Use Cloud Functions IAM policies to restrict access
3. **Monitoring**: Log all requests to this endpoint
4. **Rate Limiting**: Consider implementing rate limiting to prevent abuse

### Example IAM Restriction
```bash
# Allow only specific service account or user
gcloud functions add-iam-policy-binding app \
  --region=us-central1 \
  --member=user:admin@yourdomain.com \
  --role=roles/cloudfunctions.invoker
```

## Performance Considerations

- **Full Collection Scan**: Queries all tasks in the system
- **Large Datasets**: May be slow with thousands of tasks
- **Memory**: Processes all documents in memory before returning
- **Caching**: Consider caching results for admin dashboards

## Optimization Tips

### 1. Add Date Range Filter
If you only need recent stats, add a date filter:

```python
from datetime import datetime, timedelta

# Only last 30 days
cutoff_date = datetime.utcnow() - timedelta(days=30)
tasks_query = db.collection("tasks").where("targetDate", ">=", cutoff_date.date()).stream()
```

### 2. Use Aggregated Queries
For large datasets, consider using Firestore aggregations:

```python
from google.cloud.firestore_v1.base_query import FieldFilter

tasks_query = db.collection("tasks").aggregate([
    {
        "alias": "task_count_by_date",
        "field": "targetDate",
        "operator": "count"
    }
])
```

### 3. Cache Results
```python
import time

# Simple in-memory cache (for demo)
_cache = {
    'data': None,
    'timestamp': 0,
    'ttl': 300  # 5 minutes
}

def get_all_tasks_stats_cached(db):
    now = time.time()
    if _cache['data'] and (now - _cache['timestamp']) < _cache['ttl']:
        return _cache['data']
    
    result = get_all_tasks_stats(db)
    _cache['data'] = result
    _cache['timestamp'] = now
    return result
```

## Error Handling

### Common Errors

**1. Task Count Not Updated**
- Cause: `targetDate` field is missing or None
- Solution: Ensure all tasks have `targetDate` set

**2. Unexpected Date Formats**
- Cause: Custom date format not recognized
- Solution: Check logs and add format to parsing logic

**3. Memory Issues**
- Cause: Too many tasks in memory
- Solution: Implement pagination or date range filtering

### Debugging
```python
# Add debug logging
for doc in tasks_query:
    task = doc.to_dict()
    target_date = task.get("targetDate")
    print(f"Task {doc.id}: targetDate={target_date} (type: {type(target_date)})")
```

## Testing

### Test Script
```python
import requests

def test_get_all_tasks_stats():
    # Test successful request
    response = requests.post(
        "https://your-function-url",
        json={"action": "getAllTasksStats"}
    )
    
    assert response.status_code == 200
    result = response.json()
    
    assert result["success"] == True
    assert "data" in result
    assert isinstance(result["data"], list)
    
    # Check data structure
    if result["data"]:
        first_item = result["data"][0]
        assert "date" in first_item
        assert "count" in first_item
        assert isinstance(first_item["count"], int)
    
    print("✅ Test passed!")

if __name__ == "__main__":
    test_get_all_tasks_stats()
```

## Related Functions

- `get_task_stats()` - Get stats for specific user
- `get_tasks_by_date_range()` - Get tasks within date range
- `get_all_tasks()` - Get all tasks (not just stats)

## Future Enhancements

1. **Pagination**: Add `limit` and `offset` parameters
2. **Date Range**: Add `startDate` and `endDate` filters
3. **Grouping**: Support grouping by user, status, etc.
4. **Caching**: Implement Redis or Memcached for results
5. **Real-time**: Add Firestore real-time listener support


