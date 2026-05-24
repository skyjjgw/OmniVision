import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:intl/intl.dart';
import '../config/app_config.dart';
import 'public_user_profile_page.dart';

class PostDetailPage extends StatefulWidget {
  final Map<String, dynamic> post;
  final String username;

  const PostDetailPage({super.key, required this.post, required this.username});

  @override
  State<PostDetailPage> createState() => _PostDetailPageState();
}

class _PostDetailPageState extends State<PostDetailPage> {
  final TextEditingController _commentController = TextEditingController();
  List<dynamic> _comments = [];
  bool _isLoading = true;

  @override
  void initState() {
    super.initState();
    _fetchComments();
  }

  Future<void> _fetchComments() async {
    try {
      final response = await http.get(Uri.parse('${AppConfig.socketUrl}/api/community/comments?post_id=${widget.post['id']}'));
      if (response.statusCode == 200) {
        final data = jsonDecode(response.body);
        if (data['success']) {
          setState(() {
            _comments = data['comments'];
            _isLoading = false;
          });
        }
      }
    } catch (e) {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  Future<void> _submitComment() async {
    if (_commentController.text.isEmpty) return;
    try {
      final response = await http.post(
        Uri.parse('${AppConfig.socketUrl}/api/community/comment'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({
          'post_id': widget.post['id'],
          'email': widget.username,
          'content': _commentController.text
        }),
      );
      final data = jsonDecode(response.body);
      if (data['success']) {
        _commentController.clear();
        _fetchComments();
      } else {
        if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(data['message'])));
      }
    } catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("评论失败: $e")));
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.grey[100],
      appBar: AppBar(
        title: const Text("帖子详情"),
        backgroundColor: Colors.white,
        elevation: 0,
      ),
      body: Column(
        children: [
          Expanded(
            child: ListView(
              padding: const EdgeInsets.only(bottom: 20),
              children: [
                // Post Content
                Container(
                  color: Colors.white,
                  margin: const EdgeInsets.only(bottom: 12),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Padding(
                        padding: const EdgeInsets.all(16),
                        child: Row(
                          children: [
                            GestureDetector(
                              onTap: () {
                                Navigator.push(context, MaterialPageRoute(builder: (_) => PublicUserProfilePage(targetEmail: widget.post['email'], currentUsername: widget.username)));
                              },
                              child: CircleAvatar(
                                radius: 24,
                                backgroundImage: widget.post['avatar_path'] != null 
                                    ? NetworkImage('${AppConfig.socketUrl}/${widget.post['avatar_path']}') 
                                    : null,
                                child: widget.post['avatar_path'] == null ? Text((widget.post['nickname'] ?? widget.post['email'] ?? '?')[0].toUpperCase()) : null,
                              ),
                            ),
                            const SizedBox(width: 12),
                            Expanded(
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text(
                                    widget.post['nickname'] ?? widget.post['email'] ?? 'Unknown',
                                    style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 16),
                                  ),
                                  Text(
                                    DateFormat('yyyy-MM-dd HH:mm').format(
                                        DateTime.fromMillisecondsSinceEpoch(((widget.post['time'] ?? 0) * 1000).round())),
                                    style: TextStyle(color: Colors.grey[500], fontSize: 12),
                                  ),
                                ],
                              ),
                            ),
                          ],
                        ),
                      ),
                      if (widget.post['content'] != null && widget.post['content'].isNotEmpty)
                        Padding(
                          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
                          child: Text(
                            widget.post['content'], 
                            style: const TextStyle(fontSize: 16, height: 1.5),
                          ),
                        ),
                      if (widget.post['image'] != null)
                        Padding(
                          padding: const EdgeInsets.only(top: 8),
                          child: Image.network(
                            (widget.post['image'] as String).startsWith('http') 
                                ? (widget.post['image'] as String).replaceAll('10.0.2.2', AppConfig.serverIp).replaceAll('47.97.184.133', AppConfig.serverIp) 
                                : '${AppConfig.socketUrl}/${widget.post['image']}',
                            fit: BoxFit.cover,
                            width: double.infinity,
                          ),
                        ),
                      const SizedBox(height: 16),
                    ],
                  ),
                ),
                
                // Comments Section Header
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                  child: Text(
                    "全部评论 (${_comments.length})", 
                    style: TextStyle(fontWeight: FontWeight.bold, color: Colors.grey[700], fontSize: 14)
                  ),
                ),
                
                if (_isLoading)
                  const Center(child: Padding(padding: EdgeInsets.all(20), child: CircularProgressIndicator()))
                else if (_comments.isEmpty)
                  const Center(child: Padding(padding: EdgeInsets.all(40), child: Text("暂无评论，快来抢沙发吧~", style: TextStyle(color: Colors.grey))))
                else
                  ..._comments.map((comment) => Container(
                    color: Colors.white,
                    margin: const EdgeInsets.only(bottom: 1), // Divider effect
                    padding: const EdgeInsets.all(16),
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        GestureDetector(
                          onTap: () {
                             Navigator.push(context, MaterialPageRoute(builder: (_) => PublicUserProfilePage(targetEmail: comment['email'], currentUsername: widget.username)));
                          },
                          child: CircleAvatar(
                            radius: 18,
                            backgroundImage: comment['avatar_path'] != null 
                                ? NetworkImage('${AppConfig.socketUrl}/${comment['avatar_path']}') 
                                : null,
                            child: comment['avatar_path'] == null ? Text((comment['nickname'] ?? comment['email'] ?? '?')[0].toUpperCase(), style: const TextStyle(fontSize: 12)) : null,
                          ),
                        ),
                        const SizedBox(width: 12),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Row(
                                children: [
                                  Text(
                                    comment['nickname'] ?? comment['email'], 
                                    style: const TextStyle(fontSize: 14, fontWeight: FontWeight.bold, color: Colors.grey),
                                  ),
                                  const Spacer(),
                                  Text(
                                    DateFormat('MM-dd HH:mm').format(
                                        DateTime.fromMillisecondsSinceEpoch(((comment['time'] ?? 0) * 1000).round())),
                                    style: TextStyle(fontSize: 12, color: Colors.grey[400]),
                                  ),
                                ],
                              ),
                              const SizedBox(height: 6),
                              Text(
                                comment['content'],
                                style: const TextStyle(fontSize: 15),
                              ),
                            ],
                          ),
                        ),
                      ],
                    ),
                  )),
              ],
            ),
          ),
          
          // Comment Input
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
            decoration: BoxDecoration(
              color: Colors.white, 
              boxShadow: [BoxShadow(blurRadius: 5, color: Colors.black.withOpacity(0.05), offset: const Offset(0, -2))]
            ),
            child: SafeArea(
              child: Row(
                children: [
                  Expanded(
                    child: Container(
                      padding: const EdgeInsets.symmetric(horizontal: 16),
                      decoration: BoxDecoration(
                        color: Colors.grey[100],
                        borderRadius: BorderRadius.circular(24),
                      ),
                      child: TextField(
                        controller: _commentController,
                        decoration: const InputDecoration(
                          hintText: "写下你的评论...", 
                          border: InputBorder.none,
                          isDense: true,
                          contentPadding: EdgeInsets.symmetric(vertical: 10),
                          filled: false,
                        ),
                        minLines: 1,
                        maxLines: 3,
                      ),
                    ),
                  ),
                  const SizedBox(width: 12),
                  GestureDetector(
                    onTap: _submitComment,
                    child: Container(
                      padding: const EdgeInsets.all(10),
                      decoration: BoxDecoration(
                        color: Theme.of(context).primaryColor,
                        shape: BoxShape.circle,
                      ),
                      child: const Icon(Icons.send, color: Colors.white, size: 20),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}
