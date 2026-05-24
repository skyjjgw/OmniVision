import 'package:shared_preferences/shared_preferences.dart';

class AppConfig {
  static const String _defaultServerIp = '127.0.0.1';
  static String serverIp = _defaultServerIp;

  // 动态加载配置
  static Future<void> loadConfig() async {
    final prefs = await SharedPreferences.getInstance();
    serverIp = prefs.getString('custom_server_ip') ?? _defaultServerIp;
  }
  
  static String get socketUrl => 'http://$serverIp:6000';
  static String get apiUrl => 'http://$serverIp:8000/api';
  
  static Map<String, dynamic> get iceConfiguration => {
    'iceServers': [
      {'urls': 'stun:$serverIp:3478'},
      {
        'urls': 'turn:$serverIp:3478',
        'username': 'turn_user',
        'credential': 'turn_password'
      }
    ],
    'sdpSemantics': 'unified-plan',
  };
}
