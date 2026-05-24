import 'package:flutter/material.dart';
import 'lobby_view.dart';
import '../map/pages/volunteer_map_page.dart';
import 'community_page.dart';
import 'dispute_list_page.dart';
import 'profile_page.dart';
import '../services/theme_service.dart';

class MainScreen extends StatefulWidget {
  final String username;
  const MainScreen({super.key, required this.username});
  @override
  State<MainScreen> createState() => _MainScreenState();
}

class _MainScreenState extends State<MainScreen> {
  int _currentIndex = 0;
  late final List<Widget> _pages;
  final ThemeService _themeService = ThemeService();

  @override
  void initState() {
    super.initState();
    _pages = [
      LobbyView(username: widget.username),
      const VolunteerMapPage(),
      const DisputeListPage(), // 新增仲裁页面
      CommunityPage(username: widget.username),
      ProfilePage(username: widget.username),
    ];
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

  @override
  Widget build(BuildContext context) {
    final currentTheme = _themeService.currentTheme;
    // 获取当前主题的主色调，用于选中状态
    final primaryColor = currentTheme.gradientColors.first;

    return Scaffold(
      extendBody: true,
      body: IndexedStack(
        index: _currentIndex,
        children: _pages,
      ),
      bottomNavigationBar: Container(
        margin: const EdgeInsets.only(left: 20, right: 20, bottom: 20),
        decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.circular(24),
          boxShadow: [
            BoxShadow(
              color: Colors.black.withOpacity(0.1),
              blurRadius: 20,
              offset: const Offset(0, 10),
            )
          ],
        ),
        child: ClipRRect(
          borderRadius: BorderRadius.circular(24),
          child: NavigationBar(
            backgroundColor: Colors.white,
            surfaceTintColor: Colors.white,
            indicatorColor: primaryColor.withOpacity(0.1),
            elevation: 0,
            selectedIndex: _currentIndex,
            labelBehavior: NavigationDestinationLabelBehavior.alwaysShow,
            height: 75,
            onDestinationSelected: (index) {
              setState(() {
                _currentIndex = index;
              });
            },
            destinations: [
              NavigationDestination(
                icon: const Icon(Icons.home_outlined),
                selectedIcon: Container(
                  padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 8),
                  decoration: BoxDecoration(
                    gradient: LinearGradient(
                      colors: [
                        primaryColor.withOpacity(0.8),
                        primaryColor,
                      ],
                      begin: Alignment.topLeft,
                      end: Alignment.bottomRight,
                    ),
                    borderRadius: BorderRadius.circular(20),
                    boxShadow: [
                      BoxShadow(
                        color: primaryColor.withOpacity(0.3),
                        blurRadius: 8,
                        offset: const Offset(0, 4),
                      )
                    ],
                  ),
                  child: const Icon(Icons.home, color: Colors.white),
                ),
                label: '接单',
              ),
              NavigationDestination(
                icon: const Icon(Icons.map_outlined),
                selectedIcon: Icon(Icons.map, color: primaryColor),
                label: '地图',
              ),
              NavigationDestination(
                icon: const Icon(Icons.gavel_outlined),
                selectedIcon: Icon(Icons.gavel, color: primaryColor),
                label: '障碍争议',
              ),
              NavigationDestination(
                icon: const Icon(Icons.forum_outlined),
                selectedIcon: Icon(Icons.forum, color: primaryColor),
                label: '店铺社区',
              ),
              NavigationDestination(
                icon: const Icon(Icons.person_outline),
                selectedIcon: Icon(Icons.person, color: primaryColor),
                label: '我的',
              ),
            ],
          ),
        ),
      ),
    );
  }
}
