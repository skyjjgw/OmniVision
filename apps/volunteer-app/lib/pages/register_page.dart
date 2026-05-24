import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'dart:convert';
import 'dart:async';
import '../config/app_config.dart';

class RegisterPage extends StatefulWidget {
  const RegisterPage({super.key});
  @override
  State<RegisterPage> createState() => _RegisterPageState();
}

class _RegisterPageState extends State<RegisterPage> {
  final _emailController = TextEditingController();
  final _codeController = TextEditingController();
  final _passwordController = TextEditingController();
  bool _isSending = false;
  bool _isSubmitting = false;
  int _countdown = 0;
  Timer? _timer;

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  void _startCountdown() {
    setState(() => _countdown = 60);
    _timer = Timer.periodic(const Duration(seconds: 1), (timer) {
      if (_countdown > 0) {
        setState(() => _countdown--);
      } else {
        timer.cancel();
      }
    });
  }

  Future<void> _sendCode() async {
    if (_emailController.text.isEmpty || !_emailController.text.endsWith('@qq.com')) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('请输入正确的QQ邮箱')));
      return;
    }

    setState(() => _isSending = true);
    try {
      final response = await http.post(
        Uri.parse('${AppConfig.socketUrl}/api/auth/send-code'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({'email': _emailController.text}),
      );
      final data = jsonDecode(response.body);
      
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(data['message'])));
      if (data['success']) {
        _startCountdown();
      }
    } catch (e) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('发送失败: $e')));
    } finally {
      setState(() => _isSending = false);
    }
  }

  Future<void> _handleRegister() async {
    if (_emailController.text.isEmpty || _codeController.text.isEmpty || _passwordController.text.isEmpty) {
      return;
    }
    setState(() => _isSubmitting = true);
    try {
      final response = await http.post(
        Uri.parse('${AppConfig.socketUrl}/api/auth/register'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({
          'email': _emailController.text,
          'code': _codeController.text,
          'password': _passwordController.text,
        }),
      );
      final data = jsonDecode(response.body);
      
      if (data['success']) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('注册成功，请登录')));
        Navigator.pop(context);
      } else {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(data['message'])));
      }
    } catch (e) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('注册失败: $e')));
    } finally {
      setState(() => _isSubmitting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text("注册账号")),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(20),
        child: Column(
          children: [
             TextField(
                controller: _emailController,
                decoration: const InputDecoration(labelText: 'QQ邮箱', border: OutlineInputBorder()),
             ),
             const SizedBox(height: 10),
             Row(
               children: [
                 Expanded(
                   child: TextField(
                      controller: _codeController,
                      decoration: const InputDecoration(labelText: '验证码', border: OutlineInputBorder()),
                   ),
                 ),
                 const SizedBox(width: 10),
                 ElevatedButton(
                   onPressed: (_isSending || _countdown > 0) ? null : _sendCode,
                   style: ElevatedButton.styleFrom(
                     padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 16),
                   ),
                   child: Text(
                     _countdown > 0 ? "${_countdown}s" : "获取验证码",
                     style: const TextStyle(fontSize: 13, fontWeight: FontWeight.bold),
                   ),
                 )
               ],
             ),
             const SizedBox(height: 10),
             TextField(
                controller: _passwordController,
                decoration: const InputDecoration(labelText: '密码', border: OutlineInputBorder()),
                obscureText: true,
             ),
             const SizedBox(height: 20),
             ElevatedButton(
               onPressed: _isSubmitting ? null : _handleRegister,
               style: ElevatedButton.styleFrom(minimumSize: const Size(double.infinity, 50)),
               child: _isSubmitting ? const CircularProgressIndicator() : const Text("注册"),
             )
          ],
        ),
      ),
    );
  }
}
