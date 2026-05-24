import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'dart:convert';
import 'package:intl/intl.dart';
import '../config/app_config.dart';

class CommentSheet extends StatefulWidget {
  final int postId;
  final String username;

  const CommentSheet({super.key, required this.postId, required this.username});

  @override
  State<CommentSheet> createState() => _CommentSheetState();
}

class _CommentSheetState extends State<CommentSheet> {
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
      final response = await http.get(Uri.parse('${AppConfig.socketUrl}/api/community/comments?post_id=${widget.postId}'));
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
      print("Fetch comments error: $e");
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
          'post_id': widget.postId,
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

  Future<void> _deleteComment(int commentId) async {
    try {
      final response = await http.post(
        Uri.parse('${AppConfig.socketUrl}/api/community/comment/delete'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({
          'comment_id': commentId,
          'email': widget.username,
        }),
      );
      final data = jsonDecode(response.body);
      if (data['success']) {
        _fetchComments();
      } else {
        if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(data['message'])));
      }
    } catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("删除失败: $e")));
    }
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.only(bottom: MediaQuery.of(context).viewInsets.bottom),
      child: Container(
        height: 500,
        padding: const EdgeInsets.all(16),
        child: Column(
          children: [
            const Text("评论", style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
            const Divider(),
            Expanded(
              child: _isLoading
                  ? const Center(child: CircularProgressIndicator())
                  : _comments.isEmpty 
                      ? const Center(child: Text("暂无评论"))
                      : ListView.builder(
                          itemCount: _comments.length,
                          itemBuilder: (context, index) {
                            final comment = _comments[index];
                            final isMe = comment['email'] == widget.username;
                            return ListTile(
                              leading: CircleAvatar(
                                backgroundImage: comment['avatar_path'] != null 
                                    ? NetworkImage('${AppConfig.socketUrl}/${comment['avatar_path']}') 
                                    : null,
                                child: comment['avatar_path'] == null ? Text((comment['nickname'] ?? comment['email'] ?? '?')[0].toUpperCase()) : null,
                              ),
                              title: Text(comment['nickname'] ?? comment['email'], style: const TextStyle(fontSize: 12, color: Colors.grey)),
                              subtitle: Text(comment['content']),
                              trailing: Row(
                                mainAxisSize: MainAxisSize.min,
                                children: [
                                  Text(DateFormat('MM-dd HH:mm').format(
                                      DateTime.fromMillisecondsSinceEpoch((comment['time'] * 1000).round()))),
                                  if (isMe)
                                    IconButton(
                                      icon: const Icon(Icons.delete, color: Colors.red, size: 20),
                                      onPressed: () => _deleteComment(comment['id']),
                                    ),
                                ],
                              ),
                            );
                          },
                        ),
            ),
            Row(
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
                const SizedBox(width: 8),
                IconButton(
                  icon: const Icon(Icons.send, color: Colors.blue),
                  onPressed: _submitComment,
                )
              ],
            )
          ],
        ),
      ),
    );
  }
}
