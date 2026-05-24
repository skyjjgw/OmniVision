import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import '../models/contribution_model.dart';
import '../services/contribution_service.dart';
import '../config/app_config.dart';

class ContributionDetailPage extends StatefulWidget {
  final ContributionModel contribution;
  final String username;
  final VoidCallback onRefresh; // Callback to refresh parent list

  const ContributionDetailPage({
    super.key, 
    required this.contribution, 
    required this.username,
    required this.onRefresh,
  });

  @override
  State<ContributionDetailPage> createState() => _ContributionDetailPageState();
}

class _ContributionDetailPageState extends State<ContributionDetailPage> {
  late ContributionModel _item;
  final ContributionService _contributionService = ContributionService();
  final TextEditingController _commentController = TextEditingController();
  bool _isRefreshing = false;

  Color _reviewColor(String status) {
    switch (status) {
      case 'approve':
        return Colors.green;
      case 'reject':
        return Colors.red;
      case 'manual_review':
        return Colors.orange;
      default:
        return Colors.amber;
    }
  }

  String _reviewLabel(String status) {
    switch (status) {
      case 'approve':
        return 'AI已通过';
      case 'reject':
        return 'AI未通过';
      case 'manual_review':
        return 'AI待复核';
      default:
        return '待审核';
    }
  }

  @override
  void initState() {
    super.initState();
    _item = widget.contribution;
  }

  Future<void> _refresh() async {
    setState(() => _isRefreshing = true);
    // Fetch updated data for this specific contribution
    // Since we only have fetchAll currently, we can fetch all and find this one,
    // or better, pass the updated item back from list refresh if we had that.
    // For now, let's re-fetch the list and find this item.
    final all = await _contributionService.fetchContributions();
    try {
      final updated = all.firstWhere((c) => c.id == _item.id);
      if (mounted) {
        setState(() {
          _item = updated;
          _isRefreshing = false;
        });
      }
    } catch (e) {
      // Item might be deleted
      if (mounted) {
        setState(() => _isRefreshing = false);
        Navigator.pop(context); // Close detail if deleted
      }
    }
  }

  Future<void> _vote(String type) async {
    final success = await _contributionService.voteContribution(_item.id, widget.username, type);
    if (success) {
      await _refresh();
      widget.onRefresh();
    }
  }

  Future<void> _sendComment() async {
    if (_commentController.text.isEmpty) return;
    
    final success = await _contributionService.addComment(
      _item.id, 
      widget.username, 
      widget.username, 
      _commentController.text
    );
    
    if (success) {
      _commentController.clear();
      FocusScope.of(context).unfocus();
      await _refresh();
      widget.onRefresh();
    }
  }

  @override
  Widget build(BuildContext context) {
    final isUpvoted = _item.upvotes.contains(widget.username);
    final isDownvoted = _item.downvotes.contains(widget.username);
    final age = DateTime.now().difference(_item.createdAt);
    final remainingSeconds = _item.auditPeriod - age.inSeconds;
    final remainingDays = (remainingSeconds / 86400).toStringAsFixed(1);

    return Scaffold(
      appBar: AppBar(title: const Text("帖子详情")),
      body: _isRefreshing 
          ? const Center(child: CircularProgressIndicator())
          : Column(
            children: [
              Expanded(
                child: SingleChildScrollView(
                  padding: const EdgeInsets.all(16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      // Author Info
                      Row(
                        children: [
                          CircleAvatar(
                            backgroundColor: Colors.blue[100],
                            child: Text(_item.userNickname[0].toUpperCase()),
                          ),
                          const SizedBox(width: 12),
                          Expanded(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(_item.userNickname, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
                                Text(DateFormat('yyyy-MM-dd HH:mm').format(_item.createdAt), style: const TextStyle(color: Colors.grey, fontSize: 12)),
                              ],
                            ),
                          ),
                          if (_item.userId == widget.username)
                            IconButton(
                              icon: const Icon(Icons.delete_outline, color: Colors.red),
                              onPressed: () async {
                                final success = await _contributionService.deleteContribution(_item.id, widget.username);
                                if (success) {
                                  widget.onRefresh();
                                  if (mounted) Navigator.pop(context);
                                }
                              },
                            ),
                        ],
                      ),
                      const SizedBox(height: 16),
                      
                      // Content
                      Text(_item.content, style: const TextStyle(fontSize: 18)),
                      const SizedBox(height: 16),
                      
                      // Tags/Badges
                      Wrap(
                        spacing: 8,
                        children: [
                           Chip(
                             label: Text(_item.type == ContributionType.storeLayout ? "店铺结构" : "状态更新"),
                             backgroundColor: Colors.blue[50],
                             labelStyle: const TextStyle(color: Colors.blue),
                           ),
                           if (_item.type == ContributionType.storeLayout && _item.zones != null)
                             ..._item.zones!.map((z) => Chip(label: Text(z))),
                           
                           if (_item.type == ContributionType.obstacleStatus && _item.proposedStatus != null)
                             Chip(
                               label: Text("判定为: ${_item.proposedStatus == ObstacleStatus.active ? '仍然存在' : '已移除'}"),
                               backgroundColor: Colors.orange[50],
                               labelStyle: const TextStyle(color: Colors.orange),
                             ),
                           
                           Chip(
                             label: Text("剩余公示: $remainingDays天"),
                             backgroundColor: Colors.grey[200],
                           ),
                           Chip(
                             label: Text(_reviewLabel(_item.reviewStatus)),
                             backgroundColor: _reviewColor(_item.reviewStatus).withOpacity(0.12),
                             labelStyle: TextStyle(color: _reviewColor(_item.reviewStatus), fontWeight: FontWeight.w700),
                           ),
                        ],
                      ),
                      const SizedBox(height: 16),

                      // Image
                      if (_item.imageUrl != null)
                        ClipRRect(
                          borderRadius: BorderRadius.circular(12),
                          child: Image.network(
                            _item.imageUrl!.startsWith('http') 
                                ? _item.imageUrl! 
                                : '${AppConfig.socketUrl}/${_item.imageUrl}',
                            width: double.infinity,
                            fit: BoxFit.cover,
                            errorBuilder: (ctx, err, stack) => const SizedBox(height: 100, child: Center(child: Text("图片加载失败"))),
                          ),
                        ),

                      if (_item.aiAnalysisResult.isNotEmpty) ...[
                        const SizedBox(height: 16),
                        Container(
                          width: double.infinity,
                          padding: const EdgeInsets.all(14),
                          decoration: BoxDecoration(
                            color: Colors.indigo[50],
                            borderRadius: BorderRadius.circular(14),
                            border: Border.all(color: Colors.indigo.shade100),
                          ),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              const Text("审核摘要", style: TextStyle(fontWeight: FontWeight.bold, color: Colors.indigo)),
                              const SizedBox(height: 8),
                              Text(_item.aiAnalysisResult, style: const TextStyle(height: 1.6)),
                              if (_item.aiConfidence != null) ...[
                                const SizedBox(height: 8),
                                Text("AI置信度：${_item.aiConfidence!.toStringAsFixed(0)}", style: TextStyle(color: Colors.indigo[700], fontSize: 12)),
                              ],
                            ],
                          ),
                        ),
                      ],

                      if (_item.blindGuidanceSummary.isNotEmpty) ...[
                        const SizedBox(height: 12),
                        Container(
                          width: double.infinity,
                          padding: const EdgeInsets.all(14),
                          decoration: BoxDecoration(
                            color: Colors.green[50],
                            borderRadius: BorderRadius.circular(14),
                            border: Border.all(color: Colors.green.shade100),
                          ),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              const Text("导盲摘要", style: TextStyle(fontWeight: FontWeight.bold, color: Colors.green)),
                              const SizedBox(height: 8),
                              Text(_item.blindGuidanceSummary, style: const TextStyle(height: 1.6)),
                            ],
                          ),
                        ),
                      ],
                      
                      const SizedBox(height: 16),
                      const Divider(),
                      
                      // Stats
                      Row(
                        mainAxisAlignment: MainAxisAlignment.spaceAround,
                        children: [
                          _buildStatButton(
                            icon: isUpvoted ? Icons.thumb_up : Icons.thumb_up_outlined,
                            color: isUpvoted ? Colors.green : Colors.grey,
                            count: _item.upvotes.length,
                            onTap: () => _vote('up'),
                          ),
                          _buildStatButton(
                            icon: isDownvoted ? Icons.thumb_down : Icons.thumb_down_outlined,
                            color: isDownvoted ? Colors.red : Colors.grey,
                            count: _item.downvotes.length,
                            onTap: () => _vote('down'),
                          ),
                          _buildStatButton(
                            icon: Icons.comment_outlined,
                            color: Colors.grey,
                            count: _item.comments.length,
                            onTap: () {}, // Focus input?
                          ),
                        ],
                      ),
                      const Divider(),
                      
                      // Comments List
                      const SizedBox(height: 10),
                      const Text("评论", style: TextStyle(fontWeight: FontWeight.bold, fontSize: 18)),
                      const SizedBox(height: 10),
                      ListView.separated(
                        shrinkWrap: true,
                        physics: const NeverScrollableScrollPhysics(),
                        itemCount: _item.comments.length,
                        separatorBuilder: (ctx, idx) => const Divider(height: 1),
                        itemBuilder: (ctx, idx) {
                          final c = _item.comments[idx];
                          return ListTile(
                            contentPadding: EdgeInsets.zero,
                            leading: CircleAvatar(
                              radius: 16,
                              backgroundColor: Colors.grey[300],
                              child: Text(c.userNickname[0].toUpperCase(), style: const TextStyle(fontSize: 12)),
                            ),
                            title: Row(
                              children: [
                                Text(c.userNickname, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 14)),
                                const SizedBox(width: 8),
                                Text(DateFormat('MM-dd HH:mm').format(c.createdAt), style: const TextStyle(fontSize: 12, color: Colors.grey)),
                              ],
                            ),
                            subtitle: Text(c.content, style: const TextStyle(color: Colors.black87)),
                          );
                        },
                      ),
                    ],
                  ),
                ),
              ),
              
              // Comment Input
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
                decoration: BoxDecoration(
                  color: Colors.white,
                  boxShadow: [BoxShadow(color: Colors.black12, blurRadius: 4, offset: const Offset(0, -2))],
                ),
                child: SafeArea(
                  child: Row(
                    children: [
                      Expanded(
                        child: TextField(
                          controller: _commentController,
                          decoration: InputDecoration(
                            hintText: "发布你的评论...",
                            border: OutlineInputBorder(borderRadius: BorderRadius.circular(20), borderSide: BorderSide.none),
                            filled: true,
                            fillColor: Colors.grey[100],
                            contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
                          ),
                        ),
                      ),
                      const SizedBox(width: 8),
                      IconButton(
                        icon: const Icon(Icons.send, color: Colors.blue),
                        onPressed: _sendComment,
                      ),
                    ],
                  ),
                ),
              ),
            ],
          ),
    );
  }

  Widget _buildStatButton({required IconData icon, required Color color, required int count, required VoidCallback onTap}) {
    return InkWell(
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.all(8.0),
        child: Row(
          children: [
            Icon(icon, color: color),
            const SizedBox(width: 4),
            Text("$count", style: TextStyle(color: color)),
          ],
        ),
      ),
    );
  }
}
