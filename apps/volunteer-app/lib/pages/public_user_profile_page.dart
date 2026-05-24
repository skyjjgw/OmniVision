import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:intl/intl.dart';
import '../config/app_config.dart';
import 'post_detail_page.dart';

class PublicUserProfilePage extends StatefulWidget {
  final String targetEmail;
  final String currentUsername;
  const PublicUserProfilePage({super.key, required this.targetEmail, required this.currentUsername});

  @override
  State<PublicUserProfilePage> createState() => _PublicUserProfilePageState();
}

class _PublicUserProfilePageState extends State<PublicUserProfilePage> {
  List<dynamic> _posts = [];
  bool _isLoading = true;
  Map<String, dynamic>? _profile;

  @override
  void initState() {
    super.initState();
    _fetchData();
  }

  Future<void> _fetchData() async {
    // Fetch profile and posts
    try {
      final profileResp = await http.get(Uri.parse('${AppConfig.socketUrl}/api/user/profile?email=${widget.targetEmail}'));
      if (profileResp.statusCode == 200) {
        final data = jsonDecode(profileResp.body);
        if (data['success']) setState(() => _profile = data['profile']);
      }

      final postsResp = await http.get(Uri.parse('${AppConfig.socketUrl}/api/community/user/posts?email=${widget.targetEmail}'));
      if (postsResp.statusCode == 200) {
        final data = jsonDecode(postsResp.body);
        if (data['success']) setState(() => _posts = data['posts']);
      }
    } catch (e) {
      print("Error fetching user data: $e");
    } finally {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.grey[100],
      appBar: AppBar(
        title: Text(_profile?['nickname'] ?? "用户主页"),
        elevation: 0,
        backgroundColor: Colors.blue,
      ),
      body: Column(
        children: [
          Container(
            padding: const EdgeInsets.only(bottom: 30, left: 20, right: 20, top: 10),
            decoration: const BoxDecoration(
              color: Colors.blue,
              borderRadius: BorderRadius.vertical(bottom: Radius.circular(30)),
              boxShadow: [BoxShadow(color: Colors.black12, blurRadius: 10, offset: Offset(0, 5))],
            ),
            width: double.infinity,
            child: Column(
              children: [
                CircleAvatar(
                  radius: 40,
                  backgroundColor: Colors.white,
                  child: CircleAvatar(
                    radius: 38,
                    backgroundImage: _profile?['avatar_path'] != null 
                        ? NetworkImage('${AppConfig.socketUrl}/${_profile!['avatar_path']}') 
                        : null,
                    child: _profile?['avatar_path'] == null ? const Icon(Icons.person, size: 40, color: Colors.grey) : null,
                  ),
                ),
                const SizedBox(height: 10),
                Text(
                  _profile?['nickname'] ?? widget.targetEmail, 
                  style: const TextStyle(color: Colors.white, fontSize: 20, fontWeight: FontWeight.bold)
                ),
                const SizedBox(height: 4),
                Text(
                  widget.targetEmail, 
                  style: TextStyle(color: Colors.white.withOpacity(0.8), fontSize: 14)
                ),
              ],
            ),
          ),
          Expanded(
            child: _isLoading 
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
                              Navigator.push(context, MaterialPageRoute(builder: (_) => PostDetailPage(post: post, username: widget.currentUsername)));
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
          ),
        ],
      ),
    );
  }
}
