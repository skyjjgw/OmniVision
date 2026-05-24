import 'package:flutter/material.dart';
import 'package:geolocator/geolocator.dart';
import 'package:latlong2/latlong.dart';
import 'package:intl/intl.dart';
import '../map/services/obstacle_service.dart';
import '../map/models/obstacle_model.dart';
import '../map/pages/volunteer_map_page.dart';

class MyAnnotationsPage extends StatefulWidget {
  const MyAnnotationsPage({super.key});

  @override
  State<MyAnnotationsPage> createState() => _MyAnnotationsPageState();
}

class _MyAnnotationsPageState extends State<MyAnnotationsPage> {
  List<Obstacle> _myObstacles = [];
  bool _isLoading = true;
  LatLng? _currentPosition;
  String _searchQuery = "";
  final TextEditingController _searchController = TextEditingController();

  @override
  void initState() {
    super.initState();
    _loadMyObstacles();
    _getCurrentLocation();
  }

  @override
  void dispose() {
    _searchController.dispose();
    super.dispose();
  }

  Future<void> _getCurrentLocation() async {
    try {
      final pos = await Geolocator.getCurrentPosition();
      if (mounted) {
        setState(() {
          _currentPosition = LatLng(pos.latitude, pos.longitude);
        });
      }
    } catch (e) {
      print("Error getting location: $e");
    }
  }

  Future<void> _loadMyObstacles() async {
    // 确保 IDs 已加载
    await Future.delayed(const Duration(milliseconds: 500));
    final obstacles = ObstacleService().getMyObstacles();
    if (mounted) {
      setState(() {
        _myObstacles = obstacles;
        _isLoading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final filteredObstacles = _myObstacles.where((obstacle) {
      final query = _searchQuery.toLowerCase();
      final title = (obstacle.title ?? "").toLowerCase();
      final desc = (obstacle.description ?? "").toLowerCase();
      return title.contains(query) || desc.contains(query);
    }).toList();

    return Scaffold(
      backgroundColor: Colors.grey[100],
      appBar: AppBar(
        title: const Text("我的标注"),
        elevation: 0,
        backgroundColor: Colors.white,
      ),
      body: Column(
        children: [
          Container(
            padding: const EdgeInsets.all(16),
            color: Colors.white,
            child: TextField(
              controller: _searchController,
              decoration: InputDecoration(
                hintText: "搜索标注名称...",
                prefixIcon: const Icon(Icons.search),
                suffixIcon: _searchQuery.isNotEmpty
                    ? IconButton(
                        icon: const Icon(Icons.clear),
                        onPressed: () {
                          setState(() {
                            _searchController.clear();
                            _searchQuery = "";
                          });
                        },
                      )
                    : null,
                filled: true,
                fillColor: Colors.grey[100],
                contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 0),
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(24),
                  borderSide: BorderSide.none,
                ),
                enabledBorder: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(24),
                  borderSide: BorderSide.none,
                ),
              ),
              onChanged: (value) {
                setState(() {
                  _searchQuery = value;
                });
              },
            ),
          ),
          Expanded(
            child: _isLoading
                ? const Center(child: CircularProgressIndicator())
                : filteredObstacles.isEmpty
                    ? Center(
                        child: Text(
                          _searchQuery.isEmpty ? "暂无标注" : "未找到匹配的标注",
                          style: const TextStyle(color: Colors.grey),
                        ),
                      )
                    : ListView.builder(
                        padding: const EdgeInsets.all(16),
                        itemCount: filteredObstacles.length,
                        itemBuilder: (context, index) {
                          final obstacle = filteredObstacles[index];
                          return Card(
                            margin: const EdgeInsets.only(bottom: 12),
                            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                            child: ListTile(
                              leading: Container(
                                padding: const EdgeInsets.all(8),
                                decoration: BoxDecoration(
                                  color: Colors.orange.withOpacity(0.1),
                                  shape: BoxShape.circle,
                                ),
                                child: Icon(
                                  obstacle.type == ObstacleType.shop ? Icons.store : Icons.warning,
                                  color: Colors.orange,
                                ),
                              ),
                              title: Text(obstacle.title ?? obstacle.description ?? "未知标注"),
                              subtitle: Text(
                                "${obstacle.description ?? ''}\n${DateFormat('yyyy-MM-dd HH:mm').format(obstacle.createdAt)}",
                                maxLines: 2,
                                overflow: TextOverflow.ellipsis,
                              ),
                              isThreeLine: true,
                              onTap: () {
                                Navigator.push(
                                  context,
                                  MaterialPageRoute(
                                    builder: (context) => VolunteerMapPage(
                                      selectedObstacle: obstacle,
                                    ),
                                  ),
                                ).then((_) {
                                  // 页面返回后，刷新一下列表（防止有编辑或删除）
                                  _loadMyObstacles();
                                });
                              },
                            ),
                          );
                        },
                      ),
          ),
        ],
      ),
    );
  }
}
