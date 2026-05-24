
import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../config/app_config.dart';

class IpConfigPage extends StatefulWidget {
  const IpConfigPage({super.key});

  @override
  State<IpConfigPage> createState() => _IpConfigPageState();
}

class _IpConfigPageState extends State<IpConfigPage> {
  final TextEditingController _ipController = TextEditingController();
  final TextEditingController _portController = TextEditingController(text: "8000");

  @override
  void initState() {
    super.initState();
    _loadCurrentIp();
  }

  Future<void> _loadCurrentIp() async {
    final prefs = await SharedPreferences.getInstance();
    final savedIp = prefs.getString('custom_server_ip');
    setState(() {
      _ipController.text = savedIp ?? AppConfig.serverIp;
    });
  }

  Future<void> _saveIp() async {
    final newIp = _ipController.text.trim();
    if (newIp.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('IP 不能为空')));
      return;
    }

    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('custom_server_ip', newIp);
    
    // 更新内存中的配置 (需要 AppConfig 支持动态修改，或者重启 App)
    // 这里提示用户重启
    if (!mounted) return;
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('配置已保存'),
        content: const Text('请重启应用以使新 IP 生效。'),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(),
            child: const Text('确定'),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('服务器设置')),
      body: Padding(
        padding: const EdgeInsets.all(16.0),
        child: Column(
          children: [
            const Text(
              '临时修改服务器 IP (用于测试)',
              style: TextStyle(color: Colors.grey, fontSize: 14),
            ),
            const SizedBox(height: 20),
            TextField(
              controller: _ipController,
              decoration: const InputDecoration(
                labelText: '服务器 IP 地址',
                hintText: '例如: 127.0.0.1',
                border: OutlineInputBorder(),
              ),
              keyboardType: TextInputType.number,
            ),
            const SizedBox(height: 20),
            ElevatedButton(
              onPressed: _saveIp,
              style: ElevatedButton.styleFrom(
                minimumSize: const Size(double.infinity, 50),
              ),
              child: const Text('保存并应用'),
            ),
            const SizedBox(height: 20),
            TextButton(
              onPressed: () async {
                final prefs = await SharedPreferences.getInstance();
                await prefs.remove('custom_server_ip');
                setState(() {
                  _ipController.text = AppConfig.serverIp; // 恢复默认
                });
                if(!mounted) return;
                ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('已恢复默认 IP')));
              },
              child: const Text('恢复默认配置'),
            )
          ],
        ),
      ),
    );
  }
}
