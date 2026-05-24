import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import '../models/contribution_model.dart';
import '../services/contribution_service.dart';
import '../services/theme_service.dart';
import '../config/app_config.dart';
import 'contribution_detail_page.dart';

class MyContributionsPage extends StatefulWidget {
  final String username;
  const MyContributionsPage({super.key, required this.username});

  @override
  State<MyContributionsPage> createState() => _MyContributionsPageState();
}

class _MyContributionsPageState extends State<MyContributionsPage> {
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

  @override
  void initState() {
    super.initState();
    _fetchData();
  }

  Future<void> _fetchData() async {
    setState(() => _isLoading = true);
    final result = await _contributionService.fetchContributionsWithMetadata();
    if (mounted) {
      setState(() {
        // Filter for my posts only
        _contributions = result.contributions.where((c) => c.userId == widget.username).toList();
        _isLoading = false;
      });
    }
  }

  Future<void> _deleteContribution(ContributionModel item) async {
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

  @override
  Widget build(BuildContext context) {
    final currentTheme = _themeService.currentTheme;

    return Scaffold(
      appBar: AppBar(
        title: const Text("我的发布"),
        backgroundColor: currentTheme.gradientColors.first,
        foregroundColor: Colors.white,
      ),
      body: _isLoading 
          ? const Center(child: CircularProgressIndicator())
          : _contributions.isEmpty 
              ? const Center(child: Text("暂无发布内容"))
              : ListView.builder(
                  padding: const EdgeInsets.all(16),
                  itemCount: _contributions.length,
                  itemBuilder: (context, index) {
                    return _buildCard(_contributions[index]);
                  },
                ),
    );
  }

  Widget _buildCard(ContributionModel item) {
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
        margin: const EdgeInsets.only(bottom: 16),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Header
            Row(
              children: [
                Icon(
                  item.type == ContributionType.storeLayout ? Icons.store : Icons.warning,
                  color: Colors.blue,
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(item.markerTitle, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
                      Text(
                        "${DateFormat('MM-dd HH:mm').format(item.createdAt)} • 剩余公示: $remainingDays天",
                        style: TextStyle(color: Colors.grey[600], fontSize: 12),
                      ),
                    ],
                  ),
                ),
                IconButton(
                  icon: const Icon(Icons.delete_outline, color: Colors.red),
                  onPressed: () => _deleteContribution(item),
                ),
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
            
            Text(item.content, style: const TextStyle(fontSize: 15)),
            const SizedBox(height: 10),
            
            if (item.imageUrl != null)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 8),
                child: ClipRRect(
                  borderRadius: BorderRadius.circular(8),
                  child: Image.network(
                    item.imageUrl!.startsWith('http') 
                        ? item.imageUrl! 
                        : '${AppConfig.socketUrl}/${item.imageUrl}',
                    height: 150,
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

             // Status Summary
             Row(
               children: [
                 const Icon(Icons.thumb_up, size: 16, color: Colors.green),
                 const SizedBox(width: 4),
                 Text("${item.upvotes.length}"),
                 const SizedBox(width: 16),
                 const Icon(Icons.thumb_down, size: 16, color: Colors.red),
                 const SizedBox(width: 4),
                 Text("${item.downvotes.length}"),
                 const SizedBox(width: 16),
                 const Icon(Icons.comment, size: 16, color: Colors.grey),
                 const SizedBox(width: 4),
                 Text("${item.comments.length}"),
               ],
             )
          ],
        ),
      ),
    ),
    );
  }
}
