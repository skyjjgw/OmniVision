enum ContributionType {
  storeLayout,    // 0
  obstacleStatus, // 1
  unknown2,       // 2 (Reserved)
  newObstacle,    // 3
  newShop,        // 4
}

enum ObstacleStatus {
  active,
  removed,
  unknown,
}

class ContributionComment {
  final String id;
  final String userId;
  final String userNickname;
  final String content;
  final DateTime createdAt;

  ContributionComment({
    required this.id,
    required this.userId,
    required this.userNickname,
    required this.content,
    required this.createdAt,
  });

  factory ContributionComment.fromJson(Map<String, dynamic> json) {
    try {
      return ContributionComment(
        id: json['id'] ?? '',
        userId: json['userId'] ?? '',
        userNickname: json['userNickname'] ?? 'Unknown',
        content: json['content'] ?? '',
        createdAt: json['createdAt'] != null 
            ? DateTime.fromMillisecondsSinceEpoch(((json['createdAt'] as num) * 1000).round())
            : DateTime.now(),
      );
    } catch (e) {
      print("Error parsing comment: $e");
      return ContributionComment(
        id: 'error',
        userId: '',
        userNickname: 'Error',
        content: 'Error loading comment',
        createdAt: DateTime.now(),
      );
    }
  }

  Map<String, dynamic> toJson() {
    return {
      'id': id,
      'userId': userId,
      'userNickname': userNickname,
      'content': content,
      'createdAt': createdAt.millisecondsSinceEpoch ~/ 1000,
    };
  }
}

class ContributionModel {
  final String id;
  final String markerId;
  final String markerTitle; // For display
  final ContributionType type;
  final String userId;
  final String userNickname;
  final String? userAvatar;
  final String content; // Description
  final String? imageUrl;
  final List<String>? zones; // For store layout
  final ObstacleStatus? proposedStatus; // For obstacle status
  final double? lat;
  final double? lng;
  final String? regionCode;
  final String? regionName;
  final String? cityCode;
  final String? cityName;
  final String? forumKey;
  final String? forumName;
  final String? targetType;
  final DateTime createdAt;
  final int auditPeriod; // In seconds
  final List<String> upvotes;
  final List<String> downvotes;
  final List<ContributionComment> comments;
  final int userTrustScore; // Trust Score
  final double score; // Weighted Score
  final String reviewStatus;
  final String aiAnalysisResult;
  final String blindGuidanceSummary;
  final double? aiConfidence;
  final Map<String, dynamic>? aiValidation;

  ContributionModel({
    required this.id,
    required this.markerId,
    required this.markerTitle,
    required this.type,
    required this.userId,
    required this.userNickname,
    this.userAvatar,
    required this.content,
    this.imageUrl,
    this.zones,
    this.proposedStatus,
    this.lat,
    this.lng,
    this.regionCode,
    this.regionName,
    this.cityCode,
    this.cityName,
    this.forumKey,
    this.forumName,
    this.targetType,
    required this.createdAt,
    this.auditPeriod = 864000, // Default 10 days
    this.upvotes = const [],
    this.downvotes = const [],
    this.comments = const [],
    this.userTrustScore = 100,
    this.score = 0.0,
    this.reviewStatus = 'pending',
    this.aiAnalysisResult = '',
    this.blindGuidanceSummary = '',
    this.aiConfidence,
    this.aiValidation,
  });

  factory ContributionModel.fromJson(Map<String, dynamic> json) {
    try {
      // Safe Enum Parsing
      ContributionType safeType = ContributionType.unknown2;
      if (json['type'] != null) {
        int typeIdx = json['type'];
        if (typeIdx >= 0 && typeIdx < ContributionType.values.length) {
          safeType = ContributionType.values[typeIdx];
        }
      }

      return ContributionModel(
        id: json['id'] ?? '',
        markerId: json['markerId'] ?? '',
        markerTitle: json['markerTitle'] ?? 'Unknown Location',
        type: safeType,
        userId: json['userId'] ?? '',
        userNickname: json['userNickname'] ?? 'Volunteer',
        userAvatar: json['userAvatar'],
        content: json['content'] ?? '',
        imageUrl: json['imageUrl'],
        zones: (json['zones'] as List<dynamic>?)?.map((e) => e.toString()).toList(),
        proposedStatus: (json['proposedStatus'] != null && json['proposedStatus'] is int && json['proposedStatus'] >= 0 && json['proposedStatus'] < ObstacleStatus.values.length)
            ? ObstacleStatus.values[json['proposedStatus']]
            : null,
        lat: json['lat'] != null ? (json['lat'] as num).toDouble() : null,
        lng: json['lng'] != null ? (json['lng'] as num).toDouble() : null,
        regionCode: json['regionCode']?.toString(),
        regionName: json['regionName']?.toString(),
        cityCode: json['cityCode']?.toString(),
        cityName: json['cityName']?.toString(),
        forumKey: json['forumKey']?.toString(),
        forumName: json['forumName']?.toString(),
        targetType: json['targetType']?.toString(),
        createdAt: json['createdAt'] is num
            ? DateTime.fromMillisecondsSinceEpoch(((json['createdAt'] as num) * 1000).round())
            : DateTime.now(),
        auditPeriod: json['auditPeriod'] ?? 864000,
        upvotes: (json['upvotes'] as List<dynamic>?)?.map((e) => e.toString()).toList() ?? [],
        downvotes: (json['downvotes'] as List<dynamic>?)?.map((e) => e.toString()).toList() ?? [],
        comments: (json['comments'] as List<dynamic>?)?.map((e) {
          try {
             return ContributionComment.fromJson(e);
          } catch (_) {
             return null;
          }
        }).whereType<ContributionComment>().toList() ?? [],
        userTrustScore: json['userTrustScore'] ?? 100,
        score: (json['score'] ?? 0.0).toDouble(),
        reviewStatus: (json['reviewStatus'] ?? 'pending').toString(),
        aiAnalysisResult: (json['ai_analysis_result'] ?? '').toString(),
        blindGuidanceSummary: ((json['blind_guidance_summary'] ??
                (json['ai_validation'] is Map ? json['ai_validation']['blind_guidance_summary'] : null) ??
                json['voice_prompt']) ??
            '')
            .toString(),
        aiConfidence: json['ai_confidence'] != null ? (json['ai_confidence'] as num).toDouble() : null,
        aiValidation: json['ai_validation'] is Map<String, dynamic>
            ? json['ai_validation'] as Map<String, dynamic>
            : (json['ai_validation'] is Map ? Map<String, dynamic>.from(json['ai_validation']) : null),
      );
    } catch (e) {
      print("ContributionModel parsing error for ID ${json['id']}: $e");
      // Return a dummy error object instead of crashing/rethrowing
      return ContributionModel(
        id: json['id'] ?? 'error',
        markerId: 'error',
        markerTitle: 'Error Parsing Item',
        type: ContributionType.unknown2,
        userId: '',
        userNickname: 'System',
        content: 'Error parsing this contribution: $e',
        createdAt: DateTime.now(),
      );
    }
  }

  Map<String, dynamic> toJson() {
    return {
      'id': id,
      'markerId': markerId,
      'markerTitle': markerTitle,
      'type': type.index,
      'userId': userId,
      'userNickname': userNickname,
      'userAvatar': userAvatar,
      'content': content,
      'imageUrl': imageUrl,
      'zones': zones ?? [],
      'proposedStatus': proposedStatus?.index,
      'lat': lat,
      'lng': lng,
      'regionCode': regionCode,
      'regionName': regionName,
      'cityCode': cityCode,
      'cityName': cityName,
      'forumKey': forumKey,
      'forumName': forumName,
      'targetType': targetType,
      'createdAt': createdAt.millisecondsSinceEpoch ~/ 1000,
      'auditPeriod': auditPeriod,
      'upvotes': upvotes,
      'downvotes': downvotes,
      'comments': comments.map((e) => e.toJson()).toList(),
      'userTrustScore': userTrustScore,
      'score': score,
      'reviewStatus': reviewStatus,
      'ai_analysis_result': aiAnalysisResult,
      'blind_guidance_summary': blindGuidanceSummary,
      'ai_confidence': aiConfidence,
      'ai_validation': aiValidation,
    };
  }
}
