import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import '../models/contribution_model.dart';
import '../services/contribution_service.dart';
import '../services/theme_service.dart';
import '../config/app_config.dart';
import 'create_contribution_page.dart';
import 'contribution_detail_page.dart';

class CommunityPage extends StatefulWidget {
  final String username;
  const CommunityPage({super.key, required this.username});

  @override
  State<CommunityPage> createState() => _CommunityPageState();
}

class _CommunityPageState extends State<CommunityPage> {
  final ContributionService _contributionService = ContributionService();
  final ThemeService _themeService = ThemeService();
  List<ContributionModel> _contributions = [];
  bool _isLoading = true;

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
        return '已通过';
      case 'reject':
        return '未通过';
      case 'manual_review':
        return '待复核';
      default:
        return '待审核';
    }
  }
  
  // Region and Forum options are fetched dynamically from the backend now
  // We keep 'all' as the default selection
  String _selectedRegionCode = 'all';
  String _selectedForumKey = 'all';

  List<Map<String, String>> _regionOptions = [
    {'code': 'all', 'name': '全国/所有地区'},
  ];

  List<Map<String, String>> _forumOptions = [
    {'key': 'all', 'name': '全部板块'},
  ];

  @override
  void initState() {
    super.initState();
    _fetchData();
    _themeService.addListener(_onThemeChanged);
  }

  @override
  void dispose() {
    _themeService.removeListener(_onThemeChanged);
    super.dispose();
  }

  void _onThemeChanged() {
    if (mounted) setState(() {});
  }

  Future<void> _fetchData() async {
    setState(() => _isLoading = true);
    final result = await _contributionService.fetchContributionsWithMetadata(
      targetType: 'shop',
      regionCode: _selectedRegionCode == 'all' ? null : _selectedRegionCode,
      forumKey: _selectedForumKey == 'all' ? null : _selectedForumKey,
    );
    if (mounted) {
      setState(() {
        _contributions = result.contributions;
        
        // Merge dynamic regions
        final Map<String, Map<String, String>> regionMap = {'all': {'code': 'all', 'name': '全国/所有地区'}};
        for (var r in result.regions) {
           regionMap[r['code']!] = r;
        }
        // If current selected is not in the list, keep it
        if (_selectedRegionCode != 'all' && !regionMap.containsKey(_selectedRegionCode)) {
           regionMap[_selectedRegionCode] = {'code': _selectedRegionCode, 'name': '当前地区'};
        }
        _regionOptions = regionMap.values.toList();
        
        // Merge dynamic forums
        final Map<String, Map<String, String>> forumMap = {'all': {'key': 'all', 'name': '全部板块'}};
        for (var f in result.forums) {
           forumMap[f['key']!] = f;
        }
        if (_selectedForumKey != 'all' && !forumMap.containsKey(_selectedForumKey)) {
           forumMap[_selectedForumKey] = {'key': _selectedForumKey, 'name': '当前板块'};
        }
        _forumOptions = forumMap.values.toList();
        
        _isLoading = false;
      });
    }
  }

  Future<void> _vote(String id, String type) async {
    final success = await _contributionService.voteContribution(id, widget.username, type);
    if (success) {
      _fetchData(); // Refresh to show new counts
    }
  }

  @override
  Widget build(BuildContext context) {
    final currentTheme = _themeService.currentTheme;

    return Scaffold(
      backgroundColor: Colors.grey[100],
      floatingActionButton: Padding(
        padding: const EdgeInsets.only(bottom: 95.0),
        child: FloatingActionButton.extended(
          onPressed: () async {
            final selectedRegion = _regionOptions.firstWhere(
              (opt) => opt['code'] == _selectedRegionCode,
              orElse: () => {'code': _selectedRegionCode, 'name': '全国/所有地区'},
            );
            final result = await Navigator.push(
              context,
              MaterialPageRoute(
                builder: (_) => CreateContributionPage(
                  username: widget.username,
                  initialRegionCode: _selectedRegionCode == 'all' ? null : _selectedRegionCode,
                  initialRegionName: _selectedRegionCode == 'all' ? null : selectedRegion['name'],
                  initialForumKey: _selectedForumKey == 'all' ? 'share' : _selectedForumKey,
                ),
              ),
            );
            if (result == true) _fetchData();
          },
          label: const Text("发布店铺话题"),
          icon: const Icon(Icons.add_comment),
          backgroundColor: currentTheme.gradientColors.first,
        ),
      ),
      body: Column(
        children: [
          // Header
          Container(
            padding: EdgeInsets.only(
              top: MediaQuery.of(context).padding.top + 10,
              bottom: 20,
              left: 16,
              right: 16
            ),
            decoration: BoxDecoration(
              gradient: LinearGradient(colors: currentTheme.gradientColors),
              borderRadius: const BorderRadius.only(bottomLeft: Radius.circular(24), bottomRight: Radius.circular(24)),
            ),
            child: Column(
              children: [
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    const SizedBox(width: 40),
                    Text(
                      '店铺社区 (Shop Community)',
                      style: TextStyle(color: currentTheme.textColor, fontSize: 20, fontWeight: FontWeight.bold),
                    ),
                    IconButton(
                      icon: Icon(Icons.refresh, color: currentTheme.textColor),
                      onPressed: _fetchData,
                    ),
                  ],
                ),
                const SizedBox(height: 10),
                // Filters
                Row(
                  children: [
                    Expanded(
                      child: Container(
                        padding: const EdgeInsets.symmetric(horizontal: 12),
                        decoration: BoxDecoration(
                          color: Colors.white.withOpacity(0.2),
                          borderRadius: BorderRadius.circular(12),
                        ),
                        child: DropdownButtonHideUnderline(
                          child: DropdownButton<String>(
                            value: _selectedRegionCode,
                            isExpanded: true,
                            dropdownColor: currentTheme.gradientColors.first,
                            icon: Icon(Icons.arrow_drop_down, color: currentTheme.textColor),
                            style: TextStyle(color: currentTheme.textColor, fontWeight: FontWeight.bold),
                            items: _regionOptions.map((opt) {
                              return DropdownMenuItem<String>(
                                value: opt['code'],
                                child: Text(opt['name']!),
                              );
                            }).toList(),
                            onChanged: (val) {
                              if (val != null) {
                                setState(() => _selectedRegionCode = val);
                                _fetchData();
                              }
                            },
                          ),
                        ),
                      ),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: Container(
                        padding: const EdgeInsets.symmetric(horizontal: 12),
                        decoration: BoxDecoration(
                          color: Colors.white.withOpacity(0.2),
                          borderRadius: BorderRadius.circular(12),
                        ),
                        child: DropdownButtonHideUnderline(
                          child: DropdownButton<String>(
                            value: _selectedForumKey,
                            isExpanded: true,
                            dropdownColor: currentTheme.gradientColors.first,
                            icon: Icon(Icons.arrow_drop_down, color: currentTheme.textColor),
                            style: TextStyle(color: currentTheme.textColor, fontWeight: FontWeight.bold),
                            items: _forumOptions.map((opt) {
                              return DropdownMenuItem<String>(
                                value: opt['key'],
                                child: Text(opt['name']!),
                              );
                            }).toList(),
                            onChanged: (val) {
                              if (val != null) {
                                setState(() => _selectedForumKey = val);
                                _fetchData();
                              }
                            },
                          ),
                        ),
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),
          
          // Arbitration Entry Card - Removed since it is now in navigation
          
          Expanded(
            child: _isLoading 
                ? const Center(child: CircularProgressIndicator())
                : _contributions.isEmpty 
                    ? const Center(child: Text("当前区域暂无店铺社区话题"))
                    : ListView.builder(
                        padding: const EdgeInsets.only(bottom: 100, top: 10),
                        itemCount: _contributions.length,
                        itemBuilder: (context, index) {
                          return _buildContributionCard(_contributions[index]);
                        },
                      ),
          ),
        ],
      ),
    );
  }

  Widget _getIconForType(ContributionType type) {
    switch (type) {
      case ContributionType.storeLayout:
      case ContributionType.newShop:
        return const Icon(Icons.store, color: Colors.blue);
      case ContributionType.obstacleStatus:
      case ContributionType.newObstacle:
        return const Icon(Icons.warning, color: Colors.orange);
      default:
        return const Icon(Icons.help_outline, color: Colors.grey);
    }
  }

  String _getLabelForType(ContributionType type) {
    switch (type) {
      case ContributionType.storeLayout: return "店铺结构";
      case ContributionType.obstacleStatus: return "状态更新";
      case ContributionType.newObstacle: return "新障碍物";
      case ContributionType.newShop: return "新店铺";
      default: return "未知类型";
    }
  }

  Widget _buildContributionCard(ContributionModel item) {
    final isUpvoted = item.upvotes.contains(widget.username);
    final isDownvoted = item.downvotes.contains(widget.username);
    
    // Calculate remaining days based on auditPeriod
    final age = DateTime.now().difference(item.createdAt);
    final remainingSeconds = item.auditPeriod - age.inSeconds;
    final remainingDays = (remainingSeconds / 86400).toStringAsFixed(1);

    return GestureDetector(
      onTap: () {
        Navigator.push(
          context, 
          MaterialPageRoute(builder: (_) => ContributionDetailPage(
            contribution: item, 
            username: widget.username,
            onRefresh: _fetchData,
          ))
        ).then((_) => _fetchData()); // Refresh when returning from detail page
      },
      child: Card(
        margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Header
            Row(
              children: [
                _getIconForType(item.type),
                const SizedBox(width: 8),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(item.markerTitle, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
                      Text(
                        [
                          item.forumName ?? item.forumKey ?? '默认板块',
                          item.regionName ?? item.regionCode ?? '未分区',
                        ].join(' · '),
                        style: TextStyle(color: Colors.grey[700], fontSize: 12, fontWeight: FontWeight.w600),
                      ),
                      Text(
                        "${item.userNickname} (信任分: ${item.userTrustScore}) • ${DateFormat('MM-dd HH:mm').format(item.createdAt)} • 剩余公示: $remainingDays天",
                        style: TextStyle(color: Colors.grey[600], fontSize: 12),
                      ),
                    ],
                  ),
                ),
                if (item.userId == widget.username)
                  IconButton(
                    icon: const Icon(Icons.delete_outline, color: Colors.red),
                    onPressed: () => _deleteContribution(item),
                  ),
                Chip(
                  label: Text(_getLabelForType(item.type)),
                  backgroundColor: Colors.blue[50],
                  labelStyle: const TextStyle(color: Colors.blue, fontSize: 12),
                ),
                const SizedBox(width: 6),
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                  decoration: BoxDecoration(
                    color: _reviewColor(item.reviewStatus).withOpacity(0.12),
                    borderRadius: BorderRadius.circular(999),
                  ),
                  child: Text(
                    _reviewLabel(item.reviewStatus),
                    style: TextStyle(
                      color: _reviewColor(item.reviewStatus),
                      fontSize: 12,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                ),
              ],
            ),
            const Divider(),
            
            // Content
            Text(item.content, style: const TextStyle(fontSize: 15)),
            const SizedBox(height: 10),
            
            if ((item.type == ContributionType.storeLayout || item.type == ContributionType.newShop) && item.zones != null)
              Wrap(
                spacing: 4,
                children: item.zones!.map((z) => Chip(
                  label: Text(z, style: const TextStyle(fontSize: 10)),
                  visualDensity: VisualDensity.compact,
                )).toList(),
              ),
              
            if (item.type == ContributionType.obstacleStatus && item.proposedStatus != null)
               Container(
                 margin: const EdgeInsets.symmetric(vertical: 8),
                 padding: const EdgeInsets.all(8),
                 decoration: BoxDecoration(color: Colors.orange[50], borderRadius: BorderRadius.circular(8)),
                 child: Row(
                   children: [
                     const Icon(Icons.info_outline, color: Colors.orange, size: 16),
                     const SizedBox(width: 8),
                     Text("判定为: ${item.proposedStatus == ObstacleStatus.active ? '仍然存在' : '已移除'}",
                        style: const TextStyle(color: Colors.orange, fontWeight: FontWeight.bold)),
                   ],
                 ),
               ),
            
            if (item.imageUrl != null)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 8),
                child: ClipRRect(
                  borderRadius: BorderRadius.circular(8),
                  child: Image.network(
                    item.imageUrl!.startsWith('http') 
                        ? item.imageUrl!.replaceAll('10.0.2.2', AppConfig.serverIp).replaceAll('47.97.184.133', AppConfig.serverIp) 
                        : '${AppConfig.socketUrl}/${item.imageUrl}',
                    height: 200,
                    width: double.infinity,
                    fit: BoxFit.cover,
                    errorBuilder: (ctx, err, stack) => const SizedBox(height: 100, child: Center(child: Text("图片加载失败"))),
                  ),
                ),
              ),

            if (item.aiAnalysisResult.isNotEmpty) ...[
              const SizedBox(height: 10),
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: Colors.indigo[50],
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(color: Colors.indigo.shade100),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text("审核摘要", style: TextStyle(fontSize: 12, fontWeight: FontWeight.bold, color: Colors.indigo)),
                    const SizedBox(height: 6),
                    Text(item.aiAnalysisResult, style: const TextStyle(fontSize: 13, height: 1.5)),
                  ],
                ),
              ),
            ],

            if (item.blindGuidanceSummary.isNotEmpty) ...[
              const SizedBox(height: 10),
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: Colors.green[50],
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(color: Colors.green.shade100),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text("导盲摘要", style: TextStyle(fontSize: 12, fontWeight: FontWeight.bold, color: Colors.green)),
                    const SizedBox(height: 6),
                    Text(item.blindGuidanceSummary, style: const TextStyle(fontSize: 13, height: 1.5)),
                  ],
                ),
              ),
            ],

            // Comments Preview (Last 2)
            if (item.comments.isNotEmpty)
              Container(
                margin: const EdgeInsets.only(top: 8),
                padding: const EdgeInsets.all(8),
                decoration: BoxDecoration(
                  color: Colors.grey[100],
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    ...item.comments.take(2).map((c) => Padding(
                      padding: const EdgeInsets.only(bottom: 4),
                      child: RichText(
                        text: TextSpan(
                          style: const TextStyle(color: Colors.black87, fontSize: 13),
                          children: [
                            TextSpan(text: "${c.userNickname}: ", style: const TextStyle(fontWeight: FontWeight.bold)),
                            TextSpan(text: c.content),
                          ],
                        ),
                      ),
                    )),
                    if (item.comments.length > 2)
                      Text("查看全部 ${item.comments.length} 条评论...", style: const TextStyle(color: Colors.grey, fontSize: 12)),
                  ],
                ),
              ),

            // Actions
            const SizedBox(height: 10),
            Row(
              mainAxisAlignment: MainAxisAlignment.end,
              children: [
                TextButton.icon(
                  icon: const Icon(Icons.comment_outlined, color: Colors.grey),
                  label: Text("${item.comments.length}"),
                  onPressed: () => _showCommentDialog(item),
                ),
                TextButton.icon(
                  icon: Icon(isUpvoted ? Icons.thumb_up : Icons.thumb_up_outlined, color: isUpvoted ? Colors.green : Colors.grey),
                  label: Text("${item.upvotes.length} (Score: ${item.score > 0 ? '+' : ''}${item.score.toStringAsFixed(1)})"),
                  onPressed: () => _vote(item.id, 'up'),
                ),
                TextButton.icon(
                  icon: Icon(isDownvoted ? Icons.thumb_down : Icons.thumb_down_outlined, color: isDownvoted ? Colors.red : Colors.grey),
                  label: Text("${item.downvotes.length}"),
                  onPressed: () => _vote(item.id, 'down'),
                ),
              ],
            ),
          ],
        ),
        ),
      ),
    );
  }

  void _deleteContribution(ContributionModel item) {
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text("确认删除"),
        content: const Text("确定要删除这条发布内容吗？"),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text("取消")),
          TextButton(
            onPressed: () async {
              Navigator.pop(ctx);
              final success = await _contributionService.deleteContribution(item.id, widget.username);
              if (success) {
                if (mounted) ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("删除成功")));
                _fetchData();
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

  void _showCommentDialog(ContributionModel item) {
    final TextEditingController _commentController = TextEditingController();
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
      builder: (ctx) => Padding(
        padding: EdgeInsets.only(bottom: MediaQuery.of(ctx).viewInsets.bottom),
        child: Container(
          padding: const EdgeInsets.all(16),
          height: 500,
          child: Column(
            children: [
              Text("评论 (${item.comments.length})", style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 18)),
              const SizedBox(height: 10),
              Expanded(
                child: ListView.builder(
                  itemCount: item.comments.length,
                  itemBuilder: (ctx, idx) {
                    final c = item.comments[idx];
                    return ListTile(
                      contentPadding: EdgeInsets.zero,
                      title: Text(c.userNickname, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 14)),
                      subtitle: Text(c.content),
                      trailing: Text(DateFormat('MM-dd HH:mm').format(c.createdAt), style: const TextStyle(fontSize: 12, color: Colors.grey)),
                    );
                  },
                ),
              ),
              const Divider(),
              Row(
                children: [
                  Expanded(
                    child: TextField(
                      controller: _commentController,
                      decoration: const InputDecoration(
                        hintText: "写下你的看法...",
                        border: OutlineInputBorder(),
                        contentPadding: EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                      ),
                    ),
                  ),
                  const SizedBox(width: 8),
                  ElevatedButton(
                    onPressed: () async {
                      if (_commentController.text.isNotEmpty) {
                        final success = await _contributionService.addComment(
                          item.id, 
                          widget.username, 
                          widget.username, // Using username as nickname for now
                          _commentController.text
                        );
                        if (success) {
                          Navigator.pop(ctx);
                          _fetchData();
                        }
                      }
                    },
                    child: const Text("发送"),
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}
