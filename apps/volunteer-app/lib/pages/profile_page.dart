import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'dart:convert';
import 'package:shared_preferences/shared_preferences.dart';
import '../config/app_config.dart';
import '../services/theme_service.dart';
import 'edit_profile_page.dart';
import 'my_posts_page.dart';
import 'my_comments_page.dart';
import 'my_annotations_page.dart';
import 'settings_page.dart';
import 'dispute_judgement_settings_page.dart';
import 'login_page.dart';
import 'package:flutter_colorpicker/flutter_colorpicker.dart';

import 'my_contributions_page.dart';

class ProfilePage extends StatefulWidget {
  final String username;
  const ProfilePage({super.key, required this.username});

  @override
  State<ProfilePage> createState() => _ProfilePageState();
}

class _ProfilePageState extends State<ProfilePage> {
  String? _nickname;
  String? _avatarPath;
  final ThemeService _themeService = ThemeService();

  @override
  void initState() {
    super.initState();
    _fetchProfile();
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

  Future<void> _fetchProfile() async {
    try {
      final response = await http.get(Uri.parse('${AppConfig.socketUrl}/api/user/profile?email=${widget.username}'));
      if (response.statusCode == 200) {
        final data = jsonDecode(response.body);
        if (data['success']) {
          setState(() {
            _nickname = data['profile']['nickname'];
            _avatarPath = data['profile']['avatar_path'];
          });
        }
      }
    } catch (e) {
      print("Fetch profile error: $e");
    }
  }

  Future<void> _handleLogout(BuildContext context) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove('last_login_time');
    
    if (context.mounted) {
      Navigator.of(context).pushAndRemoveUntil(
        MaterialPageRoute(builder: (context) => const LoginPage()),
        (route) => false,
      );
    }
  }

  void _showThemeSelector() {
    showModalBottomSheet(
      context: context,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (context) {
        return Container(
          padding: const EdgeInsets.all(20),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Text(
                "选择主题颜色",
                style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
              ),
              const SizedBox(height: 20),
              Wrap(
                spacing: 16,
                runSpacing: 16,
                children: [
                  ..._themeService.availableThemes.map((theme) {
                    final isSelected = theme.id == _themeService.currentTheme.id;
                    return GestureDetector(
                      onTap: () {
                        _themeService.setTheme(theme.id);
                        Navigator.pop(context);
                      },
                      child: Column(
                        children: [
                          Container(
                            width: 60,
                            height: 60,
                            decoration: BoxDecoration(
                              shape: BoxShape.circle,
                              gradient: LinearGradient(
                                colors: theme.gradientColors,
                                begin: Alignment.topLeft,
                                end: Alignment.bottomRight,
                              ),
                              border: isSelected 
                                ? Border.all(color: Colors.blue, width: 3)
                                : Border.all(color: Colors.grey.shade300),
                              boxShadow: [
                                BoxShadow(
                                  color: Colors.black.withOpacity(0.1),
                                  blurRadius: 4,
                                  offset: const Offset(0, 2),
                                )
                              ],
                            ),
                            child: isSelected 
                              ? const Icon(Icons.check, color: Colors.white)
                              : null,
                          ),
                          const SizedBox(height: 8),
                          Text(theme.name, style: const TextStyle(fontSize: 12)),
                        ],
                      ),
                    );
                  }).toList(),
                  // 自定义颜色按钮
                  GestureDetector(
                    onTap: () {
                      Navigator.pop(context);
                      _showColorPicker();
                    },
                    child: Column(
                      children: [
                        Container(
                          width: 60,
                          height: 60,
                          decoration: BoxDecoration(
                            shape: BoxShape.circle,
                            color: Colors.white,
                            border: Border.all(color: Colors.grey.shade300),
                            boxShadow: [
                              BoxShadow(
                                color: Colors.black.withOpacity(0.1),
                                blurRadius: 4,
                                offset: const Offset(0, 2),
                              )
                            ],
                          ),
                          child: const Icon(Icons.add, color: Colors.black54),
                        ),
                        const SizedBox(height: 8),
                        const Text("更多颜色", style: TextStyle(fontSize: 12)),
                      ],
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 20),
            ],
          ),
        );
      },
    );
  }

  void _showColorPicker() {
    Color pickerColor = _themeService.currentTheme.gradientColors.first;

    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (context) {
        return Container(
          padding: const EdgeInsets.all(20),
          // Height adjusted for the color picker
          height: 500,
          child: Column(
            children: [
              const Text(
                "自定义颜色",
                style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
              ),
              const SizedBox(height: 20),
              Expanded(
                child: ColorPicker(
                  pickerColor: pickerColor,
                  onColorChanged: (color) {
                    pickerColor = color;
                  },
                  // Enable the circle picker (HueRing)
                  enableAlpha: false,
                  displayThumbColor: true,
                  paletteType: PaletteType.hueWheel,
                  pickerAreaHeightPercent: 0.8,
                  labelTypes: const [],
                ),
              ),
              const SizedBox(height: 20),
              ElevatedButton(
                onPressed: () {
                  _themeService.setCustomTheme(pickerColor);
                  Navigator.pop(context);
                },
                style: ElevatedButton.styleFrom(
                  backgroundColor: Colors.blue,
                  padding: const EdgeInsets.symmetric(horizontal: 40, vertical: 12),
                  shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(30)),
                ),
                child: const Text("确定", style: TextStyle(color: Colors.white, fontSize: 16)),
              ),
              const SizedBox(height: 20),
            ],
          ),
        );
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    final currentTheme = _themeService.currentTheme;
    
    return Scaffold(
      backgroundColor: Colors.grey[50],
      body: SingleChildScrollView(
        child: Column(
          children: [
            // Header Section
            Container(
              padding: const EdgeInsets.only(top: 60, bottom: 30),
              decoration: BoxDecoration(
                gradient: LinearGradient(
                  begin: Alignment.topLeft,
                  end: Alignment.bottomRight,
                  colors: currentTheme.gradientColors,
                ),
                borderRadius: const BorderRadius.vertical(bottom: Radius.circular(30)),
                boxShadow: [
                  BoxShadow(color: Colors.black.withOpacity(0.2), blurRadius: 10, offset: const Offset(0, 5))
                ],
              ),
              width: double.infinity,
              child: Column(
                children: [
                  GestureDetector(
                    onTap: () async {
                      final result = await Navigator.push(
                        context, 
                        MaterialPageRoute(builder: (_) => EditProfilePage(
                          username: widget.username,
                          initialNickname: _nickname,
                          initialAvatar: _avatarPath
                        ))
                      );
                      if (result == true) _fetchProfile();
                    },
                    child: Stack(
                      children: [
                        Container(
                          padding: const EdgeInsets.all(4),
                          decoration: BoxDecoration(
                            shape: BoxShape.circle,
                            color: Colors.white.withOpacity(0.3),
                          ),
                          child: CircleAvatar(
                            radius: 50,
                            backgroundColor: Colors.white,
                            backgroundImage: _avatarPath != null 
                                ? NetworkImage('${AppConfig.socketUrl}/$_avatarPath') 
                                : null,
                            child: _avatarPath == null ? const Icon(Icons.person, size: 50, color: Colors.grey) : null,
                          ),
                        ),
                        Positioned(
                          bottom: 0,
                          right: 0,
                          child: Container(
                            padding: const EdgeInsets.all(8),
                            decoration: BoxDecoration(
                              color: currentTheme.gradientColors.first,
                              shape: BoxShape.circle,
                              boxShadow: [BoxShadow(color: Colors.black26, blurRadius: 4)]
                            ),
                            child: Icon(Icons.edit, color: currentTheme.textColor, size: 16),
                          ),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 16),
                  Text(
                    _nickname ?? "志愿者",
                    style: TextStyle(
                      fontSize: 24, 
                      fontWeight: FontWeight.bold, 
                      color: currentTheme.textColor,
                      shadows: [Shadow(color: Colors.black26, blurRadius: 4, offset: Offset(0, 2))]
                    ),
                  ),
                  const SizedBox(height: 4),
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
                    decoration: BoxDecoration(
                      color: currentTheme.id == 'white' 
                          ? Colors.grey.withOpacity(0.1) 
                          : Colors.white.withOpacity(0.2),
                      borderRadius: BorderRadius.circular(20),
                    ),
                    child: Text(
                      widget.username,
                      style: TextStyle(fontSize: 14, color: currentTheme.textColor),
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 20),
            
            // Menu Group 1
            Container(
              margin: const EdgeInsets.symmetric(horizontal: 16),
              decoration: BoxDecoration(
                color: Colors.white,
                borderRadius: BorderRadius.circular(16),
                boxShadow: [BoxShadow(color: Colors.black12.withOpacity(0.05), blurRadius: 10)],
              ),
              child: Column(
                children: [
                  _buildMenuItem(
                    icon: Icons.article_outlined,
                    title: "我的动态",
                    onTap: () {
                      Navigator.push(context, MaterialPageRoute(builder: (_) => MyPostsPage(username: widget.username)));
                    },
                  ),
                  const Divider(height: 1, indent: 56),
                  _buildMenuItem(
                    icon: Icons.history,
                    title: "我的发布",
                    onTap: () {
                      Navigator.push(context, MaterialPageRoute(builder: (_) => MyContributionsPage(username: widget.username)));
                    },
                  ),
                  const Divider(height: 1, indent: 56),
                  _buildMenuItem(
                    icon: Icons.comment_outlined,
                    title: "我的评论",
                    onTap: () {
                      Navigator.push(context, MaterialPageRoute(builder: (_) => MyCommentsPage(username: widget.username)));
                    },
                  ),
                  const Divider(height: 1, indent: 56),
                  _buildMenuItem(
                    icon: Icons.map_outlined,
                    title: "我的标注",
                    onTap: () {
                      Navigator.push(context, MaterialPageRoute(builder: (_) => const MyAnnotationsPage()));
                    },
                  ),
                ],
              ),
            ),
            
            const SizedBox(height: 16),
            
            // Menu Group 2 (Theme & Settings)
            Container(
              margin: const EdgeInsets.symmetric(horizontal: 16),
              decoration: BoxDecoration(
                color: Colors.white,
                borderRadius: BorderRadius.circular(16),
                boxShadow: [BoxShadow(color: Colors.black12.withOpacity(0.05), blurRadius: 10)],
              ),
              child: Column(
                children: [
                  _buildMenuItem(
                    icon: Icons.color_lens_outlined,
                    title: "主题风格",
                    trailing: Container(
                      width: 20, height: 20,
                      decoration: BoxDecoration(
                        shape: BoxShape.circle,
                        gradient: LinearGradient(colors: currentTheme.gradientColors),
                        border: Border.all(color: Colors.grey.shade300),
                      ),
                    ),
                    onTap: _showThemeSelector,
                  ),
                  const Divider(height: 1, indent: 56),
                  _buildMenuItem(
                    icon: Icons.settings_outlined,
                    title: "设置",
                    onTap: () {
                      Navigator.push(context, MaterialPageRoute(builder: (_) => const SettingsPage()));
                    },
                  ),
                  const Divider(height: 1, indent: 56),
                  _buildMenuItem(
                    icon: Icons.rule_folder_outlined,
                    title: "争议判断设置",
                    onTap: () {
                      Navigator.push(
                        context,
                        MaterialPageRoute(
                          builder: (_) => const DisputeJudgementSettingsPage(),
                        ),
                      );
                    },
                  ),
                  const Divider(height: 1, indent: 56),
                  _buildMenuItem(
                    icon: Icons.info_outline,
                    title: "关于我们",
                    onTap: () {
                       showAboutDialog(context: context, applicationName: "导盲志愿者", applicationVersion: "1.0.0");
                    },
                  ),
                ],
              ),
            ),
            
            const SizedBox(height: 30),
            
            // Logout Button
            Padding(
              padding: const EdgeInsets.only(bottom: 100),
              child: TextButton.icon(
                onPressed: () => _handleLogout(context),
                icon: const Icon(Icons.exit_to_app, color: Colors.redAccent),
                label: const Text("退出登录", style: TextStyle(color: Colors.redAccent, fontSize: 16, fontWeight: FontWeight.bold)),
                style: TextButton.styleFrom(
                  padding: const EdgeInsets.symmetric(horizontal: 30, vertical: 12),
                  backgroundColor: Colors.red.withOpacity(0.05),
                  shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(30)),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildMenuItem({
    required IconData icon, 
    required String title, 
    required VoidCallback onTap,
    Widget? trailing,
  }) {
    final currentTheme = _themeService.currentTheme;
    final primaryColor = currentTheme.gradientColors.first;
    
    return ListTile(
      leading: Container(
        padding: const EdgeInsets.all(8),
        decoration: BoxDecoration(
          color: primaryColor.withOpacity(0.1),
          shape: BoxShape.circle,
        ),
        child: Icon(icon, color: primaryColor, size: 20),
      ),
      title: Text(title, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w500)),
      trailing: trailing ?? const Icon(Icons.chevron_right, color: Colors.grey),
      onTap: onTap,
      contentPadding: const EdgeInsets.symmetric(horizontal: 20, vertical: 4),
    );
  }
}
