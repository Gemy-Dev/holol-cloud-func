# Get Stats Client Implementation

## Overview
This document provides the Flutter client code to consume the `getStats` cloud function.

## 1. Models

```dart
/// Represents task statistics for a specific date.
typedef TableTaskStats = ({DateTime date, int count});
```

## 2. Remote Data Source

```dart
import 'dart:convert';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:http/http.dart' as http;
import 'package:holol_tibbiya/core/constants/app_constants.dart';

/// Remote data source for fetching statistics data from cloud function.
abstract class StatsRemoteDataSource {
  /// Fetches statistics data from the cloud function.
  /// Returns a list of records, each containing 'date' and 'count'.
  Future<List<TableTaskStats>> getStats();
}

/// Implementation of [StatsRemoteDataSource] using Firebase Cloud Function.
class StatsRemoteDataSourceImpl implements StatsRemoteDataSource {
  final FirebaseAuth firebaseAuth;

  StatsRemoteDataSourceImpl({FirebaseAuth? firebaseAuth})
      : firebaseAuth = firebaseAuth ?? FirebaseAuth.instance;

  @override
  Future<List<TableTaskStats>> getStats() async {
    final User? user = firebaseAuth.currentUser;
    if (user == null) {
      throw Exception('User not authenticated');
    }

    final String? token = await user.getIdToken();

    final http.Response response = await http.post(
      Uri.parse(AppConstants.cloudFunUrl),
      headers: <String, String>{
        'Content-Type': 'application/json',
        'Authorization': 'Bearer $token',
      },
      body: jsonEncode(<String, String>{'action': 'getStats'}),
    );

    if (response.statusCode == 200) {
      final dynamic decodedBody = jsonDecode(response.body);
      
      if (decodedBody is Map<String, dynamic> && decodedBody['success'] == true) {
         if (decodedBody['data'] is List) {
            return (decodedBody['data'] as List)
              .map(
                (dynamic item) => _itemToStats(Map<String, dynamic>.from(item)),
              )
              .toList();
         }
      }
      
      throw Exception('Invalid response format: ${response.body}');
    }
    
    throw Exception('Failed to load stats: ${response.statusCode}');
  }

  /// Converts a JSON item to TableTaskStats.
  TableTaskStats _itemToStats(Map<String, dynamic> item) {
    final DateTime date = _parseDate(item['date']);
    final int count = _parseCount(item['count']);
    return (date: date, count: count);
  }

  /// Parses a count value from JSON.
  int _parseCount(dynamic countValue) {
    if (countValue is int) {
      return countValue;
    } else if (countValue is String) {
      return int.tryParse(countValue) ?? 0;
    } else if (countValue is num) {
      return countValue.toInt();
    } else {
      return 0;
    }
  }

  /// Parses a date value from JSON (can be string, int timestamp, or DateTime).
  DateTime _parseDate(dynamic dateValue) {
    if (dateValue is DateTime) {
      return dateValue;
    } else if (dateValue is String) {
      return DateTime.parse(dateValue);
    } else if (dateValue is int) {
      // Handle timestamp in milliseconds
      return DateTime.fromMillisecondsSinceEpoch(dateValue);
    } else {
      throw FormatException('Invalid date format: $dateValue');
    }
  }
}
```
