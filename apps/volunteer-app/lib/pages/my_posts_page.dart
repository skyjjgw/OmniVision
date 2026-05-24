import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'dart:convert';
import 'package:intl/intl.dart';
import '../config/app_config.dart';
import 'post_detail_page.dart';

class MyPostsPage extends StatefulWidget {
  final String username;
  const MyPostsPage({super.key, required this.username});

  @override
  State<MyPostsPage> createState() => _MyPostsPageState();
}

class _MyPostsPageState extends State<MyPostsPage> {
  List<dynamic> _posts = [];
  bool _isLoading = true;

  @override
  void initState() {
    super.initState();
    _fetchMyPosts();
  }

  Future<void> _fetchMyPosts() async {
    try {
      final response = await http.get(Uri.parse('${AppConfig.socketUrl}/api/community/user/posts?email=${widget.username}'));
      if (response.statusCode == 200) {
        final data = jsonDecode(response.body);
        if (data['success']) {
          setState(() {
            _posts = data['posts'];
            _isLoading = false;
          });
        }
      }
    } catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("加载失败: $e")));
      if (mounted) setState(() => _isLoading = false);
    }
  }

  Future<void> _deletePost(int postId) async {
    try {
      final response = await http.post(
        Uri.parse('${AppConfig.socketUrl}/api/community/post/delete'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({
          'post_id': postId,
          'email': widget.username,
        }),
      );
      final data = jsonDecode(response.body);
      if (data['success']) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("删除成功")));
        _fetchMyPosts();
      } else {
        if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(data['message'])));
      }
    } catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("删除失败: $e")));
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.grey[100],
      appBar: AppBar(
        title: const Text("我的动态"),
        elevation: 0,
        backgroundColor: Colors.white,
      ),
      body: _isLoading
          ? const Center(child: CircularProgressIndicator())
          : _posts.isEmpty
              ? const Center(child: Text("暂无动态", style: TextStyle(color: Colors.grey)))
              : ListView.builder(
                  padding: const EdgeInsets.all(16),
                  itemCount: _posts.length,
                  itemBuilder: (context, index) {
                    final post = _posts[index];
                    return GestureDetector(
                      onTap: () {
                        Navigator.push(context, MaterialPageRoute(builder: (_) => PostDetailPage(post: post, username: widget.username)));
                      },
                      child: Container(
                        margin: const EdgeInsets.only(bottom: 16),
                        decoration: BoxDecoration(
                          color: Colors.white,
                          borderRadius: BorderRadius.circular(16),
                          boxShadow: [
                            BoxShadow(
                              color: Colors.black.withOpacity(0.05),
                              blurRadius: 10,
                              offset: const Offset(0, 4),
                            )
                          ],
                        ),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Padding(
                              padding: const EdgeInsets.all(12),
                              child: Row(
                                children: [
                                  CircleAvatar(
                                    radius: 20,
                                    backgroundImage: post['avatar_path'] != null 
                                        ? NetworkImage('${AppConfig.socketUrl}/${post['avatar_path']}') 
                                        : null,
                                    child: post['avatar_path'] == null ? Text((post['nickname'] ?? post['email'] ?? '?')[0].toUpperCase()) : null,
                                  ),
                                  const SizedBox(width: 12),
                                  Expanded(
                                    child: Column(
                                      crossAxisAlignment: CrossAxisAlignment.start,
                                      children: [
                                        Text(
                                          post['nickname'] ?? post['email'] ?? 'Unknown',
                                          style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 15),
                                        ),
                                        Text(
                                          DateFormat('yyyy-MM-dd HH:mm').format(
                                              DateTime.fromMillisecondsSinceEpoch(((post['time'] ?? 0) * 1000).round())),
                                          style: TextStyle(color: Colors.grey[500], fontSize: 12),
                                        ),
                                      ],
                                    ),
                                  ),
                                  IconButton(
                                    icon: const Icon(Icons.delete_outline, color: Colors.red),
                                    onPressed: () => showDialog(
                                      context: context,
                                      builder: (ctx) => AlertDialog(
                                        title: const Text("确认删除"),
                                        content: const Text("确定要删除这条动态吗？"),
                                        actions: [
                                          TextButton(
                                            onPressed: () => Navigator.pop(ctx),
                                            child: const Text("取消"),
                                          ),
                                          TextButton(
                                            onPressed: () {
                                              Navigator.pop(ctx);
                                              _deletePost(post['id']);
                                            },
                                            child: const Text("删除", style: TextStyle(color: Colors.red)),
                                          ),
                                        ],
                                      ),
                                    ),
                                  ),
                                ],
                              ),
                            ),
                            if (post['content'] != null && post['content'].isNotEmpty)
                              Padding(
                                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
                                child: Text(
                                  post['content'], 
                                  style: const TextStyle(fontSize: 16, height: 1.4),
                                  maxLines: 3,
                                  overflow: TextOverflow.ellipsis,
                                ),
                              ),
                            if (post['image'] != null)
                              Padding(
                                padding: const EdgeInsets.only(top: 8),
                                child: ClipRRect(
                                  borderRadius: const BorderRadius.vertical(bottom: Radius.circular(16)),
                                  child: Image.network(
                                    '${AppConfig.socketUrl}/${post['image']}',
                                    fit: BoxFit.cover,
                                    width: double.infinity,
                                    height: 200,
                                    errorBuilder: (context, error, stackTrace) =>
                                        Container(height: 200, color: Colors.grey[200], child: const Icon(Icons.broken_image)),
                                  ),
                                ),
                              ),
                            if (post['image'] == null)
                               const SizedBox(height: 12),
                          ],
                        ),
                      ),
                    );
                  },
                ),
    );
  }
}
