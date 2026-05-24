import 'package:flutter/material.dart';
import 'reset_password_page.dart';

class SettingsPage extends StatefulWidget {
  const SettingsPage({super.key});

  @override
  State<SettingsPage> createState() => _SettingsPageState();
}

class _SettingsPageState extends State<SettingsPage> {
  bool _notificationsEnabled = true;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text("设置"),
        backgroundColor: Colors.white,
        elevation: 0,
        foregroundColor: Colors.black,
      ),
      backgroundColor: Colors.grey[50],
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Container(
            decoration: BoxDecoration(
              color: Colors.white,
              borderRadius: BorderRadius.circular(16),
              boxShadow: [BoxShadow(color: Colors.black.withOpacity(0.05), blurRadius: 10)],
            ),
            child: Column(
              children: [
                SwitchListTile(
                  title: const Text("接收通知"),
                  subtitle: const Text("开启后可收到求助消息和社区互动通知"),
                  value: _notificationsEnabled,
                  activeColor: Colors.blue,
                  onChanged: (val) => setState(() => _notificationsEnabled = val),
                ),
                const Divider(height: 1, indent: 16, endIndent: 16),
                ListTile(
                  leading: const Icon(Icons.lock_outline, color: Colors.blueGrey),
                  title: const Text("修改密码"),
                  trailing: const Icon(Icons.arrow_forward_ios, size: 16, color: Colors.grey),
                  onTap: () {
                     Navigator.push(context, MaterialPageRoute(builder: (_) => const ResetPasswordPage()));
                  },
                ),
                const Divider(height: 1, indent: 16, endIndent: 16),
                ListTile(
                  leading: const Icon(Icons.privacy_tip_outlined, color: Colors.blueGrey),
                  title: const Text("隐私政策"),
                  trailing: const Icon(Icons.arrow_forward_ios, size: 16, color: Colors.grey),
                  onTap: () {
                    showDialog(
                      context: context, 
                      builder: (ctx) => AlertDialog(
                        title: const Text("隐私政策"),
                        content: const SingleChildScrollView(
                          child: Text(
                            "隐私政策\n\n"
                            "1. 信息收集\n我们收集您的基本个人信息用于服务提供。\n\n"
                            "2. 信息使用\n您的信息仅用于盲人求助服务和社区互动。\n\n"
                            "3. 信息保护\n我们采取严格的安全措施保护您的数据。"
                          )
                        ),
                        actions: [TextButton(onPressed: () => Navigator.pop(ctx), child: const Text("关闭"))],
                      )
                    );
                  },
                ),
                const Divider(height: 1, indent: 16, endIndent: 16),
                ListTile(
                  leading: const Icon(Icons.cleaning_services_outlined, color: Colors.blueGrey),
                  title: const Text("清除缓存"),
                  onTap: () {
                    ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("缓存已清除")));
                  },
                ),
              ],
            ),
          ),
          const SizedBox(height: 20),
          Container(
            decoration: BoxDecoration(
              color: Colors.white,
              borderRadius: BorderRadius.circular(16),
              boxShadow: [BoxShadow(color: Colors.black.withOpacity(0.05), blurRadius: 10)],
            ),
            child: ListTile(
              leading: const Icon(Icons.info_outline, color: Colors.blueGrey),
              title: const Text("关于版本"),
              subtitle: const Text("v1.0.0"),
            ),
          ),
        ],
      ),
    );
  }
}
