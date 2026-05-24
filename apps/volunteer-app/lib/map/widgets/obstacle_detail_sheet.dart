import 'dart:io';
import 'package:flutter/material.dart';
import 'package:latlong2/latlong.dart';
import 'package:image_picker/image_picker.dart';
import '../models/obstacle_model.dart';
import '../services/obstacle_service.dart';

class ObstacleDetailSheet extends StatefulWidget {
  final Obstacle obstacle;
  final LatLng? currentPosition;
  final String? username;

  const ObstacleDetailSheet({
    super.key,
    required this.obstacle,
    this.currentPosition,
    this.username,
  });

  @override
  State<ObstacleDetailSheet> createState() => _ObstacleDetailSheetState();
}

class _ObstacleDetailSheetState extends State<ObstacleDetailSheet> {
  late Obstacle _obstacle;
  final ObstacleService _obstacleService = ObstacleService();

  @override
  void initState() {
    super.initState();
    _obstacle = widget.obstacle;
  }

  // 刷新当前障碍物数据
  Future<void> _refreshObstacle() async {
    // 强制刷新远程数据
    await _obstacleService.fetchObstacles();
    final allObstacles = _obstacleService.getObstacles();
    try {
      final updated = allObstacles.firstWhere((o) => o.id == _obstacle.id);
      if (mounted) {
        setState(() {
          _obstacle = updated;
        });
      }
    } catch (e) {
      // 如果找不到（比如被删除了），可能不做处理或者关闭页面
    }
  }

  @override
  Widget build(BuildContext context) {
    return DraggableScrollableSheet(
      initialChildSize: 0.6,
      minChildSize: 0.4,
      maxChildSize: 0.9,
      builder: (context, scrollController) {
        return Container(
          decoration: const BoxDecoration(
            color: Colors.white,
            borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
            boxShadow: [BoxShadow(blurRadius: 10, color: Colors.black12)],
          ),
          child: Column(
            children: [
              // 顶部把手
              Center(
                child: Container(
                  margin: const EdgeInsets.only(top: 12, bottom: 8),
                  width: 40,
                  height: 4,
                  decoration: BoxDecoration(
                    color: Colors.grey[300],
                    borderRadius: BorderRadius.circular(2),
                  ),
                ),
              ),
              Expanded(
                child: ListView(
                  controller: scrollController,
                  padding: const EdgeInsets.all(16),
                  children: [
                    // 标题行 (可编辑)
                    Row(
                      children: [
                        Expanded(
                          child: Text(
                            _obstacle.title ?? "未命名地点",
                            style: const TextStyle(
                              fontSize: 24, 
                              fontWeight: FontWeight.bold
                            ),
                          ),
                        ),
                        IconButton(
                          icon: const Icon(Icons.edit, color: Colors.grey),
                          onPressed: () {
                            _showEditDialog(_obstacle);
                          },
                        ),
                      ],
                    ),
                    const SizedBox(height: 8),
                    
                    // 标签行
                    Wrap(
                      spacing: 8,
                      children: [
                        _buildTag("志愿者推荐", Colors.orange[50]!, Colors.orange),
                        _buildTag("无障碍设施", Colors.green[50]!, Colors.green),
                        _buildTag("盲道直达", Colors.blue[50]!, Colors.blue),
                      ],
                    ),
                    const SizedBox(height: 16),
                    
                    // 距离和地址
                    if (widget.currentPosition != null)
                      Row(
                        children: [
                          const Icon(Icons.location_on, size: 16, color: Colors.grey),
                          const SizedBox(width: 4),
                          Text(
                            "距离您约 ${_calculateDistance(_obstacle.position)} 米",
                            style: const TextStyle(color: Colors.grey),
                          ),
                        ],
                      ),
                    const Divider(height: 24),
                    
                    // 操作栏 (新增：添加图片、删除)
                    Row(
                      mainAxisAlignment: MainAxisAlignment.spaceEvenly,
                      children: [
                        TextButton.icon(
                          onPressed: () {
                            _pickAndUploadImage(_obstacle.id);
                          },
                          icon: const Icon(Icons.add_a_photo, color: Colors.blue),
                          label: const Text("添加图片"),
                        ),
                        TextButton.icon(
                          onPressed: () {
                            _showDeleteObstacleDialog(_obstacle.id);
                          },
                          icon: const Icon(Icons.delete, color: Colors.red),
                          label: const Text("删除标注", style: TextStyle(color: Colors.red)),
                        ),
                      ],
                    ),
                    const Divider(height: 24),

                    // 详情/货物分区
                    if (_obstacle.description != null && _obstacle.description!.isNotEmpty) ...[
                       const Text("详情介绍", style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
                       const SizedBox(height: 8),
                       Text(
                         _obstacle.description!,
                         style: TextStyle(color: Colors.grey[800], height: 1.5),
                       ),
                       const SizedBox(height: 16),
                    ],

                    // 图片展示
                    if (_obstacle.imageUrls.isNotEmpty) ...[
                      SizedBox(
                        height: 120,
                        child: ListView.builder(
                          scrollDirection: Axis.horizontal,
                          itemCount: _obstacle.imageUrls.length,
                          itemBuilder: (context, index) {
                            return GestureDetector(
                              onTap: () {
                                // 打开支持左右滑动的画廊
                                Navigator.push(context, MaterialPageRoute(builder: (_) => 
                                  _buildImageGallery(_obstacle.imageUrls, index)
                                ));
                              },
                              child: Container(
                                margin: const EdgeInsets.only(right: 8),
                                width: 160,
                                decoration: BoxDecoration(
                                  borderRadius: BorderRadius.circular(8),
                                  image: DecorationImage(
                                    image: NetworkImage(_obstacle.imageUrls[index]),
                                    fit: BoxFit.cover,
                                  ),
                                ),
                              ),
                            );
                          },
                        ),
                      ),
                      const SizedBox(height: 24),
                    ],

                    // 志愿者评论区
                    Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        const Text("志愿者评论", style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
                        TextButton.icon(
                          onPressed: () => _showAddCommentDialog(_obstacle),
                          icon: const Icon(Icons.edit, color: Colors.blue),
                          label: const Text("写评论", style: TextStyle(color: Colors.blue, fontWeight: FontWeight.bold)),
                        ),
                      ],
                    ),
                    const SizedBox(height: 8),
                    
                    if (_obstacle.comments.isEmpty)
                      const Padding(
                        padding: EdgeInsets.symmetric(vertical: 20),
                        child: Center(child: Text("暂无评论，快来抢沙发吧！", style: TextStyle(color: Colors.grey))),
                      )
                    else
                      ..._obstacle.comments.map((comment) => Container(
                        margin: const EdgeInsets.only(bottom: 16),
                        padding: const EdgeInsets.all(12),
                        decoration: BoxDecoration(
                          color: Colors.grey[100],
                          borderRadius: BorderRadius.circular(8),
                        ),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Row(
                              children: [
                                const CircleAvatar(
                                  radius: 12,
                                  backgroundColor: Colors.blue,
                                  child: Icon(Icons.person, size: 16, color: Colors.white),
                                ),
                                const SizedBox(width: 8),
                                Text(comment.author, style: const TextStyle(fontWeight: FontWeight.bold)),
                                const Spacer(),
                                Text(
                                  "${comment.createdAt.month}-${comment.createdAt.day}",
                                  style: const TextStyle(color: Colors.grey, fontSize: 12),
                                ),
                              ],
                            ),
                            const SizedBox(height: 8),
                            Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(comment.content),
                                if (comment.imageUrl != null && comment.imageUrl!.isNotEmpty)
                                  Padding(
                                    padding: const EdgeInsets.only(top: 8.0),
                                    child: GestureDetector(
                                      onTap: () => _viewFullImage(comment.imageUrl!),
                                      child: ClipRRect(
                                        borderRadius: BorderRadius.circular(8),
                                        child: Image.network(
                                          comment.imageUrl!,
                                          height: 150,
                                          width: double.infinity,
                                          fit: BoxFit.cover,
                                          errorBuilder: (_,__,___) => const SizedBox(
                                            height: 100, 
                                            child: Center(child: Icon(Icons.broken_image, color: Colors.grey))
                                          ),
                                        ),
                                      ),
                                    ),
                                  ),
                              ],
                            ),
                          ],
                        ),
                      )).toList(),
                    
                    const SizedBox(height: 80), // 底部留白
                  ],
                ),
              ),
            ],
          ),
        );
      },
    );
  }

  int _calculateDistance(LatLng pos) {
    if (widget.currentPosition == null) return 0;
    return (const Distance().as(LengthUnit.Meter, widget.currentPosition!, pos)).toInt();
  }

  Widget _buildTag(String text, Color bgColor, Color textColor) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: bgColor,
        borderRadius: BorderRadius.circular(4),
      ),
      child: Text(text, style: TextStyle(color: textColor, fontSize: 12)),
    );
  }

  // 拍照/选择图片并上传
  Future<void> _pickAndUploadImage(String obstacleId) async {
    final picker = ImagePicker();
    final source = await showModalBottomSheet<ImageSource>(
      context: context,
      builder: (context) => Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          ListTile(
            leading: const Icon(Icons.camera_alt),
            title: const Text("拍照"),
            onTap: () => Navigator.pop(context, ImageSource.camera),
          ),
          ListTile(
            leading: const Icon(Icons.photo_library),
            title: const Text("从相册选择"),
            onTap: () => Navigator.pop(context, ImageSource.gallery),
          ),
        ],
      ),
    );

    if (source == null) return;

    final pickedFile = await picker.pickImage(source: source, imageQuality: 80);
    if (pickedFile != null) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("正在上传图片...")));
      
      final imageUrl = await _obstacleService.uploadImage(obstacleId, pickedFile.path);
      
      if (imageUrl != null) {
        if (mounted) ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("图片上传成功！")));
        _refreshObstacle();
      } else {
        if (mounted) ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("上传失败，请重试")));
      }
    }
  }

  // 查看大图
  void _viewFullImage(String url) {
    showDialog(
      context: context,
      builder: (context) => Dialog(
        insetPadding: EdgeInsets.zero,
        backgroundColor: Colors.black,
        child: Stack(
          alignment: Alignment.center,
          children: [
            InteractiveViewer(
              child: Image.network(
                url,
                fit: BoxFit.contain,
                errorBuilder: (_, __, ___) => const Icon(Icons.broken_image, color: Colors.white),
              ),
            ),
            Positioned(
              top: 40,
              right: 20,
              child: IconButton(
                icon: const Icon(Icons.close, color: Colors.white, size: 30),
                onPressed: () => Navigator.pop(context),
              ),
            ),
          ],
        ),
      ),
    );
  }

  // 编辑店铺信息
  void _showEditDialog(Obstacle obstacle) {
    final titleController = TextEditingController(text: obstacle.title);
    final descController = TextEditingController(text: obstacle.description);
    
    showDialog(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text("编辑店铺信息"),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextField(
              controller: titleController,
              decoration: const InputDecoration(labelText: "店铺名称"),
            ),
            const SizedBox(height: 16),
            TextField(
              controller: descController,
              decoration: const InputDecoration(labelText: "货物分区/详情"),
              maxLines: 4,
            ),
          ],
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: const Text("取消")),
          ElevatedButton(
            onPressed: () async {
              Navigator.pop(context); // 关掉编辑框
              try {
                await _obstacleService.updateObstacle(
                  obstacle,
                  title: titleController.text,
                  description: descController.text,
                );
                
                if (mounted) ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("信息已更新")));
                _refreshObstacle();
              } catch (e) {
                if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("更新失败: $e")));
              }
            },
            child: const Text("保存"),
          ),
        ],
      ),
    );
  }

  void _showDeleteObstacleDialog(String id) {
    showDialog(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text("删除障碍物"),
        content: const Text("确认删除该障碍物标记？此操作将同步给所有用户。"),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: const Text("取消")),
          TextButton(
            onPressed: () {
              _obstacleService.deleteObstacle(id);
              Navigator.pop(context); // Close dialog
              Navigator.pop(context); // Close detail sheet
              if (mounted) ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("障碍物已删除")));
            },
            child: const Text("删除", style: TextStyle(color: Colors.red)),
          ),
        ],
      ),
    );
  }

  // 选择图片但不上传 (返回路径)
  Future<String?> _pickImage() async {
    final picker = ImagePicker();
    final source = await showModalBottomSheet<ImageSource>(
      context: context,
      builder: (context) => Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          ListTile(
            leading: const Icon(Icons.camera_alt),
            title: const Text("拍照"),
            onTap: () => Navigator.pop(context, ImageSource.camera),
          ),
          ListTile(
            leading: const Icon(Icons.photo_library),
            title: const Text("从相册选择"),
            onTap: () => Navigator.pop(context, ImageSource.gallery),
          ),
        ],
      ),
    );

    if (source == null) return null;
    final pickedFile = await picker.pickImage(source: source, imageQuality: 80);
    return pickedFile?.path;
  }

  void _showAddCommentDialog(Obstacle obstacle) {
    final controller = TextEditingController();
    String? selectedImagePath;
    bool isUploading = false;

    showDialog(
      context: context,
      builder: (context) => StatefulBuilder(
        builder: (context, setState) {
          return AlertDialog(
            title: const Text("纠正/评论"),
            content: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  TextField(
                    controller: controller,
                    decoration: const InputDecoration(
                      hintText: "请输入您的评价或纠正信息...",
                      border: OutlineInputBorder(),
                    ),
                    maxLines: 3,
                  ),
                  const SizedBox(height: 10),
                  if (selectedImagePath != null)
                    Stack(
                      alignment: Alignment.topRight,
                      children: [
                        Container(
                          margin: const EdgeInsets.only(top: 8),
                          height: 100,
                          width: double.infinity,
                          decoration: BoxDecoration(
                            border: Border.all(color: Colors.grey),
                            borderRadius: BorderRadius.circular(8),
                          ),
                          child: Image.file(
                            File(selectedImagePath!),
                            fit: BoxFit.cover,
                          ),
                        ),
                        IconButton(
                          icon: const Icon(Icons.close, color: Colors.red),
                          onPressed: () {
                            setState(() {
                              selectedImagePath = null;
                            });
                          },
                        ),
                      ],
                    ),
                  const SizedBox(height: 10),
                  if (!isUploading)
                    TextButton.icon(
                      onPressed: () async {
                        final path = await _pickImage();
                        if (path != null) {
                          setState(() {
                            selectedImagePath = path;
                          });
                        }
                      },
                      icon: const Icon(Icons.add_a_photo),
                      label: const Text("添加图片佐证"),
                    ),
                  if (isUploading)
                    const Padding(
                      padding: EdgeInsets.all(8.0),
                      child: Row(
                        children: [
                          CircularProgressIndicator(strokeWidth: 2),
                          SizedBox(width: 10),
                          Text("正在上传..."),
                        ],
                      ),
                    ),
                ],
              ),
            ),
            actions: [
              TextButton(
                onPressed: isUploading ? null : () => Navigator.pop(context), 
                child: const Text("取消")
              ),
              ElevatedButton(
                onPressed: isUploading ? null : () async {
                  if (controller.text.trim().isEmpty && selectedImagePath == null) {
                    ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("请输入内容或选择图片")));
                    return;
                  }

                  setState(() {
                    isUploading = true;
                  });

                  String? imageUrl;
                  if (selectedImagePath != null) {
                    imageUrl = await _obstacleService.uploadImage(obstacle.id, selectedImagePath!);
                    if (imageUrl == null) {
                      setState(() {
                        isUploading = false;
                      });
                      if (mounted) {
                        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("图片上传失败")));
                      }
                      return;
                    }
                  }

                  final success = await _obstacleService.addComment(
                    obstacle.id, 
                    controller.text.trim(),
                    imageUrl: imageUrl
                  );

                  if (success) {
                    Navigator.pop(context); // Close Dialog
                    if (mounted) ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("发布成功！")));
                    _refreshObstacle();
                  } else {
                    setState(() {
                      isUploading = false;
                    });
                    if (mounted) ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("发布失败，请重试")));
                  }
                },
                child: const Text("发布"),
              ),
            ],
          );
        }
      ),
    );
  }

  Widget _buildImageGallery(List<String> imageUrls, int initialIndex) {
    return Scaffold(
      backgroundColor: Colors.black,
      appBar: AppBar(
        backgroundColor: Colors.black,
        iconTheme: const IconThemeData(color: Colors.white),
        title: Text("${initialIndex + 1} / ${imageUrls.length}", style: const TextStyle(color: Colors.white)),
      ),
      body: PageView.builder(
        itemCount: imageUrls.length,
        controller: PageController(initialPage: initialIndex),
        itemBuilder: (context, index) {
          return InteractiveViewer(
            child: Center(
              child: Image.network(
                imageUrls[index],
                fit: BoxFit.contain,
                errorBuilder: (_, __, ___) => const Icon(Icons.broken_image, color: Colors.white, size: 60),
              ),
            ),
          );
        },
      ),
    );
  }
}
