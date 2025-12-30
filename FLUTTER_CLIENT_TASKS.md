# Auto-Create Tasks for New Client

## Overview
When a new client is created (or approved), this endpoint automatically creates tasks for that client based on matching plans.

## How It Works

1. **Finds Matching Plans**: Searches for plans where:
   - Client's `city` is in the plan's `cities` list
   - Client's `department` is in the plan's `departmentsIds` list
   - Client's `id` is **NOT** in the plan's `clientsIds` list (to avoid duplicate task creation)

2. **Filters Products**: For each matching plan:
   - Gets product IDs from `plan.targetProductSales`
   - Filters products where client's `department` is in the product's `departmentsIds`

3. **Creates Tasks**: For each filtered product:
   - Extracts influencer doctors from client's `additionalInfo`
   - Creates tasks for each influencer doctor × product × marketing task combination

## Prerequisites

- Client must be **approved** (`state == "approved"`)
- Client must have **influencer doctors** in `additionalInfo.doctors` where `isInfluencer == true`
- Matching plans must exist with products that have marketing tasks

## API Endpoint

```dart
POST https://your-cloud-function-url/app
```

### Request Body

```dart
{
  "action": "createTasksForNewClient",
  "client": {
    "id": "client_id",
    "name": "Client Name",
    "city": "بغداد",
    "department": "dept_id",
    "state": "approved",
    "clientType": "hospital",
    "sectorType": "private",
    "region": "Iraq",
    "priority": "high",
    "specialty": "specialty_id",
    "creatorId": "creator_id",
    "createdAt": "2024-01-01T00:00:00Z",
    "updatedAt": "2024-01-01T00:00:00Z",
    "logoUrl": "https://...",
    "additionalInfo": {
      "doctors": [
        {
          "name": "Dr. Ahmed",
          "phone": "+9647000000000",
          "email": "doctor@example.com",
          "isInfluencer": true
        }
      ],
      "procedures": [],
      "notes": ""
    }
  }
}
```

### Response (Success)

```dart
{
  "success": true,
  "message": "Created 15 tasks for client across 2 plans",
  "clientId": "client_id",
  "tasksCreated": 15,
  "tasksSkipped": 3,  // Tasks that already existed
  "matchingPlans": 2,
  "plansProcessed": [
    {
      "planId": "plan_id_1",
      "planTitle": "Q1 2024 Plan",
      "tasksCreated": 10,
      "tasksSkipped": 2,
      "productsProcessed": 5
    },
    {
      "planId": "plan_id_2",
      "planTitle": "Q2 2024 Plan",
      "tasksCreated": 5,
      "tasksSkipped": 1,
      "productsProcessed": 3
    }
  ],
  "influencerDoctorsCount": 3
}
```

### Response (No Matching Plans)

```dart
{
  "success": true,
  "message": "No matching plans found for this client",
  "clientId": "client_id",
  "clientCity": "بغداد",
  "clientDepartment": "dept_id",
  "tasksCreated": 0
}
```

### Response (Not Approved)

```dart
{
  "success": true,
  "message": "Client is not approved yet. No tasks created.",
  "clientId": "client_id",
  "clientState": "قيد المراجعة",
  "tasksCreated": 0
}
```

### Response (No Influencer Doctors)

```dart
{
  "success": true,
  "message": "Client has no influencer doctors. No tasks created.",
  "clientId": "client_id",
  "matchingPlans": 2,
  "tasksCreated": 0
}
```

### Response (Error)

```dart
{
  "success": false,
  "error": "Failed to create tasks for new client: <error details>",
  "clientId": "client_id"
}
```

## Flutter Implementation

### 1. Remote Data Source

```dart
import 'dart:convert';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:http/http.dart' as http;
import 'package:medical_advisor/core/constants/app_constants.dart';
import 'package:medical_advisor/data/models/client_model.dart';

abstract class ClientTaskRemoteDataSource {
  Future<Map<String, dynamic>> createTasksForNewClient(ClientModel client);
}

class ClientTaskRemoteDataSourceImpl implements ClientTaskRemoteDataSource {
  @override
  Future<Map<String, dynamic>> createTasksForNewClient(ClientModel client) async {
    final User? user = FirebaseAuth.instance.currentUser;
    if (user == null) {
      throw Exception('User not authenticated');
    }

    final String? token = await user.getIdToken();
    final Uri url = Uri.parse(AppConstants.cloudFunUrl);

    final http.Response response = await http.post(
      url,
      headers: <String, String>{
        'Content-Type': 'application/json',
        'Authorization': 'Bearer $token',
      },
      body: jsonEncode(<String, dynamic>{
        'action': 'createTasksForNewClient',
        'client': client.toJson(),
      }),
    );

    if (response.statusCode == 200) {
      return jsonDecode(response.body) as Map<String, dynamic>;
    } else {
      throw Exception(
        'Failed to create tasks for client: ${response.statusCode} - ${response.body}',
      );
    }
  }
}
```

### 2. Usage Example

```dart
// After creating a new client in Firestore
Future<void> onClientCreated(ClientModel newClient) async {
  try {
    // Call the cloud function to auto-create tasks
    final result = await clientTaskRemoteDataSource.createTasksForNewClient(newClient);
    
    if (result['success'] == true) {
      final tasksCreated = result['tasksCreated'] as int;
      final matchingPlans = result['matchingPlans'] as int;
      
      print('✅ Created $tasksCreated tasks across $matchingPlans plans');
      
      // Show success message to user
      showSnackbar('تم إنشاء $tasksCreated مهمة للعميل الجديد');
    } else {
      print('⚠️ ${result['message']}');
    }
  } catch (e) {
    print('❌ Error creating tasks: $e');
    // Handle error appropriately
  }
}
```

### 3. Call After Client Approval

```dart
// When approving a client
Future<void> approveClient(ClientModel client) async {
  try {
    // Update client state to approved
    final updatedClient = client.copyWith(
      state: ReviewState.approved,
      updatedAt: DateTime.now(),
    );
    
    // Save to Firestore
    await firestore.collection('clients').doc(client.id).update(updatedClient.toJson());
    
    // Auto-create tasks for the approved client
    await onClientCreated(updatedClient);
    
  } catch (e) {
    print('Error approving client: $e');
    rethrow;
  }
}
```

## Important Notes

1. **Duplicate Prevention**: The function has two levels of duplicate prevention:
   - **Plan Level**: Excludes plans that already have the client's ID in their `clientsIds` list
   - **Task Level**: Checks for existing tasks before creating new ones

2. **Task Structure**: Tasks are created with:
   - `taskType`: "planned"
   - `assignedToId`: First sales representative from plan
   - `planId`: The matching plan ID
   - `clientId`: The new client's ID
   - `productId`: The product ID
   - `doctorName`: The influencer doctor's name
   - `status`: "قيد الانجاز" (In Progress)
   - `state`: "قيد المراجعة" (Under Review)
   - `priority`: From client's priority
   - `marketingTask`: The marketing task data

3. **Performance**: The function processes all matching plans in a single call, so it may take a few seconds if there are many plans and products

4. **Error Handling**: Individual task creation errors are collected in `taskErrors` array but don't stop the overall process

5. **Best Practice**: Call this function:
   - After creating a new client (if already approved)
   - After approving a pending client
   - Do NOT call it for rejected clients or clients under review

## Testing

```bash
# Test with cURL
curl -X POST https://your-cloud-function-url/app \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_FIREBASE_TOKEN" \
  -d '{
    "action": "createTasksForNewClient",
    "client": {
      "id": "test_client_id",
      "name": "Test Hospital",
      "city": "بغداد",
      "department": "dept_id",
      "state": "approved",
      "additionalInfo": {
        "doctors": [
          {
            "name": "Dr. Test",
            "phone": "+9647000000000",
            "email": "test@example.com",
            "isInfluencer": true
          }
        ]
      }
    }
  }'
```

