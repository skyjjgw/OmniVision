import 'dart:convert';
import 'dart:math' as Math;
import 'package:flutter/material.dart';
import 'package:webview_flutter/webview_flutter.dart';
import 'package:geolocator/geolocator.dart';
import 'package:latlong2/latlong.dart';
import 'package:image_picker/image_picker.dart';
import 'package:flutter_compass/flutter_compass.dart';
import '../../services/theme_service.dart';
import '../models/obstacle_model.dart';
import '../services/obstacle_service.dart';

import 'dart:io';
import 'package:image_picker/image_picker.dart';
import '../widgets/obstacle_detail_sheet.dart';
import '../../config/app_config.dart';

class VolunteerMapPage extends StatefulWidget {
  final Obstacle? selectedObstacle;
  final bool isSelectionMode;

  const VolunteerMapPage({
    super.key, 
    this.selectedObstacle,
    this.isSelectionMode = false,
  });

  @override
  State<VolunteerMapPage> createState() => _VolunteerMapPageState();
}

class _VolunteerMapPageState extends State<VolunteerMapPage> {
  late final WebViewController _webViewController;
  final ObstacleService _obstacleService = ObstacleService();
  bool _isLoading = true;
  bool _isWebPageReady = false; // 标记网页是否加载完成
  LatLng? _currentPosition;
  double _currentHeading = 0.0; // 当前设备朝向
  List<Obstacle> _obstacles = [];
  final ThemeService _themeService = ThemeService();
  
  // 搜索相关
  final TextEditingController _searchController = TextEditingController();
  String _searchQuery = "";
  List<Obstacle> _searchResults = [];
  List<Map<String, dynamic>> _poiResults = []; // Amap POI results

  @override
  void initState() {
    super.initState();
    // Initialize with existing obstacles
    _obstacles = _obstacleService.getObstacles();
    
    _initWebView();
    _initLocation();
    _setupListeners();
    _themeService.addListener(_onThemeChanged);
  }

  @override
  void dispose() {
    _searchController.dispose();
    _themeService.removeListener(_onThemeChanged);
    super.dispose();
  }

  void _onThemeChanged() {
    if (mounted) setState(() {});
  }

  void _initWebView() {
    _webViewController = WebViewController()
      ..setJavaScriptMode(JavaScriptMode.unrestricted)
      ..setBackgroundColor(const Color(0x00000000))
      ..addJavaScriptChannel(
        'Flutter',
        onMessageReceived: (JavaScriptMessage message) {
          _handleJsMessage(message.message);
        },
      )
      ..setNavigationDelegate(
        NavigationDelegate(
          onPageFinished: (String url) {
            print("WebPage Loaded: $url");
            if (mounted) {
              setState(() {
                _isLoading = false;
                _isWebPageReady = true;
              });
              // 页面加载完后，如果已有位置，立即同步
              if (_currentPosition != null) {
                _updateWebPosition(_currentPosition!);
              }
              
              // 强制多次同步障碍物，防止地图初始化延迟导致第一次同步失败
              _syncObstaclesToWeb();
              Future.delayed(const Duration(seconds: 1), _syncObstaclesToWeb);
              Future.delayed(const Duration(seconds: 3), _syncObstaclesToWeb);

              // 如果有传入的选中障碍物，跳转并显示
              if (widget.selectedObstacle != null) {
                // 稍微延迟一点，确保地图完全初始化
                Future.delayed(const Duration(milliseconds: 500), () {
                  if (mounted) {
                    _onSearchResultTap(widget.selectedObstacle!);
                  }
                });
              }
            }
          },
          onWebResourceError: (error) {
            print("WebResourceError: ${error.description}");
          },
        ),
      )
      ..loadFlutterAsset('assets/html/amap.html');
  }

  void _handleJsMessage(String jsonStr) {
    try {
      final data = jsonDecode(jsonStr);
      if (data['type'] == 'MARKER_CLICK') {
        final id = data['id'];
        final obstacle = _obstacles.firstWhere(
          (o) => o.id == id,
          orElse: () => Obstacle(id: id, position: const LatLng(0, 0), createdAt: DateTime.now())
        );

        // 如果是选择模式，直接返回结果
        if (widget.isSelectionMode) {
          Navigator.pop(context, obstacle);
          return;
        }

        // Clear search when clicking a marker
        setState(() {
          _searchController.clear();
          _searchQuery = "";
          _searchResults = [];
          _poiResults = [];
          FocusScope.of(context).unfocus();
        });

        // 无论是店铺还是障碍物，统一使用新的详情页
        
        // 增加: 点击标注时也跳转并放大
        _webViewController.runJavaScript(
          "map.setZoomAndCenter(20, [${obstacle.position.longitude}, ${obstacle.position.latitude}]);"
        );
        
        _showShopDetailsSheet(obstacle);
      } else if (data['type'] == 'POI_CLICK') {
        final name = data['name'];
        final id = data['id'];
        final lat = data['lat'];
        final lon = data['lon'];
        // 直接使用新建标注对话框，并填入信息
        _showAddObstacleDialog(LatLng(lat, lon), initialTitle: name);
      } else if (data['type'] == 'POI_SEARCH_RESULT') {
        final List<dynamic> pois = data['data'];
        setState(() {
          _poiResults = pois.cast<Map<String, dynamic>>();
        });
      }
    } catch (e) {
      print("JS Message Error: $e");
    }
  }

  // 显示 POI 对话框
  void _showPoiDialog(String name, String? id, LatLng position) {
    showDialog(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(name),
        content: const Text("您想将此地点标记为障碍物/场景并添加图片吗？"),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: const Text("取消")),
          TextButton(
            onPressed: () async {
              Navigator.pop(context);
              // 创建一个新的障碍物/场景标记
              // 这里的类型可以根据需求调整，暂时设为 other
              await _obstacleService.addObstacle(position, ObstacleType.other);
              
              // 获取刚创建的（或最近的）ID，这里假设 addObstacle 是同步内存添加的
              // 实际上 ObstacleService.addObstacle 是异步的，我们最好让它返回 ID
              // 暂时我们通过查找最近位置的 Obstacle 来获取 ID，或者修改 addObstacle 返回 ID
              // 为了简单，我们让用户先标记，然后再点进去加图片。
              // 或者：
              
              ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("标记已添加，请点击标记添加图片")));
            },
            child: const Text("标记并添加图片"),
          ),
        ],
      ),
    );
  }

  void _showAddObstacleDialog(LatLng point, {String? initialTitle, String? initialDescription}) {
    final TextEditingController _titleController = TextEditingController(text: initialTitle);
    final TextEditingController _descController = TextEditingController(text: initialDescription);
    
    // 默认选择 shop
    ObstacleType _selectedType = ObstacleType.shop;
    XFile? _selectedImage;
    bool _isUploading = false;

    showDialog(
      context: context,
      builder: (context) => StatefulBuilder(
        builder: (context, setState) {
          return AlertDialog(
            title: const Text("新建标注"),
            content: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text("选择类型:", style: TextStyle(fontWeight: FontWeight.bold)),
                  const SizedBox(height: 10),
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceEvenly,
                    children: [
                      _buildTypeButton(
                        onTap: () => setState(() => _selectedType = ObstacleType.shop),
                        label: "商家店铺", 
                        icon: Icons.store, 
                        color: Colors.blue,
                        isSelected: _selectedType == ObstacleType.shop
                      ),
                      _buildTypeButton(
                        onTap: () => setState(() => _selectedType = ObstacleType.construction),
                        label: "障碍/施工", 
                        icon: Icons.warning_amber_rounded, 
                        color: Colors.red,
                        isSelected: _selectedType == ObstacleType.construction
                      ),
                    ],
                  ),
                  const SizedBox(height: 20),
                  if (_selectedType == ObstacleType.shop) ...[
                    TextField(
                      controller: _titleController,
                      decoration: const InputDecoration(
                        labelText: "店铺名称",
                        border: OutlineInputBorder(),
                        hintText: "例如：幸福超市",
                      ),
                    ),
                    const SizedBox(height: 10),
                    TextField(
                      controller: _descController,
                      decoration: const InputDecoration(
                        labelText: "货物分区/详情",
                        border: OutlineInputBorder(),
                        hintText: "例如：进门左手边是饮料区，右手边是零食区...",
                      ),
                      maxLines: 4,
                    ),
                  ] else ...[
                    TextField(
                      controller: _descController,
                      decoration: const InputDecoration(
                        labelText: "障碍描述",
                        border: OutlineInputBorder(),
                        hintText: "例如：修路，请绕行",
                      ),
                      maxLines: 3,
                    ),
                  ],
                  const SizedBox(height: 10),
                  Text("位置: ${point.latitude.toStringAsFixed(5)}, ${point.longitude.toStringAsFixed(5)}", 
                       style: const TextStyle(fontSize: 12, color: Colors.grey)),
                  const SizedBox(height: 20),
                  const Text("现场照片 (可选):", style: TextStyle(fontWeight: FontWeight.bold)),
                  const SizedBox(height: 10),
                  if (_selectedImage != null)
                    Stack(
                      children: [
                        Container(
                          height: 150,
                          width: double.infinity,
                          decoration: BoxDecoration(
                            borderRadius: BorderRadius.circular(12),
                            border: Border.all(color: Colors.grey.shade300),
                          ),
                          child: ClipRRect(
                            borderRadius: BorderRadius.circular(12),
                            child: Image.file(File(_selectedImage!.path), fit: BoxFit.cover),
                          ),
                        ),
                        Positioned(
                          right: 8,
                          top: 8,
                          child: GestureDetector(
                            onTap: () => setState(() => _selectedImage = null),
                            child: Container(
                              padding: const EdgeInsets.all(4),
                              decoration: const BoxDecoration(
                                color: Colors.black54,
                                shape: BoxShape.circle,
                              ),
                              child: const Icon(Icons.close, color: Colors.white, size: 20),
                            ),
                          ),
                        ),
                      ],
                    )
                  else
                    Row(
                      children: [
                        Expanded(
                          child: GestureDetector(
                            onTap: () async {
                              final ImagePicker picker = ImagePicker();
                              final XFile? image = await picker.pickImage(source: ImageSource.camera, imageQuality: 80);
                              if (image != null) {
                                setState(() => _selectedImage = image);
                              }
                            },
                            child: Container(
                              height: 100,
                              decoration: BoxDecoration(
                                color: Colors.grey.shade100,
                                border: Border.all(color: Colors.grey.shade300, style: BorderStyle.solid),
                                borderRadius: BorderRadius.circular(12),
                              ),
                              child: Column(
                                mainAxisAlignment: MainAxisAlignment.center,
                                children: [
                                  Icon(Icons.camera_alt, color: Colors.grey.shade400, size: 30),
                                  const SizedBox(height: 8),
                                  Text("点击拍照", style: TextStyle(color: Colors.grey.shade600, fontWeight: FontWeight.bold)),
                                ],
                              ),
                            ),
                          ),
                        ),
                        const SizedBox(width: 12),
                        Expanded(
                          child: GestureDetector(
                            onTap: () async {
                              final ImagePicker picker = ImagePicker();
                              final XFile? image = await picker.pickImage(source: ImageSource.gallery, imageQuality: 80);
                              if (image != null) {
                                setState(() => _selectedImage = image);
                              }
                            },
                            child: Container(
                              height: 100,
                              decoration: BoxDecoration(
                                color: Colors.grey.shade100,
                                border: Border.all(color: Colors.grey.shade300, style: BorderStyle.solid),
                                borderRadius: BorderRadius.circular(12),
                              ),
                              child: Column(
                                mainAxisAlignment: MainAxisAlignment.center,
                                children: [
                                  Icon(Icons.photo_library, color: Colors.grey.shade400, size: 30),
                                  const SizedBox(height: 8),
                                  Text("相册选择", style: TextStyle(color: Colors.grey.shade600, fontWeight: FontWeight.bold)),
                                ],
                              ),
                            ),
                          ),
                        ),
                      ],
                    ),
                ],
              ),
            ),
            actions: [
              TextButton(onPressed: () => Navigator.pop(context), child: const Text("取消")),
              ElevatedButton(
                onPressed: _isUploading ? null : () async {
                  setState(() => _isUploading = true);
                  final newObstacleId = await _addObstacle(
                    point, 
                    _selectedType, 
                    title: _titleController.text, 
                    description: _descController.text
                  );
                  
                  if (newObstacleId != null && _selectedImage != null) {
                    try {
                      await _obstacleService.uploadImage(newObstacleId, _selectedImage!.path);
                      if (mounted) ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("图片上传成功！")));
                    } catch (e) {
                      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("图片上传失败: $e")));
                    }
                  }
                  
                  setState(() => _isUploading = false);
                  if (mounted) Navigator.pop(context);
                },
                child: _isUploading 
                  ? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                  : const Text("确定并上传"),
              ),
            ],
          );
        }
      ),
    );
  }

  Widget _buildTypeButton({
    required VoidCallback onTap, 
    required String label, 
    required IconData icon, 
    required Color color,
    required bool isSelected,
  }) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
        decoration: BoxDecoration(
          color: isSelected ? color.withOpacity(0.1) : Colors.transparent,
          border: Border.all(color: isSelected ? color : Colors.grey.shade300, width: 2),
          borderRadius: BorderRadius.circular(8),
        ),
        child: Column(
          children: [
            Icon(icon, color: isSelected ? color : Colors.grey, size: 30),
            const SizedBox(height: 4),
            Text(label, style: TextStyle(
              color: isSelected ? color : Colors.grey,
              fontWeight: isSelected ? FontWeight.bold : FontWeight.normal
            )),
          ],
        ),
      ),
    );
  }

  Future<String?> _addObstacle(LatLng point, ObstacleType type, {String? title, String? description}) async {
    try {
      final newId = await _obstacleService.addObstacle(point, type, title: title, description: description);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("标注成功")));
      }
      return newId;
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("标记失败: $e")));
      }
      return null;
    }
  }

  Future<void> _initLocation() async {
    bool serviceEnabled;
    LocationPermission permission;

    try {
      serviceEnabled = await Geolocator.isLocationServiceEnabled();
      if (!serviceEnabled) {
        if (mounted) ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('请开启定位服务')));
        return;
      }

      permission = await Geolocator.checkPermission();
      if (permission == LocationPermission.denied) {
        permission = await Geolocator.requestPermission();
        if (permission == LocationPermission.denied) {
          if (mounted) ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('定位权限被拒绝')));
          return;
        }
      }
      
      if (permission == LocationPermission.deniedForever) {
         if (mounted) ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('定位权限被永久拒绝，请在设置中开启')));
         return;
      }

      // 罗盘/设备朝向监听
      FlutterCompass.events?.listen((CompassEvent event) {
        if (event.heading != null && mounted && _currentPosition != null) {
          setState(() {
            _currentHeading = event.heading!;
          });
          _updateWebPosition(_currentPosition!);
        }
      });

      // 实时定位监听
      Geolocator.getPositionStream(
        locationSettings: const LocationSettings(
          accuracy: LocationAccuracy.high, // 高德地图需要较高精度配合
          distanceFilter: 5,
        )
      ).listen((pos) {
         final newPos = LatLng(pos.latitude, pos.longitude);
         if (mounted) {
           setState(() {
             _currentPosition = newPos;
           });
           _updateWebPosition(newPos);
         }
      });

    } catch (e) {
      print("定位出错: $e");
    }
  }

  void _updateWebPosition(LatLng pos, {bool forceCenter = false, double? angle}) {
    if (!_isWebPageReady) return;
    
    final rot = angle ?? _currentHeading;
    // 发送原始 WGS84 坐标给网页，由高德地图自带的 AMap.convertFrom 统一纠偏
    final script = "window.handleFlutterMessage(JSON.stringify({type: 'UPDATE_POSITION', lat: ${pos.latitude}, lon: ${pos.longitude}, forceCenter: $forceCenter, angle: $rot}));";
    _webViewController.runJavaScript(script);
  }

  // 商家详情底部弹窗
  void _showShopDetailsSheet(Obstacle obstacle) {
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (context) {
        return ObstacleDetailSheet(
          obstacle: obstacle,
          currentPosition: _currentPosition,
        );
      },
    ).then((_) {
      // 详情页关闭后，刷新一下地图上的数据
      _syncObstaclesToWeb();
    });
  }

  void _setupListeners() {
    _obstacleService.obstacleStream.listen((obstacles) {
      if (mounted) {
        setState(() {
          _obstacles = obstacles;
        });
        _syncObstaclesToWeb();
      }
    });
  }
  
  void _syncObstaclesToWeb() {
    if (!_isWebPageReady) return;
    
    // 转换为 JS 需要的数据格式
    final data = _obstacles.map((o) {
      String typeStr = 'active'; // 默认
      // 如果是商家
      if (o.type == ObstacleType.shop) {
          typeStr = 'shop';
      } else if (o.disputeStatus != null && o.disputeStatus!.isNotEmpty) {
          // 争议状态 (Optimization 2)
          typeStr = 'dispute';
      } else if (o.status == VerificationStatus.candidateForRemoval) {
          typeStr = 'candidate';
      } else {
          // 障碍物/施工/其他
          typeStr = 'obstacle';
      }

      return {
        'id': o.id,
        'lat': o.position.latitude,
        'lon': o.position.longitude,
        'status': typeStr, 
        'description': o.description,
        'imageUrl': o.imageUrls.isNotEmpty ? o.imageUrls[0].replaceAll('10.0.2.2', AppConfig.serverIp).replaceAll('47.97.184.133', AppConfig.serverIp) : null,
        'imageCount': o.imageUrls.length,
      };
    }).toList();

    print("Syncing ${data.length} obstacles to Web"); // Debug Log

    // 使用 jsonEncode 对整个对象进行序列化，确保字符串转义正确
    final jsonStr = jsonEncode({
      'type': 'SET_OBSTACLES',
      'data': data,
    });
    
    // 使用 jsonEncode 再次序列化字符串，以便安全地嵌入到 JS 字符串字面量中
    final safeJsonStr = jsonEncode(jsonStr);
    
    _webViewController.runJavaScript("if(window.handleFlutterMessage) { window.handleFlutterMessage($safeJsonStr); } else { console.log('handleFlutterMessage not ready'); }");
  }

  void _onSearchChanged(String query) {
    setState(() {
      _searchQuery = query;
      if (query.isEmpty) {
        _searchResults = [];
        _poiResults = [];
      } else {
        // 1. Local Search
        _searchResults = _obstacles.where((o) {
          final title = (o.title ?? "").toLowerCase();
          final desc = (o.description ?? "").toLowerCase();
          final q = query.toLowerCase();
          return title.contains(q) || desc.contains(q);
        }).toList();

        // 2. Remote POI Search
        if (_isWebPageReady && !widget.isSelectionMode) { // 只在非选择模式下搜索 POI
          // Add quotes around query properly
          final script = "window.handleFlutterMessage(JSON.stringify({type: 'SEARCH_POI', keyword: \"$query\"}));";
          _webViewController.runJavaScript(script);
        }
      }
    });
  }

  void _onPoiResultTap(Map<String, dynamic> poi) {
    // 1. Clear search
    setState(() {
      _searchController.clear();
      _searchQuery = "";
      _searchResults = [];
      _poiResults = [];
      FocusScope.of(context).unfocus();
    });

    // 2. Move map
    final lat = poi['lat'];
    final lon = poi['lon'];
    _webViewController.runJavaScript(
      "map.setZoomAndCenter(18, [$lon, $lat]);"
    );

    // 3. Ask to select if in selection mode
    if (widget.isSelectionMode) {
      showDialog(
        context: context,
        builder: (ctx) => AlertDialog(
          title: Text("选择此地点?"),
          content: Text(poi['name'] ?? ""),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(ctx), 
              child: const Text("取消")
            ),
            TextButton(
              onPressed: () {
                Navigator.pop(ctx);
                // Create a temporary obstacle object for selection
                final obs = Obstacle(
                  id: poi['id'] ?? "poi_${DateTime.now().millisecondsSinceEpoch}",
                  position: LatLng(lat, lon),
                  title: poi['name'],
                  description: poi['address'],
                  type: ObstacleType.other,
                  createdAt: DateTime.now(),
                );
                Navigator.pop(context, obs);
              }, 
              child: const Text("确定")
            ),
          ],
        )
      );
    } else {
        // Show Add Dialog with pre-filled info
        _showAddObstacleDialog(LatLng(lat, lon), initialTitle: poi['name'], initialDescription: poi['address']);
      }
    }

  void _onSearchResultTap(Obstacle obstacle) {
    // 如果是选择模式，直接返回结果
    if (widget.isSelectionMode) {
      Navigator.pop(context, obstacle);
      return;
    }

    // 1. 清空搜索
    setState(() {
      _searchController.clear();
      _searchQuery = "";
      _searchResults = [];
      _poiResults = [];
      // 收起键盘
      FocusScope.of(context).unfocus();
    });

    // 2. 移动地图中心 (调用 JS)
    // 使用 map.setZoomAndCenter(zoom, center)
    // 障碍物坐标已经是 GCJ02 (存储时就是地图中心点)，所以不需要再转换
    _webViewController.runJavaScript(
      "map.setZoomAndCenter(20, [${obstacle.position.longitude}, ${obstacle.position.latitude}]);"
    );

    // 3. 显示详情
    _showShopDetailsSheet(obstacle);
  }

  @override
  Widget build(BuildContext context) {
    final currentTheme = _themeService.currentTheme;

    return Scaffold(
      resizeToAvoidBottomInset: false, // 防止键盘顶起整个页面布局
      body: Stack(
        children: [
          WebViewWidget(controller: _webViewController),
          if (_isLoading)
             const Center(child: CircularProgressIndicator()),
          
          // 顶部自定义头部与搜索栏
          Positioned(
            top: 0,
            left: 0,
            right: 0,
            child: Container(
              padding: const EdgeInsets.only(bottom: 16),
              decoration: BoxDecoration(
                gradient: LinearGradient(
                  begin: Alignment.topLeft,
                  end: Alignment.bottomRight,
                  colors: currentTheme.gradientColors,
                ),
                boxShadow: [
                  BoxShadow(
                    color: Colors.black.withOpacity(0.2),
                    blurRadius: 10,
                    offset: const Offset(0, 4),
                  )
                ],
                borderRadius: const BorderRadius.only(
                  bottomLeft: Radius.circular(24),
                  bottomRight: Radius.circular(24),
                ),
              ),
              child: SafeArea(
                bottom: false,
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    // 标题栏
                    Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                      child: Row(
                        children: [
                          Expanded(
                            child: Text(
                              widget.isSelectionMode ? "请选择一个地点" : "志愿者地图标注",
                              textAlign: TextAlign.center,
                              style: TextStyle(
                                color: currentTheme.textColor,
                                fontSize: 22,
                                fontWeight: FontWeight.bold,
                                letterSpacing: 1.2,
                                shadows: [
                                  Shadow(
                                    offset: Offset(0, 2),
                                    blurRadius: 4.0,
                                    color: Color.fromARGB(64, 0, 0, 0),
                                  ),
                                ],
                              ),
                            ),
                          ),
                        ],
                      ),
                    ),
                    
                    // 搜索栏容器
                    Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 16),
                      child: Column(
                        children: [
                          Container(
                            decoration: BoxDecoration(
                              color: Colors.white,
                              borderRadius: BorderRadius.circular(16),
                              boxShadow: [
                                BoxShadow(
                                  color: Colors.black.withOpacity(0.05),
                                  blurRadius: 8,
                                  offset: const Offset(0, 2),
                                )
                              ],
                            ),
                            child: TextField(
                              controller: _searchController,
                              decoration: InputDecoration(
                                hintText: "搜索地图上的标注...",
                                hintStyle: TextStyle(color: Colors.grey.shade400, fontSize: 14),
                                prefixIcon: Icon(Icons.search, color: currentTheme.gradientColors.first.withOpacity(0.7)),
                                suffixIcon: _searchQuery.isNotEmpty
                                    ? IconButton(
                                        icon: const Icon(Icons.clear, color: Colors.grey),
                                        onPressed: () {
                                          setState(() {
                                            _searchController.clear();
                                            _onSearchChanged("");
                                            FocusScope.of(context).unfocus();
                                          });
                                        },
                                      )
                                    : null,
                                border: InputBorder.none,
                                contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                              ),
                              onChanged: _onSearchChanged,
                            ),
                          ),
                          
                          // 搜索结果列表
                          if (_searchResults.isNotEmpty || _poiResults.isNotEmpty)
                            Container(
                              margin: const EdgeInsets.only(top: 8),
                              constraints: const BoxConstraints(maxHeight: 400),
                              decoration: BoxDecoration(
                                color: Colors.white,
                                borderRadius: BorderRadius.circular(12),
                                boxShadow: [
                                  BoxShadow(
                                    color: Colors.black.withOpacity(0.1),
                                    blurRadius: 8,
                                    offset: const Offset(0, 2),
                                  )
                                ],
                              ),
                              child: ListView(
                              shrinkWrap: true,
                              padding: EdgeInsets.zero,
                              children: [
                                // Local Results
                                if (_searchResults.isNotEmpty) ...[
                                  const Padding(
                                    padding: EdgeInsets.all(8.0),
                                    child: Text("  已有标注", style: TextStyle(fontWeight: FontWeight.bold, color: Colors.grey)),
                                  ),
                                  ..._searchResults.map((item) => ListTile(
                                    leading: Icon(
                                      item.type == ObstacleType.shop ? Icons.store : Icons.warning,
                                      color: Colors.orange,
                                    ),
                                    title: Text(item.title ?? item.description ?? "未知地点"),
                                    subtitle: Text(
                                      item.description ?? "", 
                                      maxLines: 1, 
                                      overflow: TextOverflow.ellipsis
                                    ),
                                    onTap: () => _onSearchResultTap(item),
                                  )),
                                  if (_poiResults.isNotEmpty) const Divider(),
                                ],

                                // POI Results
                                if (_poiResults.isNotEmpty) ...[
                                  const Padding(
                                    padding: EdgeInsets.all(8.0),
                                    child: Text("  新地点 (高德搜索)", style: TextStyle(fontWeight: FontWeight.bold, color: Colors.grey)),
                                  ),
                                  ..._poiResults.map((item) => ListTile(
                                    leading: const Icon(Icons.place, color: Colors.blue),
                                    title: Text(item['name'] ?? ""),
                                    subtitle: Text(item['address'] ?? ""),
                                    onTap: () => _onPoiResultTap(item),
                                  )),
                                ],
                              ],
                            ),
                            ),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),

          // 屏幕中心准星
          const Center(
            child: Icon(Icons.add, size: 30, color: Colors.red),
          ),
        ],
      ),
      floatingActionButton: Padding(
        padding: const EdgeInsets.only(bottom: 95.0),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            if (!widget.isSelectionMode)
              FloatingActionButton(
                heroTag: "add_marker",
                backgroundColor: currentTheme.gradientColors.first,
                child: Icon(Icons.add_location_alt, color: currentTheme.textColor),
                onPressed: () async {
                  // 获取地图中心点进行标注
                  try {
                    final result = await _webViewController.runJavaScriptReturningResult("window.getMapCenter()");
                    // 解析返回的 JSON 字符串
                    String jsonString = result.toString();
                    // 处理可能的双重引号
                    if (jsonString.startsWith('"') && jsonString.endsWith('"')) {
                      jsonString = jsonDecode(jsonString); 
                    }
                    
                    final centerData = jsonDecode(jsonString);
                    final lat = centerData['lat'];
                    final lon = centerData['lon'];
                    
                    // 直接使用地图中心的坐标 (已经是 GCJ02)
                    _showAddObstacleDialog(LatLng(lat, lon));
                    
                  } catch (e) {
                    print("获取地图中心失败: $e");
                    ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("获取地图位置失败，请重试")));
                  }
                },
              ),
            const SizedBox(height: 16),
            FloatingActionButton(
              heroTag: "reposition",
              backgroundColor: Colors.white,
              child: Icon(Icons.my_location, color: currentTheme.gradientColors.first),
              onPressed: () async {
                // 策略优化：
                // 1. 先尝试获取“上次已知位置”（极快，无延迟）
                // 2. 如果有，立即跳转
                // 3. 然后后台发起高精度定位刷新（允许较长超时）
                
                try {
                  // 1. 获取缓存位置
                  final lastPos = await Geolocator.getLastKnownPosition();
                  if (lastPos != null) {
                    if (mounted) {
                      _currentPosition = LatLng(lastPos.latitude, lastPos.longitude);
                      _updateWebPosition(_currentPosition!, forceCenter: true);
                    }
                  } else {
                    if (mounted) ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("正在定位..."), duration: Duration(seconds: 1)));
                  }

                  // 2. 发起高精度定位 (10秒超时)
                  final pos = await Geolocator.getCurrentPosition(
                    timeLimit: const Duration(seconds: 10) 
                  );
                  
                  if (mounted) {
                    setState(() {
                      _currentPosition = LatLng(pos.latitude, pos.longitude);
                    });
                    // 二次校准
                    _updateWebPosition(_currentPosition!, forceCenter: true);
                  }
                } catch (e) {
                  print("定位异常: $e");
                  // 只有当完全没有任何位置信息时才报错
                  if (_currentPosition == null && mounted) {
                     ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("定位慢，请检查GPS信号")));
                  }
                }
              },
            ),
          ],
        ),
      ),
    );
  }
}
