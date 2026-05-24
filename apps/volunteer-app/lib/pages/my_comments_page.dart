import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import '../models/contribution_model.dart';
import '../services/contribution_service.dart';
import '../services/theme_service.dart';
import '../config/app_config.dart';
import 'contribution_detail_page.dart';

class MyCommentsPage extends StatefulWidget {
  final String username;

  const MyCommentsPage({super.key, required this.username});

  @override
  State<MyCommentsPage> createState() => _MyCommentsPageState();
}

class _MyCommentsPageState extends State<MyCommentsPage> {
  final ContributionService _contributionService = ContributionService();
  final ThemeService _themeService = ThemeService();
  List<Map<String, dynamic>> _myComments = []; // Stores {comment: ContributionComment, contribution: ContributionModel}
  bool _isLoading = true;

  @override
  void initState() {
    super.initState();
    _fetchMyComments();
  }

  Future<void> _fetchMyComments() async {
    setState(() => _isLoading = true);
    final result = await _contributionService.fetchContributionsWithMetadata();
    final allContributions = result.contributions;
    
    List<Map<String, dynamic>> comments = [];
    
    for (var contribution in allContributions) {
      for (var comment in contribution.comments) {
        if (comment.userId == widget.username) {
          comments.add({
            'comment': comment,
            'contribution': contribution,
          });
        }
      }
    }
    
    // Sort by comment time descending
    comments.sort((a, b) {
      final c1 = a['comment'] as ContributionComment;
      final c2 = b['comment'] as ContributionComment;
      return c2.createdAt.compareTo(c1.createdAt);
    });

    if (mounted) {
      setState(() {
        _myComments = comments;
        _isLoading = false;
      });
    }
  }

  Future<void> _deleteComment(ContributionModel contribution, ContributionComment comment) async {
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text("确认删除"),
        content: const Text("确定要删除这条评论吗？"),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text("取消")),
          TextButton(
            onPressed: () async {
              Navigator.pop(ctx);
              final success = await _contributionService.deleteComment(contribution.id, comment.id, widget.username);
              if (success) {
                if (mounted) ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("删除成功")));
                _fetchMyComments();
              } else {
                if (mounted) ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("删除失败")));
              }
            },
            child: const Text("删除", style: TextStyle(color: Colors.red)),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final currentTheme = _themeService.currentTheme;

    return Scaffold(
      appBar: AppBar(
        title: const Text("我的评论"),
        backgroundColor: currentTheme.gradientColors.first,
        foregroundColor: Colors.white,
      ),
      body: _isLoading
          ? const Center(child: CircularProgressIndicator())
          : _myComments.isEmpty
              ? const Center(child: Text("暂无评论", style: TextStyle(color: Colors.grey)))
              : ListView.builder(
                  padding: const EdgeInsets.all(16),
                  itemCount: _myComments.length,
                  itemBuilder: (context, index) {
                    final item = _myComments[index];
                    final comment = item['comment'] as ContributionComment;
                    final contribution = item['contribution'] as ContributionModel;
                    
                    return GestureDetector(
                      onTap: () {
                        Navigator.push(
                          context,
                          MaterialPageRoute(builder: (_) => ContributionDetailPage(
                            contribution: contribution,
                            username: widget.username,
                            onRefresh: _fetchMyComments,
                          )),
                        ).then((_) => _fetchMyComments()); // Refresh when returning from detail page
                      },
                      child: Card(
                        margin: const EdgeInsets.only(bottom: 12),
                        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            // Header: Replied to...
                            Container(
                              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                              width: double.infinity,
                              decoration: BoxDecoration(
                                color: Colors.grey[50],
                                borderRadius: const BorderRadius.vertical(top: Radius.circular(12)),
                                border: Border(bottom: BorderSide(color: Colors.grey[200]!)),
                              ),
                              child: Text(
                                "回复帖子: ${contribution.markerTitle}",
                                style: TextStyle(color: Colors.grey[600], fontSize: 12),
                                maxLines: 1,
                                overflow: TextOverflow.ellipsis,
                              ),
                            ),
                            
                            Padding(
                              padding: const EdgeInsets.all(12),
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text(
                                    comment.content,
                                    style: const TextStyle(fontSize: 15),
                                  ),
                                  const SizedBox(height: 8),
                                  Text(
                                     DateFormat('yyyy-MM-dd HH:mm').format(comment.createdAt),
                                     style: TextStyle(color: Colors.grey[400], fontSize: 11),
                                   ),
                                 ],
                               ),
                             ),
                             IconButton(
                               icon: const Icon(Icons.delete_outline, color: Colors.red, size: 20),
                               onPressed: () => _deleteComment(contribution, comment),
                             ),
                           ],
                         ),
                       ),
                     );
                   },
                 ),
    );
  }
}
