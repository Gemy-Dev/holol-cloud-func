# Flutter Function: Create Plan Tasks

## Dart Implementation

Add this to your Flutter data source or repository:

```dart
import 'dart:convert';
import 'package:firebase_auth/firebase_auth.dart' show FirebaseAuth, User;
import 'package:http/http.dart' as http;
import 'package:medical_advisor/core/constants/app_constants.dart';
import 'package:medical_advisor/data/models/plan_model.dart';

/// Creates tasks for an approved plan via Cloud Function
/// 
/// The function extracts all necessary data from the plan:
/// - Product IDs from plan.targetProductsSales
/// - Client criteria from plan.departmentsIds and plan.cities
/// 
/// [plan] - The plan model to create tasks for (must have id, targetProductsSales, departmentsIds, and cities)
/// 
/// Throws [Exception] if the operation fails
Future<void> createPlanTasks({
  required PlanModel plan,
}) async {
  final User? user = FirebaseAuth.instance.currentUser;
  if (user == null) {
    throw Exception('User not authenticated');
  }

  final String? token = await user.getIdToken();
  final Uri url = Uri.parse(AppConstants.cloudFunUrl);

  // Prepare request payload - only send the plan
  final Map<String, dynamic> payload = {
    'action': 'createPlanTasks',
    'plan': plan.toJson(),
  };

  final http.Response response = await http.post(
    url,
    headers: <String, String>{
      'Content-Type': 'application/json',
      'Authorization': 'Bearer $token',
    },
    body: jsonEncode(payload),
  );

  if (response.statusCode == 200) {
    final Map<String, dynamic> result = jsonDecode(response.body);
    if (result['success'] == true) {
      print('✅ Tasks created successfully: ${result['tasksCreated']} created, ${result['tasksSkipped']} skipped');
      return;
    } else {
      throw Exception('Failed to create plan tasks: ${result['error']}');
    }
  } else {
    final Map<String, dynamic> error = jsonDecode(response.body);
    throw Exception(
      'Failed to create plan tasks: ${response.statusCode} - ${error['error'] ?? response.body}',
    );
  }
}
```

## Usage Example

```dart
try {
  await createPlanTasks(plan: myPlan);
  print('Tasks created successfully!');
} catch (e) {
  print('Error creating tasks: $e');
}
```

## Response Format

**Success (200):**
```json
{
  "success": true,
  "message": "Created 150 tasks, skipped 10 duplicates",
  "tasksCreated": 150,
  "tasksSkipped": 10,
  "planId": "plan_123"
}
```

**Error (400/500):**
```json
{
  "success": false,
  "error": "Error message here"
}
```

## Notes

- The function automatically extracts all data from the Plan model:
  - **Product IDs**: Extracted from `plan.targetProductsSales` (each `TargetProductSales` has a `productId`)
  - **Client criteria**: Uses `plan.departmentsIds` and `plan.cities` to fetch eligible clients
- The function automatically checks for duplicate tasks (same planId, clientId, productId, and marketingTask)
- Only creates tasks for clients with `state == "approved"`
- Tasks are created one at a time to ensure duplicate checking works correctly
- The plan must have:
  - Valid `id` (not empty)
  - `targetProductsSales` with at least one product
  - `departmentsIds` with at least one department
  - `cities` with at least one city

