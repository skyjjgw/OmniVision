import 'dart:io';
import 'dart:typed_data';
import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:latlong2/latlong.dart';
import 'package:path_provider/path_provider.dart';
import '../models/contribution_model.dart';
import '../services/contribution_service.dart';
import '../config/app_config.dart';
import '../map/services/obstacle_service.dart';
import '../map/models/obstacle_model.dart';
import '../map/pages/volunteer_map_page.dart';

class CreateContributionPage extends StatefulWidget {
  final String username;
  final String? preSelectedMarkerId;
  final bool isObstacleMode;
  final String? initialRegionCode;
  final String? initialRegionName;
  final String? initialForumKey;

  const CreateContributionPage({
    super.key,
    required this.username,
    this.preSelectedMarkerId,
    this.isObstacleMode = false,
    this.initialRegionCode,
    this.initialRegionName,
    this.initialForumKey,
  });

  @override
  State<CreateContributionPage> createState() => _CreateContributionPageState();
}

class _CreateContributionPageState extends State<CreateContributionPage> {
  final ContributionService _contributionService = ContributionService();
  final ObstacleService _obstacleService = ObstacleService();
  
  Obstacle? _selectedObstacle;
  late ContributionType _selectedType;
  
  // Layout Form
  File? _layoutImage;
  
  // Status Form
  File? _statusImage;
  ObstacleStatus _proposedStatus = ObstacleStatus.active;
  
  final TextEditingController _contentController = TextEditingController();
  bool _isSubmitting = false;
  // The region options are no longer hardcoded.
  // Instead, the region is automatically extracted and populated from the selected location.
  String _selectedRegionCode = '';
  String _selectedRegionName = '';

  String _selectedForumKey = 'share';
  final List<Map<String, String>> _forumOptions = [
    {'key': 'share', 'name': '探店分享'},
    {'key': 'help', 'name': '互助问答'},
    {'key': 'feedback', 'name': '功能反馈'},
  ];

  @override
  void initState() {
    super.initState();
    _selectedType = widget.isObstacleMode ? ContributionType.obstacleStatus : ContributionType.storeLayout;
    _selectedRegionCode = widget.initialRegionCode ?? '';
    _selectedRegionName = widget.initialRegionName ?? '';
    _selectedForumKey = widget.initialForumKey ?? 'share';
    _fetchUserProfile();
    if (widget.preSelectedMarkerId != null) {
      final obstacles = _obstacleService.getObstacles();
      try {
        _selectedObstacle = obstacles.firstWhere((o) => o.id == widget.preSelectedMarkerId);
      } catch (e) {
        print("Pre-selected obstacle not found locally: $e");
      }
    }
  }

  String? _nickname;
  String? _avatarPath;

  Future<void> _fetchUserProfile() async {
    try {
      final response = await http.get(Uri.parse('${AppConfig.socketUrl}/api/user/profile?email=${widget.username}'));
      if (response.statusCode == 200) {
        final data = jsonDecode(response.body);
        if (data['success']) {
          if (mounted) {
            setState(() {
              _nickname = data['profile']['nickname'];
              _avatarPath = data['profile']['avatar_path'];
              if (widget.preSelectedMarkerId != null && _selectedObstacle != null && _selectedRegionCode.isEmpty) {
                _selectedRegionCode = '310000'; // fallback mock
                _selectedRegionName = '自动识别中...';
                _reverseGeocode(_selectedObstacle!.position);
              }
            });
          }
        }
      }
    } catch (e) {
      print("Fetch profile error: $e");
    }
  }

  Future<void> _submit() async {
    if (_selectedObstacle == null) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("请先选择一个地点")));
      return;
    }
    if (_contentController.text.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("请输入描述")));
      return;
    }

    setState(() => _isSubmitting = true);

    String? imageUrl;
    
    // Upload Image
    if (_selectedType == ContributionType.storeLayout && _layoutImage != null) {
      imageUrl = await _contributionService.uploadImage(_layoutImage!);
    } else if ((_selectedType == ContributionType.obstacleStatus || _selectedType == ContributionType.newObstacle) && _statusImage != null) {
      imageUrl = await _contributionService.uploadImage(_statusImage!);
    }

    final contribution = ContributionModel(
      id: '', // Server will generate
      markerId: _selectedObstacle!.id,
      markerTitle: _selectedObstacle!.title ?? '未命名地点',
      type: _selectedType,
      userId: widget.username, // Using username as ID for now
      userNickname: _nickname ?? widget.username,
      userAvatar: _avatarPath,
      content: _contentController.text,
      imageUrl: imageUrl,
      zones: const [],
      proposedStatus: _selectedType == ContributionType.obstacleStatus ? _proposedStatus : null,
      lat: _selectedObstacle!.position.latitude,
      lng: _selectedObstacle!.position.longitude,
      createdAt: DateTime.now(),
      regionCode: _selectedRegionCode,
      regionName: _selectedRegionName.isNotEmpty ? _selectedRegionName : (widget.isObstacleMode ? '障碍物争议区' : '店铺社区'),
      forumKey: _selectedForumKey,
      forumName: _forumOptions.firstWhere(
        (o) => o['key'] == _selectedForumKey,
        orElse: () => {
          'name': widget.isObstacleMode ? '障碍论坛' : '探店分享',
        },
      )['name'],
      targetType: _selectedType == ContributionType.storeLayout ? 'shop' : 'obstacle',
    );

    final success = await _contributionService.addContribution(contribution);
    
    setState(() => _isSubmitting = false);
    
    if (success) {
      if (mounted) {
        Navigator.pop(context, true);
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("发布成功，等待验证")));
      }
    } else {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("发布失败")));
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text(widget.isObstacleMode ? "发布障碍物反馈" : "发布店铺社区话题")),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // 0. Region & Forum Selection
            Row(
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Text("当前地区", style: TextStyle(fontWeight: FontWeight.bold)),
                      const SizedBox(height: 8),
                      Container(
                        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 12),
                        decoration: BoxDecoration(
                          color: Colors.grey[200],
                          border: Border.all(color: Colors.grey.shade400),
                          borderRadius: BorderRadius.circular(4),
                        ),
                        child: Row(
                          children: [
                            const Icon(Icons.map, size: 20, color: Colors.grey),
                            const SizedBox(width: 8),
                            Text(
                              _selectedRegionName.isNotEmpty ? _selectedRegionName : "选择地点后自动识别",
                              style: TextStyle(color: _selectedRegionName.isNotEmpty ? Colors.black : Colors.grey.shade600),
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                ),
                if (!widget.isObstacleMode) ...[
                  const SizedBox(width: 16),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const Text("选择板块", style: TextStyle(fontWeight: FontWeight.bold)),
                        const SizedBox(height: 8),
                        DropdownButtonFormField<String>(
                          value: _selectedForumKey,
                          decoration: const InputDecoration(border: OutlineInputBorder(), contentPadding: EdgeInsets.symmetric(horizontal: 10)),
                          items: _forumOptions.map((opt) => DropdownMenuItem(value: opt['key'], child: Text(opt['name']!))).toList(),
                          onChanged: (val) => setState(() => _selectedForumKey = val!),
                        ),
                      ],
                    ),
                  ),
                ],
              ],
            ),
            const SizedBox(height: 20),

            // 1. Select Marker
            _buildMarkerSelector(),
            const SizedBox(height: 20),
            
            // 2. Select Type
            _buildTypeSelector(),
            const Divider(height: 30),
            
            // 3. Dynamic Form
            _buildDynamicForm(),
              
            const SizedBox(height: 20),
            TextField(
              controller: _contentController,
              decoration: const InputDecoration(
                labelText: "补充描述",
                border: OutlineInputBorder(),
                hintText: "请详细描述您的标注内容...",
              ),
              maxLines: 3,
            ),
            
            const SizedBox(height: 30),
            SizedBox(
              width: double.infinity,
              height: 50,
              child: ElevatedButton(
                onPressed: _isSubmitting ? null : _submit,
                child: _isSubmitting 
                    ? const CircularProgressIndicator(color: Colors.white)
                    : const Text("提交审核 (10天公示期)"),
              ),
            ),
          ],
        ),
      ),
    );
  }

  // Fetch Region Name and Code by LatLng using AMap Web API or internal logic
  Future<void> _reverseGeocode(LatLng position) async {
    try {
      // Mocked Reverse Geocode logic based on typical coordinates
      // Here you would normally use a geocoding API like amap or flutter_geocoder
      // For demonstration, map ranges or use simple API:
      final response = await http.get(Uri.parse('https://restapi.amap.com/v3/geocode/regeo?key=0eb8479e0dc15a0c3bb79a95786968db&location=${position.longitude},${position.latitude}'));
      if (response.statusCode == 200) {
        final data = jsonDecode(response.body);
        if (data['status'] == '1' && data['regeocode'] != null) {
          final addressComponent = data['regeocode']['addressComponent'];
          String adcode = addressComponent['adcode'] ?? '';
          String city = addressComponent['city'] ?? addressComponent['province'] ?? '';
          if (adcode.length == 6) {
             // Ensure it's a city level code (ending in 00)
             adcode = adcode.substring(0, 4) + '00';
          }
          if (mounted) {
            setState(() {
               _selectedRegionCode = adcode.isNotEmpty ? adcode : 'unknown';
               _selectedRegionName = city.isNotEmpty ? city : '未知城市';
            });
          }
        }
      }
    } catch (e) {
      print("Reverse geocode error: $e");
      if (mounted) {
        setState(() {
          _selectedRegionCode = 'unknown';
          _selectedRegionName = '无法获取地区';
        });
      }
    }
  }

  Widget _buildMarkerSelector() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Text("选择地点", style: TextStyle(fontWeight: FontWeight.bold)),
        const SizedBox(height: 8),
        InkWell(
          onTap: () async {
            final result = await Navigator.push(
              context,
              MaterialPageRoute(builder: (_) => const VolunteerMapPage(isSelectionMode: true)),
            );
            if (result != null && result is Obstacle) {
              setState(() {
                _selectedObstacle = result;
                
                // Set Region based on marker's internal region fields if they exist
                // Assuming Obstacle model can be enhanced to carry regionCode/Name
                // If not available on Obstacle, use a fallback logic or call Geocoder
                // For demonstration, if we added regionCode to Obstacle we'd map it here
                // As Obstacle might not have it loaded, we'll try to extract or mock
                // In production, we should reverse geocode the result.position
                // Let's use a dummy fetch or assume Obstacle model has it (will update model later if needed)
                _selectedRegionCode = '310000'; // fallback mock
                _selectedRegionName = '自动识别中...'; 
                _reverseGeocode(result.position); // trigger actual mapping
                
                if (!widget.isObstacleMode) {
                   _selectedType = ContributionType.storeLayout;
                }
                // If in obstacle mode, user manually selects status or newObstacle via RadioListTile
              });
            }
          },
          child: Container(
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              border: Border.all(color: Colors.grey),
              borderRadius: BorderRadius.circular(8),
            ),
            child: Row(
              children: [
                const Icon(Icons.location_on, color: Colors.blue),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    _selectedObstacle?.title ?? (_selectedObstacle?.description ?? "点击选择地点..."),
                    style: TextStyle(
                      color: _selectedObstacle == null ? Colors.grey : Colors.black,
                    ),
                  ),
                ),
                const Icon(Icons.map),
              ],
            ),
          ),
        ),
      ],
    );
  }

  Widget _buildTypeSelector() {
    if (!widget.isObstacleMode) {
      return const SizedBox.shrink(); // No type selector in shop mode
    }
    
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        RadioListTile<ContributionType>(
          title: const Text("状态更新"),
          value: ContributionType.obstacleStatus,
          groupValue: _selectedType,
          onChanged: (v) => setState(() => _selectedType = v!),
          contentPadding: EdgeInsets.zero,
        ),
        RadioListTile<ContributionType>(
          title: const Text("多障碍物近距离合并/独立分开"),
          subtitle: const Text("可能出现位置冲突，提交以供仲裁"),
          value: ContributionType.newObstacle,
          groupValue: _selectedType,
          onChanged: (v) => setState(() => _selectedType = v!),
          contentPadding: EdgeInsets.zero,
        ),
      ],
    );
  }

  Widget _buildDynamicForm() {
    if (widget.isObstacleMode) {
      return _buildStatusForm();
    } else {
      return _buildLayoutForm();
    }
  }

  Widget _buildLayoutForm() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Text("上传店铺内容", style: TextStyle(fontWeight: FontWeight.bold)),
        const SizedBox(height: 8),
        GestureDetector(
          onTap: () async {
             final picker = ImagePicker();
             final picked = await picker.pickImage(source: ImageSource.gallery);
             if (picked != null) {
               setState(() => _layoutImage = File(picked.path));
             }
          },
          child: Container(
            height: 200,
            width: double.infinity,
            color: Colors.grey[200],
            child: _layoutImage == null
                ? const Column(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      Icon(Icons.add_photo_alternate, size: 50, color: Colors.grey),
                      Text("点击上传店铺内容", style: TextStyle(color: Colors.grey)),
                    ],
                  )
                : Image.file(_layoutImage!, fit: BoxFit.contain),
          ),
        ),
        const SizedBox(height: 10),
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceEvenly,
          children: [
            ElevatedButton.icon(
              onPressed: () async {
                 final picker = ImagePicker();
                 final picked = await picker.pickImage(source: ImageSource.camera);
                 if (picked != null) {
                   setState(() => _layoutImage = File(picked.path));
                 }
              },
              icon: const Icon(Icons.camera_alt),
              label: const Text("拍照"),
            ),
            ElevatedButton.icon(
              onPressed: () async {
                 final picker = ImagePicker();
                 final picked = await picker.pickImage(source: ImageSource.gallery);
                 if (picked != null) {
                   setState(() => _layoutImage = File(picked.path));
                 }
              },
              icon: const Icon(Icons.photo_library),
              label: const Text("相册"),
            ),
          ],
        ),
      ],
    );
  }

  Widget _buildStatusForm() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Text("上传现场照片", style: TextStyle(fontWeight: FontWeight.bold)),
        const SizedBox(height: 8),
        GestureDetector(
          onTap: () async {
             final picker = ImagePicker();
             final picked = await picker.pickImage(source: ImageSource.camera);
             if (picked != null) {
               setState(() => _statusImage = File(picked.path));
             }
          },
          child: Container(
            height: 150,
            width: double.infinity,
            color: Colors.grey[200],
            child: _statusImage == null
                ? const Icon(Icons.camera_alt, size: 50, color: Colors.grey)
                : Image.file(_statusImage!, fit: BoxFit.cover),
          ),
        ),
        const SizedBox(height: 10),
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceEvenly,
          children: [
            ElevatedButton.icon(
              onPressed: () async {
                 final picker = ImagePicker();
                 final picked = await picker.pickImage(source: ImageSource.camera);
                 if (picked != null) {
                   setState(() => _statusImage = File(picked.path));
                 }
              },
              icon: const Icon(Icons.camera_alt),
              label: const Text("拍照"),
            ),
            ElevatedButton.icon(
              onPressed: () async {
                 final picker = ImagePicker();
                 final picked = await picker.pickImage(source: ImageSource.gallery);
                 if (picked != null) {
                   setState(() => _statusImage = File(picked.path));
                 }
              },
              icon: const Icon(Icons.photo_library),
              label: const Text("相册"),
            ),
          ],
        ),
        if (_selectedType == ContributionType.obstacleStatus) ...[
          const SizedBox(height: 20),
          const Text("当前状态判定", style: TextStyle(fontWeight: FontWeight.bold)),
          DropdownButton<ObstacleStatus>(
            value: _proposedStatus,
            isExpanded: true,
            items: const [
              DropdownMenuItem(value: ObstacleStatus.active, child: Text("仍然存在 / 施工中")),
              DropdownMenuItem(value: ObstacleStatus.removed, child: Text("已移除 / 施工结束")),
            ],
            onChanged: (v) => setState(() => _proposedStatus = v!),
          ),
        ],
      ],
    );
  }
}
