import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'dart:convert';
import 'dart:async';
import '../config/app_config.dart';

class ResetPasswordPage extends StatefulWidget {
  const ResetPasswordPage({super.key});
  @override
  State<ResetPasswordPage> createState() => _ResetPasswordPageState();
}

class _ResetPasswordPageState extends State<ResetPasswordPage> {
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
    if (_emailController.text.isEmpty) return;
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

  Future<void> _handleReset() async {
    setState(() => _isSubmitting = true);
    try {
      final response = await http.post(
        Uri.parse('${AppConfig.socketUrl}/api/auth/reset-password'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({
          'email': _emailController.text,
          'code': _codeController.text,
          'password': _passwordController.text,
        }),
      );
      final data = jsonDecode(response.body);
      if (data['success']) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('密码重置成功，请登录')));
        Navigator.pop(context);
      } else {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(data['message'])));
      }
    } catch (e) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('重置失败: $e')));
    } finally {
      setState(() => _isSubmitting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text("重置密码")),
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
                decoration: const InputDecoration(labelText: '新密码', border: OutlineInputBorder()),
                obscureText: true,
             ),
             const SizedBox(height: 20),
             ElevatedButton(
               onPressed: _isSubmitting ? null : _handleReset,
               style: ElevatedButton.styleFrom(minimumSize: const Size(double.infinity, 50)),
               child: _isSubmitting ? const CircularProgressIndicator() : const Text("确认重置"),
             )
          ],
        ),
      ),
    );
  }
}
