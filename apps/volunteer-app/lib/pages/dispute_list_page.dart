import 'package:flutter/material.dart';
import '../models/dispute_model.dart';
import '../services/dispute_service.dart';
import '../config/app_config.dart'; // For currentUser ID (assuming we have a way to get it, or pass it)

// Assuming we have a global way to get current User ID. 
// If not, we'll need to pass it or store it in SharedPreferences.
// For now, let's assume a hardcoded or stored ID is available via a helper or just ask user to implement.
// Actually, AppConfig doesn't usually store user session. 
// Let's look at `login_page.dart` or `main.dart` to see how user is stored.
// Wait, I can't read all files. I'll assume I need to fetch it from SharedPreferences inside the widget.

import 'package:shared_preferences/shared_preferences.dart';

import 'create_contribution_page.dart';

import '../services/theme_service.dart';

class DisputeListPage extends StatefulWidget {
  const DisputeListPage({Key? key}) : super(key: key);

  @override
  _DisputeListPageState createState() => _DisputeListPageState();
}

class _DisputeListPageState extends State<DisputeListPage> {
  List<Dispute> _disputes = [];
  bool _isLoading = true;
  String? _currentUserId;
  // Removed manual definition of cities so that the dropdown can load dynamically
  // We'll populate _regionOptions from the API call or rely on AppConfig
  String _selectedRegionCode = 'all';
  List<Map<String, String>> _regionOptions = [
    {'code': 'all', 'name': '全国/所有地区'},
  ];

  void _showSingleSnackBar(String message) {
    final messenger = ScaffoldMessenger.of(context);
    messenger.hideCurrentSnackBar();
    messenger.showSnackBar(SnackBar(content: Text(message)));
  }

  final ThemeService _themeService = ThemeService();

  @override
  void initState() {
    super.initState();
    _loadUser();
    _loadDisputes();
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

  Future<void> _loadUser() async {
    final prefs = await SharedPreferences.getInstance();
    final storedUserId = prefs.getString('user_id');
    final fallbackUsername = prefs.getString('username');
    final resolvedUserId = (storedUserId != null && storedUserId.isNotEmpty)
        ? storedUserId
        : ((fallbackUsername != null && fallbackUsername.isNotEmpty) ? fallbackUsername : null);

    if (!mounted) return;
    setState(() {
      _currentUserId = resolvedUserId;
    });
  }

  Future<void> _loadDisputes() async {
    setState(() => _isLoading = true);
    final result = await DisputeService.getDisputesWithMetadata(
      targetType: 'obstacle',
      regionCode: _selectedRegionCode == 'all' ? null : _selectedRegionCode,
    );
    setState(() {
      _disputes = result.disputes;
      
      // Merge dynamic regions
      final Map<String, Map<String, String>> regionMap = {'all': {'code': 'all', 'name': '全国/所有地区'}};
      for (var r in result.regions) {
         regionMap[r['code']!] = r;
      }
      if (_selectedRegionCode != 'all' && !regionMap.containsKey(_selectedRegionCode)) {
         regionMap[_selectedRegionCode] = {'code': _selectedRegionCode, 'name': '当前地区'};
      }
      _regionOptions = regionMap.values.toList();
      
      _isLoading = false;
    });
  }

  Future<void> _vote(String disputeId, String option) async {
    if (_currentUserId == null) {
      _showSingleSnackBar('请先登录');
      return;
    }

    final success = await DisputeService.voteDispute(disputeId, _currentUserId!, option);
    if (success) {
      if (!mounted) return;
      setState(() {
        _disputes.removeWhere((dispute) => dispute.id == disputeId);
      });
      _showSingleSnackBar('投票成功');
    } else {
      _showSingleSnackBar('投票失败');
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
            if (_currentUserId == null) {
              _showSingleSnackBar('请先登录');
              return;
            }
            final selectedRegion = _regionOptions.firstWhere(
              (opt) => opt['code'] == _selectedRegionCode,
              orElse: () => {'code': _selectedRegionCode, 'name': '全国/所有地区'},
            );
            final result = await Navigator.push(
              context,
              MaterialPageRoute(
                builder: (_) => CreateContributionPage(
                  username: _currentUserId!,
                  isObstacleMode: true,
                  initialRegionCode: _selectedRegionCode == 'all' ? null : _selectedRegionCode,
                  initialRegionName: _selectedRegionCode == 'all' ? null : selectedRegion['name'],
                  initialForumKey: 'obstacle',
                ),
              ),
            );
            if (result == true) {
              _loadDisputes();
            }
          },
          label: const Text("发布障碍物反馈"),
          icon: const Icon(Icons.add),
          backgroundColor: currentTheme.gradientColors.first,
        ),
      ),
      body: Column(
        children: [
          // Custom Header
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
                    const SizedBox(width: 48), // Balance for back button if needed, or just space
                    Text(
                      '障碍物争议',
                      style: TextStyle(color: currentTheme.textColor, fontSize: 20, fontWeight: FontWeight.bold),
                    ),
                    const SizedBox(width: 48), // Right spacing
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
                                _loadDisputes();
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
          
          Expanded(
            child: _isLoading
                ? const Center(child: CircularProgressIndicator())
                : _disputes.isEmpty
                    ? const Center(child: Text('当前区域暂无待处理的障碍物争议'))
                    : ListView.builder(
                        padding: const EdgeInsets.only(bottom: 100, top: 10),
                        itemCount: _disputes.length,
                        itemBuilder: (context, index) {
                          final dispute = _disputes[index];
                          return _buildDisputeCard(dispute);
                        },
                      ),
          ),
        ],
      ),
    );
  }

  Future<void> _editDispute(Dispute dispute) async {
    // Navigate to a dedicated edit page or show a modal dialog
    // For now, we will show a modal dialog to modify its status or votes
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
      builder: (ctx) {
        return Padding(
          padding: EdgeInsets.only(bottom: MediaQuery.of(ctx).viewInsets.bottom + 24, left: 16, right: 16, top: 24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text("修改障碍物争议", style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
              const SizedBox(height: 16),
              Text("争议ID: ${dispute.id}", style: const TextStyle(color: Colors.grey)),
              const SizedBox(height: 24),
              ListTile(
                leading: const Icon(Icons.refresh, color: Colors.blue),
                title: const Text("重置投票数据"),
                subtitle: const Text("清空该争议的所有投票记录"),
                onTap: () {
                  Navigator.pop(ctx);
                  _showSingleSnackBar('重置投票功能正在开发中');
                },
              ),
              ListTile(
                leading: const Icon(Icons.delete_outline, color: Colors.red),
                title: const Text("强制移除该争议"),
                subtitle: const Text("直接关闭并删除此争议"),
                onTap: () {
                  Navigator.pop(ctx);
                  _showSingleSnackBar('强制移除功能正在开发中');
                },
              ),
            ],
          ),
        );
      },
    );
  }

  Widget _buildDisputeCard(Dispute dispute) {
    // Data A is Existing (Left), Data B is New (Right)
    final dataA = dispute.dataA ?? {};
    final dataB = dispute.dataB ?? {};

    // Get images
    String? imgA;
    if (dataA['imageUrls'] != null && dataA['imageUrls'] is List && (dataA['imageUrls'] as List).isNotEmpty) {
        imgA = (dataA['imageUrls'][0] as String).replaceAll('10.0.2.2', AppConfig.serverIp).replaceAll('47.97.184.133', AppConfig.serverIp);
    }
    
    String? imgB;
    if (dataB['imageUrl'] != null && dataB['imageUrl'] is String && (dataB['imageUrl'] as String).isNotEmpty) {
        imgB = (dataB['imageUrl'] as String).replaceAll('10.0.2.2', AppConfig.serverIp).replaceAll('47.97.184.133', AppConfig.serverIp);
    } else if (dataB['imageUrls'] != null && dataB['imageUrls'] is List && (dataB['imageUrls'] as List).isNotEmpty) {
        imgB = (dataB['imageUrls'][0] as String).replaceAll('10.0.2.2', AppConfig.serverIp).replaceAll('47.97.184.133', AppConfig.serverIp);
    }

    // Votes
    final votesMerge = (dispute.votes['merge'] as List?)?.length ?? 0;
    final votesSeparate = (dispute.votes['separate'] as List?)?.length ?? 0;
    final votesReject = (dispute.votes['reject'] as List?)?.length ?? 0;

    return Card(
      margin: const EdgeInsets.all(12),
      elevation: 4,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      child: Column(
        children: [
          Container(
            padding: const EdgeInsets.all(12),
            color: Colors.amber.withOpacity(0.1),
            child: Row(
              children: [
                const Icon(Icons.warning_amber_rounded, color: Colors.amber),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    '争议话题：障碍位置冲突 - ${dispute.regionName ?? "未知地区"}',
                    style: const TextStyle(fontWeight: FontWeight.bold),
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                Text(dispute.status, style: const TextStyle(color: Colors.grey)),
                IconButton(
                  icon: const Icon(Icons.edit, size: 20, color: Colors.blueGrey),
                  onPressed: () => _editDispute(dispute),
                  constraints: const BoxConstraints(),
                  padding: const EdgeInsets.only(left: 8),
                )
              ],
            ),
          ),
          const SizedBox(height: 8),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 8),
            child: Row(
              children: [
                Expanded(child: _buildCandidateView('现有障碍物 (A)', dataA, imgA)),
                const VerticalDivider(width: 1),
                Expanded(child: _buildCandidateView('新上报障碍 (B)', dataB, imgB)),
              ],
            ),
          ),
          const Divider(),
          Padding(
            padding: const EdgeInsets.all(12),
            child: Column(
              children: [
                const Text('请投票裁决：', style: TextStyle(fontWeight: FontWeight.bold)),
                const SizedBox(height: 12),
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceEvenly,
                  children: [
                    _buildVoteButton(
                      '合并 (是同一个)', 
                      'merge', 
                      votesMerge, 
                      Colors.blue,
                      () => _vote(dispute.id, 'merge')
                    ),
                    _buildVoteButton(
                      '独立 (是两个)', 
                      'separate', 
                      votesSeparate, 
                      Colors.green,
                      () => _vote(dispute.id, 'separate')
                    ),
                  ],
                ),
                const SizedBox(height: 8),
                TextButton.icon(
                  onPressed: () => _vote(dispute.id, 'reject'),
                  icon: const Icon(Icons.close, color: Colors.red),
                  label: Text('拒绝 B (无效/误报) - $votesReject 票', style: const TextStyle(color: Colors.red)),
                )
              ],
            ),
          )
        ],
      ),
    );
  }

  Widget _buildCandidateView(String title, Map<String, dynamic> data, String? imgUrl) {
    return Column(
      children: [
        Text(title, style: const TextStyle(fontWeight: FontWeight.bold, color: Colors.grey)),
        const SizedBox(height: 8),
        Container(
          height: 100,
          width: double.infinity,
          decoration: BoxDecoration(
            color: Colors.grey[200],
            borderRadius: BorderRadius.circular(8),
            image: imgUrl != null 
                ? DecorationImage(image: NetworkImage(imgUrl), fit: BoxFit.cover)
                : null,
          ),
          child: imgUrl == null ? const Icon(Icons.image_not_supported) : null,
        ),
        const SizedBox(height: 8),
        Text(data['title'] ?? data['markerTitle'] ?? '无标题', maxLines: 1, overflow: TextOverflow.ellipsis),
        Text(data['description'] ?? data['content'] ?? '无描述', maxLines: 2, overflow: TextOverflow.ellipsis, style: const TextStyle(fontSize: 12)),
      ],
    );
  }

  Widget _buildVoteButton(String label, String value, int count, Color color, VoidCallback onTap) {
    return InkWell(
      onTap: onTap,
      child: Column(
        children: [
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
            decoration: BoxDecoration(
              color: color.withOpacity(0.1),
              borderRadius: BorderRadius.circular(20),
              border: Border.all(color: color),
            ),
            child: Text(label, style: TextStyle(color: color, fontWeight: FontWeight.bold)),
          ),
          const SizedBox(height: 4),
          Text('$count 票', style: TextStyle(color: color, fontSize: 12)),
        ],
      ),
    );
  }
}
