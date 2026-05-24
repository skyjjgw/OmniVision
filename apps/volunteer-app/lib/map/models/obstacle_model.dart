import 'package:latlong2/latlong.dart';
import '../../config/app_config.dart';

enum ObstacleType {
  construction, // 施工
  barrier,      // 路障
  crowd,        // 人群聚集
  other,        // 其他
  shop,         // 商家店铺
}

enum VerificationStatus {
  active,             // 活跃中
  candidateForRemoval,// 待移除 (已被穿越)
  removed,            // 已移除
}

class Obstacle {
  final String id;
  final LatLng position;
  final ObstacleType type;
  final double radius; // 影响半径 (米)
  final DateTime createdAt;
  
  // 验证相关字段
  VerificationStatus status;
  int clearCount; // 成功穿越次数
  DateTime lastVerifiedAt;
  
  // 场景图片
  List<String> imageUrls;
  String? title;       // 标题/名称 (如店铺名)
  String? description; // 场景描述/POI名称/货物分区详情
  List<Comment> comments; // 评论列表
  String? disputeStatus; // 争议状态 (new_pending, pending_review)

  Obstacle({
    required this.id,
    required this.position,
    this.type = ObstacleType.other,
    this.radius = 20.0, // 默认 20米半径
    required this.createdAt,
    this.status = VerificationStatus.active,
    this.clearCount = 0,
    DateTime? lastVerifiedAt,
    this.imageUrls = const [],
    this.title,
    this.description,
    this.comments = const [],
    this.disputeStatus,
  }) : lastVerifiedAt = lastVerifiedAt ?? createdAt;

  // 模拟从 JSON 解析
  factory Obstacle.fromJson(Map<String, dynamic> json) {
    // Safe Enum Parsing
    ObstacleType safeType = ObstacleType.other;
    if (json['type'] != null) {
      int typeIdx = json['type'];
      if (typeIdx >= 0 && typeIdx < ObstacleType.values.length) {
        safeType = ObstacleType.values[typeIdx];
      }
    }

    VerificationStatus safeStatus = VerificationStatus.active;
    if (json['status'] != null) {
      int statusIdx = json['status'];
      if (statusIdx >= 0 && statusIdx < VerificationStatus.values.length) {
        safeStatus = VerificationStatus.values[statusIdx];
      }
    }

    DateTime parseDate(dynamic val) {
      if (val == null) return DateTime.now();
      if (val is String) {
        try {
          return DateTime.parse(val);
        } catch (_) {
          return DateTime.now();
        }
      }
      if (val is int || val is double) {
        return DateTime.fromMillisecondsSinceEpoch(((val as num) * 1000).round());
      }
      return DateTime.now();
    }

    double parseDouble(dynamic val) {
      if (val == null) return 0.0;
      if (val is num) return val.toDouble();
      if (val is String) {
        return double.tryParse(val) ?? 0.0;
      }
      return 0.0;
    }

    return Obstacle(
      id: json['id'],
      position: LatLng(
        parseDouble(json['lat']),
        parseDouble(json['lng']),
      ),
      type: safeType,
      radius: parseDouble(json['radius']),
      createdAt: parseDate(json['createdAt']),
      status: safeStatus,
      clearCount: json['clearCount'] ?? 0,
      lastVerifiedAt: parseDate(json['lastVerifiedAt']),
      imageUrls: (json['imageUrls'] as List<dynamic>?)?.map((e) => e.toString().replaceAll('10.0.2.2', AppConfig.serverIp).replaceAll('47.97.184.133', AppConfig.serverIp)).toList() ?? [],
      title: json['title'],
      description: json['description'],
      comments: (json['comments'] as List<dynamic>?)?.map((e) => Comment.fromJson(e)).toList() ?? [],
      disputeStatus: json['disputeStatus'],
    );
  }

  // 模拟转为 JSON
  Map<String, dynamic> toJson() {
    return {
      'id': id,
      'lat': position.latitude,
      'lng': position.longitude,
      'type': type.index,
      'radius': radius,
      'createdAt': createdAt.toIso8601String(),
      'status': status.index,
      'clearCount': clearCount,
      'lastVerifiedAt': lastVerifiedAt.toIso8601String(),
      'imageUrls': imageUrls,
      'title': title,
      'description': description,
      'comments': comments.map((e) => e.toJson()).toList(),
      'disputeStatus': disputeStatus,
    };
  }
}

class Comment {
  final String content;
  final String author;
  final double rating;
  final DateTime createdAt;
  final String? imageUrl; // 新增：评论图片

  Comment({
    required this.content,
    this.author = "志愿者",
    this.rating = 5.0,
    required this.createdAt,
    this.imageUrl,
  });

  factory Comment.fromJson(Map<String, dynamic> json) {
    return Comment(
      content: json['content'],
      author: json['author'] ?? "志愿者",
      rating: (json['rating'] ?? 5.0).toDouble(),
      createdAt: json['createdAt'] != null ? DateTime.parse(json['createdAt']) : DateTime.now(),
      imageUrl: json['imageUrl'],
    );
  }

  Map<String, dynamic> toJson() {
    return {
      'content': content,
      'author': author,
      'rating': rating,
      'createdAt': createdAt.toIso8601String(),
      'imageUrl': imageUrl,
    };
  }
}
