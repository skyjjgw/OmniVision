import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

class DisputeJudgementSettingsPage extends StatefulWidget {
  const DisputeJudgementSettingsPage({super.key});

  @override
  State<DisputeJudgementSettingsPage> createState() =>
      _DisputeJudgementSettingsPageState();
}

class _DisputeJudgementSettingsPageState
    extends State<DisputeJudgementSettingsPage> {
  static const String _keyRuleTitle = 'dispute_rule_title';
  static const String _keyRuleDescription = 'dispute_rule_description';
  static const String _keyMergeThreshold = 'dispute_merge_threshold';
  static const String _keyAutoCloseEnabled = 'dispute_auto_close_enabled';
  static const String _keyAutoCloseMinutes = 'dispute_auto_close_minutes';

  final TextEditingController _titleController = TextEditingController();
  final TextEditingController _descriptionController = TextEditingController();
  final TextEditingController _mergeThresholdController =
      TextEditingController();
  final TextEditingController _autoCloseMinutesController =
      TextEditingController();

  bool _autoCloseEnabled = false;
  bool _isLoading = true;
  bool _isSaving = false;

  @override
  void initState() {
    super.initState();
    _loadSettings();
  }

  @override
  void dispose() {
    _titleController.dispose();
    _descriptionController.dispose();
    _mergeThresholdController.dispose();
    _autoCloseMinutesController.dispose();
    super.dispose();
  }

  Future<void> _loadSettings() async {
    final prefs = await SharedPreferences.getInstance();
    _titleController.text = prefs.getString(_keyRuleTitle) ?? '位置冲突';
    _descriptionController.text =
        prefs.getString(_keyRuleDescription) ?? '当两个标注距离过近且描述矛盾时，进入社区仲裁。';
    _mergeThresholdController.text =
        (prefs.getInt(_keyMergeThreshold) ?? 3).toString();
    _autoCloseEnabled = prefs.getBool(_keyAutoCloseEnabled) ?? false;
    _autoCloseMinutesController.text =
        (prefs.getInt(_keyAutoCloseMinutes) ?? 60).toString();

    if (!mounted) return;
    setState(() {
      _isLoading = false;
    });
  }

  int? _parsePositiveInt(String value) {
    final parsed = int.tryParse(value.trim());
    if (parsed == null || parsed <= 0) return null;
    return parsed;
  }

  Future<void> _saveSettings() async {
    final mergeThreshold = _parsePositiveInt(_mergeThresholdController.text);
    final autoCloseMinutes = _parsePositiveInt(_autoCloseMinutesController.text);

    if (mergeThreshold == null) {
      _showTip('合并判定票数必须是大于 0 的整数');
      return;
    }

    if (_autoCloseEnabled && autoCloseMinutes == null) {
      _showTip('自动关闭时长必须是大于 0 的整数');
      return;
    }

    setState(() {
      _isSaving = true;
    });

    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_keyRuleTitle, _titleController.text.trim());
    await prefs.setString(
      _keyRuleDescription,
      _descriptionController.text.trim(),
    );
    await prefs.setInt(_keyMergeThreshold, mergeThreshold);
    await prefs.setBool(_keyAutoCloseEnabled, _autoCloseEnabled);
    await prefs.setInt(_keyAutoCloseMinutes, autoCloseMinutes ?? 60);

    if (!mounted) return;
    setState(() {
      _isSaving = false;
    });
    _showTip('争议判断设置已保存');
  }

  void _showTip(String message) {
    final messenger = ScaffoldMessenger.of(context);
    messenger.hideCurrentSnackBar();
    messenger.showSnackBar(SnackBar(content: Text(message)));
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('争议判断设置'),
      ),
      body: _isLoading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.all(16),
              children: [
                const Text(
                  '你可以在这里修改争议判断说明和基础阈值。',
                  style: TextStyle(color: Colors.grey),
                ),
                const SizedBox(height: 16),
                TextField(
                  controller: _titleController,
                  decoration: const InputDecoration(
                    labelText: '争议标题',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: _descriptionController,
                  minLines: 3,
                  maxLines: 5,
                  decoration: const InputDecoration(
                    labelText: '争议判断说明',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: _mergeThresholdController,
                  keyboardType: TextInputType.number,
                  decoration: const InputDecoration(
                    labelText: '合并判定票数阈值',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 12),
                SwitchListTile(
                  contentPadding: EdgeInsets.zero,
                  title: const Text('启用自动关闭争议'),
                  value: _autoCloseEnabled,
                  onChanged: (value) {
                    setState(() {
                      _autoCloseEnabled = value;
                    });
                  },
                ),
                const SizedBox(height: 4),
                TextField(
                  controller: _autoCloseMinutesController,
                  enabled: _autoCloseEnabled,
                  keyboardType: TextInputType.number,
                  decoration: const InputDecoration(
                    labelText: '自动关闭时长（分钟）',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 20),
                ElevatedButton(
                  onPressed: _isSaving ? null : _saveSettings,
                  child: _isSaving
                      ? const SizedBox(
                          width: 18,
                          height: 18,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Text('保存修改'),
                ),
              ],
            ),
    );
  }
}
