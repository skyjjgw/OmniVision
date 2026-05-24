import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

class AppTheme {
  final String id;
  final String name;
  final List<Color> gradientColors;
  final Color textColor;
  final Brightness brightness;

  const AppTheme({
    required this.id,
    required this.name,
    required this.gradientColors,
    required this.textColor,
    required this.brightness,
  });
}

class ThemeService extends ChangeNotifier {
  static final ThemeService _instance = ThemeService._internal();
  factory ThemeService() => _instance;

  ThemeService._internal() {
    _loadTheme();
  }

  static const String _prefsKey = 'selected_theme_id';
  static const String _customColorKey = 'custom_theme_color';

  final List<AppTheme> _themes = [
    AppTheme(
      id: 'blue',
      name: '经典蓝',
      gradientColors: [Colors.blue.shade800, Colors.blue.shade500],
      textColor: Colors.white,
      brightness: Brightness.dark,
    ),
    AppTheme(
      id: 'white',
      name: '简约白',
      gradientColors: [Colors.white, Colors.grey.shade100],
      textColor: Colors.black87,
      brightness: Brightness.light,
    ),
    AppTheme(
      id: 'purple',
      name: '梦幻紫',
      gradientColors: [Colors.purple.shade800, Colors.purple.shade400],
      textColor: Colors.white,
      brightness: Brightness.dark,
    ),
    AppTheme(
      id: 'orange',
      name: '活力橙',
      gradientColors: [Colors.orange.shade800, Colors.orange.shade500],
      textColor: Colors.white,
      brightness: Brightness.dark,
    ),
    AppTheme(
      id: 'dark',
      name: '深邃夜',
      gradientColors: [Color(0xFF1A1A1A), Color(0xFF2C2C2C)],
      textColor: Colors.white,
      brightness: Brightness.dark,
    ),
  ];

  late AppTheme _currentTheme = _themes[0];

  AppTheme get currentTheme => _currentTheme;
  List<AppTheme> get availableThemes => _themes;

  Future<void> _loadTheme() async {
    final prefs = await SharedPreferences.getInstance();
    final themeId = prefs.getString(_prefsKey);
    
    if (themeId == 'custom') {
      final colorValue = prefs.getInt(_customColorKey);
      if (colorValue != null) {
        final color = Color(colorValue);
        _setCustomThemeInternal(color);
        return;
      }
    }

    if (themeId != null) {
      final theme = _themes.firstWhere(
        (t) => t.id == themeId,
        orElse: () => _themes[0],
      );
      _currentTheme = theme;
      notifyListeners();
    }
  }

  Future<void> setTheme(String themeId) async {
    final theme = _themes.firstWhere(
      (t) => t.id == themeId,
      orElse: () => _themes[0],
    );
    _currentTheme = theme;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_prefsKey, themeId);
    notifyListeners();
  }

  Future<void> setCustomTheme(Color color) async {
    _setCustomThemeInternal(color);
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_prefsKey, 'custom');
    await prefs.setInt(_customColorKey, color.value);
    notifyListeners();
  }

  void _setCustomThemeInternal(Color color) {
    // 根据颜色亮度决定文字颜色
    final brightness = ThemeData.estimateBrightnessForColor(color);
    final textColor = brightness == Brightness.dark ? Colors.white : Colors.black87;
    
    // 生成渐变色
    final color2 = HSLColor.fromColor(color).withLightness((HSLColor.fromColor(color).lightness + 0.1).clamp(0.0, 1.0)).toColor();

    _currentTheme = AppTheme(
      id: 'custom',
      name: '自定义',
      gradientColors: [color, color2],
      textColor: textColor,
      brightness: brightness,
    );
    notifyListeners();
  }
}
