# Get Tasks By Date Range Client Implementation

## Overview
This document provides the Flutter client code to consume the `getTasksByDateRange` cloud function.

## 1. Remote Data Source

```dart
import 'dart:convert';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:http/http.dart' as http;
import 'package:holol_tibbiya/core/constants/app_constants.dart';
// Import your TaskModel
// import 'package:holol_tibbiya/data/models/task_model.dart'; 

/// Remote data source for fetching tasks within a date range.
abstract class TasksRemoteDataSource {
  /// Fetches tasks starting from [startDate] for the duration of [days].
  Future<List<dynamic>> getTasksByDateRange(DateTime startDate, int days);
}

/// Implementation using Firebase Cloud Function.
class TasksRemoteDataSourceImpl implements TasksRemoteDataSource {
  final FirebaseAuth firebaseAuth;

  TasksRemoteDataSourceImpl({FirebaseAuth? firebaseAuth})
      : firebaseAuth = firebaseAuth ?? FirebaseAuth.instance;

  @override
  Future<List<dynamic>> getTasksByDateRange(DateTime startDate, int days) async {
    final User? user = firebaseAuth.currentUser;
    if (user == null) {
      throw Exception('User not authenticated');
    }

    final String? token = await user.getIdToken();
    
    // Format date as YYYY-MM-DD
    final String dateStr = "${startDate.year}-${startDate.month.toString().padLeft(2, '0')}-${startDate.day.toString().padLeft(2, '0')}";

    final http.Response response = await http.post(
      Uri.parse(AppConstants.cloudFunUrl),
      headers: <String, String>{
        'Content-Type': 'application/json',
        'Authorization': 'Bearer $token',
      },
      body: jsonEncode(<String, dynamic>{
        'action': 'getTasksByDateRange',
        'date': dateStr,
        'days': days,
      }),
    );

    if (response.statusCode == 200) {
      final dynamic decodedBody = jsonDecode(response.body);
      
      if (decodedBody is Map<String, dynamic> && decodedBody['success'] == true) {
         if (decodedBody['data'] is List) {
            // Return raw list or map to TaskModel here
            // return (decodedBody['data'] as List).map((e) => TaskModel.fromJson(e)).toList();
            return decodedBody['data'] as List<dynamic>;
         }
      }
      
      throw Exception('Invalid response format: ${response.body}');
    }
    
    throw Exception('Failed to load tasks: ${response.statusCode}');
  }
}
```

## 2. Usage Example

```dart
final dataSource = TasksRemoteDataSourceImpl();

try {
  // Get tasks for the next 7 days starting today
  final tasks = await dataSource.getTasksByDateRange(DateTime.now(), 7);
  print('Loaded ${tasks.length} tasks');
  
} catch (e) {
  print('Error loading tasks: $e');
}
```
