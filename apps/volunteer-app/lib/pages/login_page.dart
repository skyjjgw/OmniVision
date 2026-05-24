import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'dart:convert';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:permission_handler/permission_handler.dart';
import '../config/app_config.dart';
import 'main_screen.dart';
import 'register_page.dart';
import 'reset_password_page.dart';
import 'ip_config_page.dart';

class LoginPage extends StatefulWidget {
  const LoginPage({super.key});
  @override
  State<LoginPage> createState() => _LoginPageState();
}

class _LoginPageState extends State<LoginPage> {
  final TextEditingController _emailController = TextEditingController();
  final TextEditingController _passwordController = TextEditingController();
  bool _isLoading = false;

  Future<void> _saveLoginSession(Map<String, dynamic> data) async {
    final prefs = await SharedPreferences.getInstance();
    final dynamic user = data['user'];
    final String resolvedUserId =
        (user is Map && user['email'] != null && user['email'].toString().isNotEmpty)
            ? user['email'].toString()
            : _emailController.text.trim();

    await prefs.setString('username', _emailController.text.trim());
    await prefs.setString('user_id', resolvedUserId);
    await prefs.setString('password', _passwordController.text);
    await prefs.setInt('last_login_time', DateTime.now().millisecondsSinceEpoch);
  }

  @override
  void initState() {
    super.initState();
    _checkAutoLogin();
  }

  Future<void> _checkAutoLogin() async {
    final prefs = await SharedPreferences.getInstance();
    final String? savedUsername = prefs.getString('username');
    final String? savedPassword = prefs.getString('password');
    final int? lastLoginTime = prefs.getInt('last_login_time');

    // 1. 自动填充账号密码 (如果有保存)
    if (savedUsername != null) {
      _emailController.text = savedUsername;
    }
    if (savedPassword != null) {
      _passwordController.text = savedPassword;
    }
    
    // 2. 检查30天免登录
    if (savedUsername != null && savedPassword != null && savedUsername.isNotEmpty && lastLoginTime != null) {
      final lastLogin = DateTime.fromMillisecondsSinceEpoch(lastLoginTime);
      final difference = DateTime.now().difference(lastLogin).inDays;
      
      if (difference < 30) {
        // 验证账号状态 (防止封禁用户自动登录)
        try {
           // 显示自动登录中... (可选)
           final response = await http.post(
             Uri.parse('${AppConfig.socketUrl}/api/auth/login'),
             headers: {'Content-Type': 'application/json'},
             body: jsonEncode({
               'email': savedUsername,
               'password': savedPassword,
             }),
           ).timeout(const Duration(seconds: 3));

           final data = jsonDecode(response.body);
           
           if (data['success']) {
               await _saveLoginSession(data);
               if (await _requestPermissions()) {
                 if (!mounted) return;
                 Navigator.of(context).pushReplacement(MaterialPageRoute(
                     builder: (context) => MainScreen(username: savedUsername)));
               }
           } else {
               // 登录失败 (如已被封禁)
               if (!mounted) return;
               ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(data['message'] ?? '自动登录失败')));
           }
        } catch (e) {
           // 网络错误，保留在登录页让用户手动尝试
           if (!mounted) return;
           ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('无法连接服务器，请手动登录')));
        }
      }
    }
  }

  Future<bool> _requestPermissions() async {
    Map<Permission, PermissionStatus> statuses = await [
      Permission.camera,
      Permission.microphone,
    ].request();
    return statuses[Permission.camera]!.isGranted &&
           statuses[Permission.microphone]!.isGranted;
  }

  Future<void> _handleLogin() async {
    if (_emailController.text.isEmpty || _passwordController.text.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('请输入邮箱和密码')));
      return;
    }

    setState(() => _isLoading = true);

    try {
      final response = await http.post(
        Uri.parse('${AppConfig.socketUrl}/api/auth/login'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({
          'email': _emailController.text,
          'password': _passwordController.text,
        }),
      );

      final data = jsonDecode(response.body);
      if (data['success']) {
        // 登录成功
        if (await _requestPermissions()) {
           await _saveLoginSession(data);
           
           if (!mounted) return;
           Navigator.of(context).pushReplacement(MaterialPageRoute(
              builder: (context) => MainScreen(username: _emailController.text.trim())));
        } else {
           if (!mounted) return;
           ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('需要权限才能使用')));
        }
      } else {
        if (!mounted) return;
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(data['message'] ?? '登录失败')));
      }
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('网络错误: $e')));
    } finally {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('志愿者登录'),
        centerTitle: true,
        actions: [
          IconButton(
            icon: const Icon(Icons.settings),
            onPressed: () {
              Navigator.push(
                context,
                MaterialPageRoute(builder: (context) => const IpConfigPage()),
              );
            },
          ),
        ],
      ),
      backgroundColor: Theme.of(context).colorScheme.surface,
      body: Center(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(30.0),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              // 品牌 Logo 区域
              Container(
                padding: const EdgeInsets.all(20),
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: Colors.white,
                  boxShadow: [
                    BoxShadow(
                      color: Theme.of(context).colorScheme.primary.withOpacity(0.2),
                      blurRadius: 20,
                      offset: const Offset(0, 10),
                    )
                  ],
                ),
                child: Icon(
                  Icons.volunteer_activism, 
                  size: 80, 
                  color: Theme.of(context).colorScheme.primary
                ),
              ),
              const SizedBox(height: 20),
              Text(
                '欢迎回来',
                style: Theme.of(context).textTheme.headlineMedium?.copyWith(
                  fontWeight: FontWeight.bold,
                  color: Colors.black87,
                ),
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 10),
              Text(
                '成为盲人的眼睛，传递温暖与光亮',
                style: Theme.of(context).textTheme.bodyLarge?.copyWith(color: Colors.grey),
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 40),
              
              // 输入框区域
              Card(
                elevation: 0,
                color: Colors.transparent,
                child: Column(
                  children: [
                    TextField(
                      controller: _emailController,
                      decoration: const InputDecoration(
                        labelText: 'QQ邮箱',
                        prefixIcon: Icon(Icons.email_outlined),
                      ),
                      keyboardType: TextInputType.emailAddress,
                    ),
                    const SizedBox(height: 15),
                    TextField(
                      controller: _passwordController,
                      decoration: const InputDecoration(
                        labelText: '密码',
                        prefixIcon: Icon(Icons.lock_outline),
                      ),
                      obscureText: true,
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 30),
              
              if (_isLoading)
                const Center(child: CircularProgressIndicator())
              else
                Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    ElevatedButton(
                      onPressed: _handleLogin,
                      child: const Text('登 录', style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
                    ),
                    const SizedBox(height: 20),
                    Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        TextButton(
                          onPressed: () {
                            Navigator.push(context, MaterialPageRoute(builder: (_) => const RegisterPage()));
                          },
                          child: const Text("注册新账号"),
                        ),
                        TextButton(
                          onPressed: () {
                            Navigator.push(context, MaterialPageRoute(builder: (_) => const ResetPasswordPage()));
                          },
                          child: const Text("忘记密码?"),
                        ),
                      ],
                    )
                  ],
                ),
            ],
          ),
        ),
      ),
    );
  }
}
