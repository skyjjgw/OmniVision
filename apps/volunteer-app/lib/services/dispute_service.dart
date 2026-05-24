import 'dart:convert';
import 'package:http/http.dart' as http;
import '../config/app_config.dart';
import '../models/dispute_model.dart';

class DisputeFetchResult {
  final List<Dispute> disputes;
  final List<Map<String, String>> regions;

  DisputeFetchResult({
    required this.disputes,
    required this.regions,
  });
}

class DisputeService {
  static Future<DisputeFetchResult> getDisputesWithMetadata({
    String? regionCode,
    String? targetType,
  }) async {
    try {
      final query = <String, String>{};
      if (regionCode != null && regionCode.isNotEmpty) query['regionCode'] = regionCode;
      if (targetType != null && targetType.isNotEmpty) query['targetType'] = targetType;
      final uri = Uri.parse('${AppConfig.apiUrl}/disputes').replace(
        queryParameters: query.isEmpty ? null : query,
      );
      final response = await http.get(uri).timeout(const Duration(seconds: 10));

      if (response.statusCode == 200) {
        final data = json.decode(utf8.decode(response.bodyBytes));
        if (data['success'] == true) {
          final disputesList = (data['disputes'] as List)
              .map((item) => Dispute.fromJson(item))
              .toList();

          final backendRegions = (data['regions'] as List<dynamic>?)?.map((item) {
            final map = Map<String, dynamic>.from(item as Map);
            return {
              'code': (map['regionCode'] ?? '').toString(),
              'name': (map['regionName'] ?? map['regionCode'] ?? '').toString(),
            };
          }).where((item) => item['code']!.isNotEmpty).toList() ?? [];

          return DisputeFetchResult(
            disputes: disputesList,
            regions: backendRegions,
          );
        }
      }
    } catch (e) {
      print('Error fetching disputes: $e');
    }
    return DisputeFetchResult(disputes: [], regions: []);
  }

  static Future<List<Dispute>> getDisputes({
    String? regionCode,
    String? targetType,
  }) async {
    try {
      final query = <String, String>{};
      if (regionCode != null && regionCode.isNotEmpty) query['regionCode'] = regionCode;
      if (targetType != null && targetType.isNotEmpty) query['targetType'] = targetType;
      final uri = Uri.parse('${AppConfig.apiUrl}/disputes').replace(
        queryParameters: query.isEmpty ? null : query,
      );
      final response = await http.get(uri).timeout(const Duration(seconds: 10));

      if (response.statusCode == 200) {
        final data = json.decode(utf8.decode(response.bodyBytes));
        if (data['success'] == true) {
          return (data['disputes'] as List)
              .map((item) => Dispute.fromJson(item))
              .toList();
        }
      }
    } catch (e) {
      print('Error fetching disputes: $e');
    }
    return [];
  }

  static Future<bool> voteDispute(String disputeId, String userId, String voteOption) async {
    final response = await http.post(
      Uri.parse('${AppConfig.apiUrl}/dispute/vote'),
      headers: {'Content-Type': 'application/json'},
      body: json.encode({
        'disputeId': disputeId,
        'userId': userId,
        'voteOption': voteOption,
      }),
    );

    return response.statusCode == 200;
  }
}
