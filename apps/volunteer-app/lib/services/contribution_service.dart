import 'dart:convert';
import 'package:http/http.dart' as http;
import '../config/app_config.dart';
import '../models/contribution_model.dart';
import 'dart:io';

class ContributionFetchResult {
  final List<ContributionModel> contributions;
  final List<Map<String, String>> forums;
  final List<Map<String, String>> regions;

  ContributionFetchResult({
    required this.contributions,
    required this.forums,
    required this.regions,
  });
}

class ContributionService {

  Future<ContributionFetchResult> fetchContributionsWithMetadata({
    String? targetType,
    String? regionCode,
    String? forumKey,
  }) async {
    try {
      final query = <String, String>{};
      if (targetType != null && targetType.isNotEmpty) query['targetType'] = targetType;
      if (regionCode != null && regionCode.isNotEmpty) query['regionCode'] = regionCode;
      if (forumKey != null && forumKey.isNotEmpty) query['forumKey'] = forumKey;
      final uri = Uri.parse('${AppConfig.apiUrl}/contribution/list').replace(queryParameters: query.isEmpty ? null : query);
      final response = await http.get(uri).timeout(const Duration(seconds: 10));
      
      if (response.statusCode == 200) {
        final bodyString = utf8.decode(response.bodyBytes, allowMalformed: true);
        final data = jsonDecode(bodyString);
        if (data['success']) {
          final List<dynamic> posts = data['posts'];
          final List<ContributionModel> parsedPosts = posts.map((e) {
            try {
              return ContributionModel.fromJson(e);
            } catch (err) {
              return null;
            }
          }).whereType<ContributionModel>().toList();

          // Backend might provide forums, but we can also extract unique regions dynamically
          final forumsList = (data['forums'] as List<dynamic>?)?.map((e) => {
            'key': e['key'].toString(),
            'name': e['name'].toString(),
          }).toList() ?? [];

          // Extract unique regions from posts
          final Set<String> regionSet = {};
          final List<Map<String, String>> extractedRegions = [];
          for (var post in parsedPosts) {
            if (post.regionCode != null && post.regionCode!.isNotEmpty && !regionSet.contains(post.regionCode)) {
               regionSet.add(post.regionCode!);
               extractedRegions.add({
                 'code': post.regionCode!,
                 'name': post.regionName ?? post.regionCode!,
               });
            }
          }

          return ContributionFetchResult(
            contributions: parsedPosts,
            forums: forumsList,
            regions: extractedRegions,
          );
        }
      }
    } catch (e) {
      print("Fetch contributions error: $e");
    }
    return ContributionFetchResult(contributions: [], forums: [], regions: []);
  }

  Future<List<ContributionModel>> fetchContributions({
    String? targetType,
    String? regionCode,
    String? forumKey,
  }) async {
    try {
      final query = <String, String>{};
      if (targetType != null && targetType.isNotEmpty) query['targetType'] = targetType;
      if (regionCode != null && regionCode.isNotEmpty) query['regionCode'] = regionCode;
      if (forumKey != null && forumKey.isNotEmpty) query['forumKey'] = forumKey;
      final uri = Uri.parse('${AppConfig.apiUrl}/contribution/list').replace(queryParameters: query.isEmpty ? null : query);
      final response = await http.get(uri).timeout(const Duration(seconds: 10));
      print("Fetch status: ${response.statusCode}");
      
      if (response.statusCode == 200) {
        // Use allowMalformed to prevent crashing on bad encoding
        final bodyString = utf8.decode(response.bodyBytes, allowMalformed: true);
        print("Fetch body: $bodyString"); // Debug log
        
        final data = jsonDecode(bodyString);
        if (data['success']) {
          final List<dynamic> posts = data['posts'];
          return posts.map((e) {
            try {
              return ContributionModel.fromJson(e);
            } catch (err) {
              print("Error parsing contribution item: $err");
              print("Problematic item: $e");
              return null;
            }
          }).whereType<ContributionModel>().toList();
        }
      }
    } catch (e) {
      print("Fetch contributions error: $e");
    }
    return [];
  }

  Future<bool> addContribution(ContributionModel contribution) async {
    try {
      final response = await http.post(
        Uri.parse('${AppConfig.apiUrl}/contribution/add'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode(contribution.toJson()),
      );
      return response.statusCode == 200;
    } catch (e) {
      print("Add contribution error: $e");
      return false;
    }
  }

  Future<bool> voteContribution(String contributionId, String userId, String voteType) async {
    try {
      final response = await http.post(
        Uri.parse('${AppConfig.apiUrl}/contribution/vote'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({
          'contributionId': contributionId,
          'userId': userId,
          'voteType': voteType,
        }),
      );
      return response.statusCode == 200;
    } catch (e) {
      print("Vote error: $e");
      return false;
    }
  }

  Future<bool> deleteContribution(String id, String userId) async {
    try {
      final response = await http.delete(
        Uri.parse('${AppConfig.apiUrl}/contribution/$id?userId=$userId'),
      );
      return response.statusCode == 200;
    } catch (e) {
      print("Delete error: $e");
      return false;
    }
  }

  Future<bool> addComment(String contributionId, String userId, String userNickname, String content) async {
    try {
      final response = await http.post(
        Uri.parse('${AppConfig.apiUrl}/contribution/$contributionId/comment'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({
          'userId': userId,
          'userNickname': userNickname,
          'content': content,
        }),
      );
      return response.statusCode == 200;
    } catch (e) {
      print("Add comment error: $e");
      return false;
    }
  }

  Future<bool> deleteComment(String contributionId, String commentId, String userId) async {
    try {
      final response = await http.delete(
        Uri.parse('${AppConfig.apiUrl}/contribution/$contributionId/comment/$commentId?userId=$userId'),
      );
      return response.statusCode == 200;
    } catch (e) {
      print("Delete comment error: $e");
      return false;
    }
  }

  Future<String?> uploadImage(File imageFile) async {
    try {
      final request = http.MultipartRequest(
        'POST',
        Uri.parse('${AppConfig.apiUrl}/contribution/upload_image'),
      );
      request.files.add(await http.MultipartFile.fromPath('file', imageFile.path));
      final response = await request.send();
      
      if (response.statusCode == 200) {
        final respStr = await response.stream.bytesToString();
        final data = jsonDecode(respStr);
        if (data['success']) {
          return data['url'];
        }
      }
    } catch (e) {
      print("Upload image error: $e");
    }
    return null;
  }
}
