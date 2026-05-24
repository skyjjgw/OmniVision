import 'dart:async';
import 'dart:convert';
import 'package:latlong2/latlong.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';
import '../../config/app_config.dart';
import '../models/obstacle_model.dart';

class ObstacleService {
  // 单例模式
  static final ObstacleService _instance = ObstacleService._internal();
  factory ObstacleService() => _instance;
  ObstacleService._internal() {
    _loadMyObstacleIds();
    fetchObstacles(); // 初始化时拉取数据
    _startAutoSync();
  }

  Future<void> _loadMyObstacleIds() async {
    final prefs = await SharedPreferences.getInstance();
    _myObstacleIds = prefs.getStringList('my_obstacle_ids') ?? [];
  }

  Future<void> _saveMyObstacleId(String id) async {
    if (!_myObstacleIds.contains(id)) {
      _myObstacleIds.add(id);
      final prefs = await SharedPreferences.getInstance();
      await prefs.setStringList('my_obstacle_ids', _myObstacleIds);
    }
  }

  List<Obstacle> getMyObstacles() {
    return _obstacles.where((o) => _myObstacleIds.contains(o.id)).toList();
  }

  Timer? _syncTimer;

  void _startAutoSync() {
    _syncTimer?.cancel();
    _syncTimer = Timer.periodic(const Duration(seconds: 5), (timer) {
      fetchObstacles(silent: true);
    });
  }

 // 内存中存储障碍物列表
  List<Obstacle> _obstacles = [];
  List<String> _myObstacleIds = [];
  
  // 公开获取当前列表的方法
  List<Obstacle> getObstacles() {
    // 过滤掉已移除的
    return _obstacles.where((o) => o.status != VerificationStatus.removed).toList();
  }

  // 暴露给 UI 的流

  // 暴露给 UI 的流
  final _obstacleStreamController = StreamController<List<Obstacle>>.broadcast();
  Stream<List<Obstacle>> get obstacleStream => _obstacleStreamController.stream;

  // 从服务器拉取障碍物 (同时拉取店铺)
  Future<void> fetchObstacles({bool silent = false}) async {
    try {
      final ts = DateTime.now().millisecondsSinceEpoch;
      // Parallel fetch
      final responses = await Future.wait([
        http.get(Uri.parse('${AppConfig.apiUrl}/obstacles?t=$ts')),
        http.get(Uri.parse('${AppConfig.apiUrl}/shops?t=$ts')),
      ]);

      final respObstacles = responses[0];
      final respShops = responses[1];
      
      List<Obstacle> newObstacles = [];

      if (respObstacles.statusCode == 200) {
        final List<dynamic> data = jsonDecode(utf8.decode(respObstacles.bodyBytes));
        newObstacles.addAll(data.map((json) {
          try {
            return Obstacle.fromJson(json);
          } catch (e) {
            print("Error parsing obstacle: $e");
            return null;
          }
        }).whereType<Obstacle>());
      }
      
      if (respShops.statusCode == 200) {
        final List<dynamic> data = jsonDecode(utf8.decode(respShops.bodyBytes));
        newObstacles.addAll(data.map((json) {
          try {
            // Ensure logic treats it as shop
            return Obstacle.fromJson(json);
          } catch (e) {
             print("Error parsing shop: $e");
             return null;
          }
        }).whereType<Obstacle>());
      }

      // 简单比对数据是否变化，避免无效刷新
      // 实际项目可以使用更高效的比对
      
      // 只要数据有变化，或者是首次加载(空)，都更新
      // 为了确保一致性，如果是静默刷新，必须有变化才通知。
      // 但如果是手动刷新 (silent=false)，强制通知。
      bool changed = _obstacles.length != newObstacles.length || 
          !_areObstacleListsEqual(_obstacles, newObstacles);
          
      if (changed || !silent) { 
         _obstacles = newObstacles;
         _notifyListeners();
         if (!silent) print("✅ 从服务器同步了 ${_obstacles.length} 个标注 (含店铺)");
         if (silent && changed) print("🔄 自动同步更新: ${_obstacles.length} 个标注");
      }
    } catch (e) {
      if (!silent) print("⚠️ 拉取标注失败: $e");
    }
  }

  bool _areObstacleListsEqual(List<Obstacle> a, List<Obstacle> b) {
    if (a.length != b.length) return false;
    
    final aMap = {for (var o in a) o.id: o};
    final bMap = {for (var o in b) o.id: o};
    
    if (aMap.length != bMap.length) return false;

    for (final id in aMap.keys) {
        if (!bMap.containsKey(id)) return false;
        final o1 = aMap[id]!;
        final o2 = bMap[id]!;
        
        // 比较关键字段
        if (o1.type != o2.type) return false;
        if (o1.status != o2.status) return false;
        if (o1.title != o2.title) return false;
        if (o1.description != o2.description) return false;
        
        // 比较图片列表
        if (o1.imageUrls.length != o2.imageUrls.length) return false;
        // 简单比较第一张图是否变化（通常封面图变了就需要刷新）
        if (o1.imageUrls.isNotEmpty && o2.imageUrls.isNotEmpty) {
             if (o1.imageUrls.first != o2.imageUrls.first) return false;
        }
    }
    return true;
  }

  // 标记障碍物
  Future<String> addObstacle(LatLng position, ObstacleType type, {String? title, String? description, String? existingId}) async {
    try {
      final obstacleId = existingId ?? DateTime.now().millisecondsSinceEpoch.toString();
      final obstacle = Obstacle(
        id: obstacleId,
        position: position,
        type: type,
        createdAt: DateTime.now(),
        title: title,
        description: description,
      );

      print("📤 正在上传标注: ${obstacle.toJson()}");
      await _submitObstacle(obstacle);
      await _saveMyObstacleId(obstacle.id);
      
      // Update local memory to ensure we can find it when uploading image right after
      _obstacles.add(obstacle);
      
      return obstacleId;
    } catch (e) {
      print("Error adding obstacle: $e");
       throw Exception("Error adding obstacle: $e");
    }
  }

  // 更新障碍物
  Future<void> updateObstacle(Obstacle original, {String? title, String? description}) async {
    try {
      final updated = Obstacle(
        id: original.id,
        position: original.position,
        type: original.type,
        createdAt: original.createdAt, // 保持创建时间
        title: title ?? original.title,
        description: description ?? original.description,
        imageUrls: original.imageUrls, // 保持图片
        status: original.status, // 保持状态
      );

      print("📤 正在更新标注: ${updated.toJson()}");
      await _submitObstacle(updated);
    } catch (e) {
      print("Error updating obstacle: $e");
      throw Exception("Error updating obstacle: $e");
    }
  }

  Future<void> _submitObstacle(Obstacle obstacle) async {
      // 乐观更新 (先显示在地图上)
      // _obstacles.add(obstacle);
      // _notifyListeners();
      
      String endpoint = '${AppConfig.apiUrl}/obstacles';
      if (obstacle.type == ObstacleType.shop) {
          endpoint = '${AppConfig.apiUrl}/shops';
      }

      final response = await http.post(
        Uri.parse(endpoint),
        headers: {'Content-Type': 'application/json; charset=utf-8'},
        body: jsonEncode(obstacle.toJson()),
      );

      if (response.statusCode == 200) {
        // 成功后再拉取最新列表 (或者手动添加)
        await fetchObstacles(silent: true);
      } else {
        print("Failed to submit obstacle: ${response.statusCode}");
        throw Exception("Failed to submit obstacle: ${response.statusCode}");
      }
  }

  // 上传图片并返回 URL
  Future<String?> uploadImage(String obstacleId, String filePath) async {
    try {
      // Determine endpoint based on known type in memory
      String endpoint = '${AppConfig.apiUrl}/obstacles/$obstacleId/image';
      
      // Find local object to check type
      try {
          final obj = _obstacles.firstWhere((o) => o.id == obstacleId);
          if (obj.type == ObstacleType.shop) {
              endpoint = '${AppConfig.apiUrl}/shops/$obstacleId/image';
          }
      } catch (_) {
          // If not found locally, try obstacles first (default)
      }

      final uri = Uri.parse(endpoint);
      final request = http.MultipartRequest('POST', uri);
      request.files.add(await http.MultipartFile.fromPath('image', filePath));
      
      final response = await request.send();
      if (response.statusCode == 200) {
        final respStr = await response.stream.bytesToString();
        final json = jsonDecode(respStr);
        await fetchObstacles(silent: true);
        return json['imageUrl'];
      } else {
        // If 404 and we guessed wrong, maybe try the other? 
        // But better to rely on local state.
        final respStr = await response.stream.bytesToString();
        print("⚠️ 图片上传失败: ${response.statusCode} - $respStr");
        return null;
      }
    } catch (e) {
      print("⚠️ 图片上传异常: $e");
      return null;
    }
  }

  // 添加评论 (支持图片)
  Future<bool> addComment(String obstacleId, String content, {double rating = 5.0, String? imageUrl}) async {
    try {
      // 1. 获取当前障碍物
      final obstacle = _obstacles.firstWhere((o) => o.id == obstacleId);
      
      // 2. 构建新评论
      final newComment = Comment(
        content: content, 
        createdAt: DateTime.now(),
        rating: rating,
        imageUrl: imageUrl,
      );

      // 3. 更新障碍物对象
      final updatedComments = List<Comment>.from(obstacle.comments)..add(newComment);
      
      final updatedObstacle = Obstacle(
        id: obstacle.id,
        position: obstacle.position,
        type: obstacle.type,
        createdAt: obstacle.createdAt,
        title: obstacle.title,
        description: obstacle.description,
        imageUrls: obstacle.imageUrls,
        status: obstacle.status,
        comments: updatedComments,
        clearCount: obstacle.clearCount,
        lastVerifiedAt: obstacle.lastVerifiedAt,
      );

      // 4. 提交整个对象
      await _submitObstacle(updatedObstacle);
      return true;
    } catch (e) {
      print("⚠️ 添加评论失败: $e");
      return false;
    }
  }

  // 删除障碍物
  Future<void> deleteObstacle(String id) async {
    // 1. 本地更新
    Obstacle? target;
    try {
        target = _obstacles.firstWhere((o) => o.id == id);
    } catch (_) {}
    
    _obstacles.removeWhere((o) => o.id == id);
    _notifyListeners();
    print("🗑️ 删除标注: $id");

    // 2. 同步到服务器
    try {
      String endpoint = '${AppConfig.apiUrl}/obstacles/$id';
      if (target != null && target.type == ObstacleType.shop) {
          endpoint = '${AppConfig.apiUrl}/shops/$id';
      }
      
      final response = await http.delete(Uri.parse(endpoint));
      if (response.statusCode != 200) {
        print("⚠️ 服务器删除失败: ${response.statusCode}");
      }
    } catch (e) {
      print("⚠️ 删除请求失败: $e");
    }
  }

  void _notifyListeners() {
    _obstacleStreamController.add(getObstacles());
  }
}
