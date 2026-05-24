import 'package:flutter/material.dart';
import 'package:flutter/foundation.dart';
import 'package:socket_io_client/socket_io_client.dart' as IO;
import 'package:permission_handler/permission_handler.dart';
import 'package:flutter_background/flutter_background.dart' as fb;
import 'package:vibration/vibration.dart';
import 'package:flutter_ringtone_player/flutter_ringtone_player.dart';
import 'package:flutter/services.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'dart:io';
import '../config/app_config.dart';
import 'call_page.dart';
import '../services/theme_service.dart';

class LobbyView extends StatefulWidget {
  final String username;
  const LobbyView({super.key, required this.username});
  @override
  State<LobbyView> createState() => _LobbyViewState();
}

class _LobbyViewState extends State<LobbyView> with SingleTickerProviderStateMixin {
  late IO.Socket socket;
  String statusMessage = "正在连接服务器...";
  Color statusColor = Colors.orange;
  List<String> logs = []; // 屏幕日志
  static const platform = MethodChannel('com.example.volunteer_app/bring_to_front');
  final FlutterLocalNotificationsPlugin flutterLocalNotificationsPlugin = FlutterLocalNotificationsPlugin();
  late AnimationController _rippleController;
  final ThemeService _themeService = ThemeService();

  @override
  void initState() {
    super.initState();
    _rippleController = AnimationController(
      vsync: this,
      duration: const Duration(seconds: 2),
    )..repeat(reverse: true);
    _initNotifications();
    _requestSystemAlertPermission();
    _requestNotificationPermission();
    _requestBatteryOptimizationPermission();
    _connectSocket();
    _themeService.addListener(_onThemeChanged);
  }

  void _initNotifications() async {
    if (kIsWeb || (!Platform.isAndroid && !Platform.isIOS)) return;

    const AndroidInitializationSettings initializationSettingsAndroid =
        AndroidInitializationSettings('@mipmap/ic_launcher');
    const InitializationSettings initializationSettings =
        InitializationSettings(android: initializationSettingsAndroid);
    
    await flutterLocalNotificationsPlugin.initialize(
      initializationSettings,
      onDidReceiveNotificationResponse: (NotificationResponse response) async {
        // 点击通知时，将 App 带到前台（实际上通知点击默认就会打开 App）
        _bringAppToFront();
      }
    );
  }

  Future<void> _showNotification() async {
    if (kIsWeb || (!Platform.isAndroid && !Platform.isIOS)) return;

    if (Platform.isAndroid) {
      final notificationStatus = await Permission.notification.status;
      if (!notificationStatus.isGranted) {
        _addLog("❌ 无法弹出来电通知：通知权限未授权");
        return;
      }
    }

    const AndroidNotificationDetails androidNotificationDetails =
        AndroidNotificationDetails(
            'call_channel', '求助来电',
            channelDescription: '盲人用户发起的视频求助',
            importance: Importance.max,
            priority: Priority.high,
            fullScreenIntent: true, // 关键：尝试全屏通知
            ticker: '有求助来电！');
            
    const NotificationDetails notificationDetails =
        NotificationDetails(android: androidNotificationDetails);
        
    await flutterLocalNotificationsPlugin.show(
        0, '求助来电', '盲人用户请求视频协助，点击接听', notificationDetails);
  }

  Future<void> _requestSystemAlertPermission() async {
    if (!Platform.isAndroid) return;

    // 请求后台弹出界面权限 (Android 10+)
    var status = await Permission.systemAlertWindow.status;
    if (!status.isGranted) {
      await Permission.systemAlertWindow.request();
    }
    
    // 很多厂商的“后台弹出界面”权限是独立的，Permission handler 覆盖不到
    // 建议提示用户手动去开
    if (await Permission.systemAlertWindow.isDenied) {
       _addLog("⚠️ 请在设置中开启“显示在其他应用上层”权限");
    }
  }

  Future<void> _requestNotificationPermission() async {
    if (kIsWeb || !Platform.isAndroid) return;
    final status = await Permission.notification.status;
    if (!status.isGranted) {
      final result = await Permission.notification.request();
      if (result.isGranted) {
        _addLog("✅ 通知权限已开启");
      } else {
        _addLog("⚠️ 通知权限未开启，后台来电可能无法弹出");
      }
    }
  }

  Future<void> _requestBatteryOptimizationPermission() async {
    if (kIsWeb || !Platform.isAndroid) return;
    final status = await Permission.ignoreBatteryOptimizations.status;
    if (!status.isGranted) {
      final result = await Permission.ignoreBatteryOptimizations.request();
      if (result.isGranted) {
        _addLog("✅ 已允许忽略电池优化");
      } else {
        _addLog("⚠️ 未关闭电池优化，后台连接可能被系统中断");
      }
    }
  }

  Future<void> _bringAppToFront() async {
    if (!Platform.isAndroid) return;

    try {
      // 必须先检查权限，否则调用可能会静默失败
      if (await Permission.systemAlertWindow.isGranted) {
          await platform.invokeMethod('bringAppToFront');
      } else {
          _addLog("❌ 无法后台弹出：缺少悬浮窗权限");
      }
    } on PlatformException catch (e) {
      print("Failed to bring app to front: '${e.message}'.");
    }
  }

  Future<void> _startVibration() async {
    if (kIsWeb || (!Platform.isAndroid && !Platform.isIOS)) return;

    // 播放系统铃声
    FlutterRingtonePlayer.playRingtone(looping: true);
    
    // 震动
    if (await Vibration.hasVibrator() ?? false) {
      Vibration.vibrate(pattern: [500, 1000, 500, 1000], repeat: 0);
    }
  }

  void _stopVibration() {
    if (kIsWeb || (!Platform.isAndroid && !Platform.isIOS)) return;

    FlutterRingtonePlayer.stop();
    Vibration.cancel();
  }

  void _addLog(String msg) {
    print(msg);
    if (mounted) {
      setState(() {
        logs.insert(0, "${DateTime.now().hour}:${DateTime.now().minute}:${DateTime.now().second} $msg");
        if (logs.length > 50) logs.removeLast();
      });
    }
  }

  void _connectSocket() async {
    // --- 1. 初始化后台保活 ---
    if (!kIsWeb && Platform.isAndroid) {
      try {
        final androidConfig = fb.FlutterBackgroundAndroidConfig(
          notificationTitle: "导盲志愿者在线中",
          notificationText: "正在后台保持连接，随时准备接听求助...",
          notificationImportance: fb.AndroidNotificationImportance.Default,
          notificationIcon: fb.AndroidResource(name: 'ic_launcher', defType: 'mipmap'),
        );
        
        bool hasPermissions = await fb.FlutterBackground.hasPermissions;
        if (!hasPermissions) {
          _addLog("请求后台运行权限...");
        }
        
        bool success = await fb.FlutterBackground.initialize(androidConfig: androidConfig);
        if (success) {
          bool enabled = await fb.FlutterBackground.enableBackgroundExecution();
          if (enabled) {
            _addLog("✅ 后台保活服务已启动");
          } else {
            _addLog("❌ 后台保活启动失败");
          }
        } else {
          _addLog("❌ 后台服务初始化失败");
        }
      } catch (e) {
        _addLog("后台服务错误: $e");
      }
    }

    // --- 2. 连接 Socket ---
    _addLog("尝试连接: ${AppConfig.socketUrl}");
    
    socket = IO.io(AppConfig.socketUrl, <String, dynamic>{
      'transports': ['websocket'],
      'autoConnect': false,
      'reconnection': true,
      'reconnectionAttempts': 999,
      'reconnectionDelay': 1000,
      'timeout': 20000, // 增加超时
      'pingInterval': 10000, // 心跳间隔
      'pingTimeout': 5000, // 心跳超时
      'forceNew': true, // 强制创建新连接，解决注销重连失败问题
    });

    socket.connect();

    socket.onConnect((_) {
      _addLog("✅ 服务器连接成功！");
      setState(() {
        statusMessage = "在线 - 等待呼叫";
        statusColor = Colors.green;
      });
      socket.emit('join', {
        'room': 'stream_room',
        'role': 'volunteer',
        'userId': widget.username,
      });
    });

    socket.onConnectError((data) {
      _addLog("❌ 连接错误: $data");
      setState(() {
        statusMessage = "连接失败 - 重试中...";
        statusColor = Colors.red;
      });
    });

    socket.onDisconnect((_) {
      _addLog("❌ 服务器断开连接");
      setState(() {
        statusMessage = "离线";
        statusColor = Colors.grey;
      });
    });

    socket.on('incoming_call', (data) {
      _addLog("📞 收到求助呼叫: $data");
      // 1. 发送高优先级通知 (包含 Full Screen Intent)
      _showNotification();
      // 2. 唤醒屏幕并前台显示
      _bringAppToFront();
      // 3. 开始震动
      _startVibration();
      // 4. 显示弹窗
      _showIncomingCallDialog(data);
    });

    socket.on('cancel_call', (data) {
      _addLog("呼叫已取消或超时");
      _stopVibration();
      if (Navigator.canPop(context)) {
        // 如果当前有弹窗 (AlertDialog)，尝试关闭它
        // 注意：这里需要更精确的判断，但简单起见，假设顶层路由是 dialog
        Navigator.of(context).pop(); 
      }
    });
  }

  void _showIncomingCallDialog(dynamic data) {
    String callerSid = data['caller_sid'] ?? '未知用户';
    showDialog(
      context: context,
      barrierDismissible: false,
      builder: (context) => AlertDialog(
        title: const Text('求助来电'),
        content: const Text('盲人用户请求视频协助，是否接听？'),
        actions: [
          TextButton(
            onPressed: () {
              _stopVibration(); // 停止震动
              Navigator.of(context).pop();
              _addLog("已拒绝呼叫");
            },
            child: const Text('拒绝', style: TextStyle(color: Colors.grey)),
          ),
          ElevatedButton(
            onPressed: () {
              _stopVibration(); // 停止震动
              Navigator.of(context).pop();
              _navigateToCallPage(callerSid);
            },
            style: ElevatedButton.styleFrom(backgroundColor: Colors.green),
            child: const Text('接听', style: TextStyle(color: Colors.white)),
          ),
        ],
      ),
    );
  }

  void _navigateToCallPage(String callerSid) async {
    _addLog("跳转通话页面...");
    
    try {
      // 停止铃声和震动 (确保接听后立刻停止)
      _stopVibration();
      // 清除通知
      await flutterLocalNotificationsPlugin.cancel(0);
    } catch (e) {
      print("清理通知/震动失败: $e");
    }

    try {
      // 暂时移除 Socket 监听，交给 CallPage 处理
      socket.off('incoming_call');
    } catch (e) {
      print("移除监听失败: $e");
    }
    
    if (!mounted) return;
    
    try {
      await Navigator.of(context).push(MaterialPageRoute(
          builder: (context) => CallPage(
              socket: socket, 
              callerSid: callerSid, 
              username: widget.username
          )));
    } catch (e) {
      _addLog("跳转失败: $e");
      // 尝试恢复监听
      socket.on('incoming_call', (data) {
          _addLog("📞 收到求助呼叫: $data");
          _showNotification();
          _bringAppToFront();
          _startVibration();
          _showIncomingCallDialog(data);
      });
      return;
    }
    
    // 通话结束后，重新恢复监听
    _addLog("通话结束，回到大厅");
    // 确保回来后状态是干净的
    _stopVibration(); 
    
    // 重新注册监听
    socket.on('incoming_call', (data) {
        _addLog("📞 收到求助呼叫: $data");
        // 1. 发送高优先级通知 (包含 Full Screen Intent)
        _showNotification();
        // 2. 尝试唤醒屏幕并前台显示 (后台弹出权限)
        _bringAppToFront();
        // 3. 开始震动
        _startVibration();
        // 4. 显示弹窗
        _showIncomingCallDialog(data);
    });

    socket.on('cancel_call', (data) {
      _addLog("呼叫已取消或超时");
      _stopVibration();
      // 关闭弹窗
      Navigator.of(context).popUntil((route) => route.isFirst);
    });
  }

  @override
  void dispose() {
    _rippleController.dispose();
    socket.dispose();
    _themeService.removeListener(_onThemeChanged);
    super.dispose();
  }

  void _onThemeChanged() {
    if (mounted) setState(() {});
  }

  @override
  Widget build(BuildContext context) {
    final currentTheme = _themeService.currentTheme;
    final primaryColor = currentTheme.gradientColors.first;
    final isOnline = statusColor == Colors.green;
    
    // 检查是否是浅色主题
    final isLightTheme = currentTheme.brightness == Brightness.light;

    return Stack(
      children: [
        // 背景颜色 (浅灰色)
        Container(color: Colors.grey[50]),
        
        // 背景纹理
        Positioned.fill(
          child: CustomPaint(
            painter: _DotGridPainter(color: Colors.grey.withOpacity(0.05)),
          ),
        ),
        
        // 顶部沉浸式状态区域 (新增)
        Positioned(
          top: 0,
          left: 0,
          right: 0,
          height: MediaQuery.of(context).size.height * 0.25,
          child: Container(
            decoration: BoxDecoration(
              gradient: LinearGradient(
                begin: Alignment.topLeft,
                end: Alignment.bottomRight,
                colors: currentTheme.gradientColors,
              ),
              boxShadow: [
                BoxShadow(
                  color: Colors.black.withOpacity(0.1),
                  blurRadius: 10,
                  offset: const Offset(0, 5),
                )
              ],
              borderRadius: const BorderRadius.only(
                bottomLeft: Radius.circular(40),
                bottomRight: Radius.circular(40),
              ),
            ),
            child: Stack(
              children: [
                // 品牌 Logo 水印
                Positioned(
                  right: -30,
                  top: -30,
                  child: Icon(
                    Icons.volunteer_activism,
                    size: 200,
                    // 浅色主题下水印要深一点，深色主题下水印要白一点
                    color: isLightTheme 
                        ? primaryColor.withOpacity(0.05) 
                        : Colors.white.withOpacity(0.1),
                  ),
                ),
                SafeArea(
                  child: Padding(
                    padding: const EdgeInsets.only(top: 20, left: 24),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          "接单中心",
                          style: TextStyle(
                            fontSize: 28,
                            fontWeight: FontWeight.bold,
                            color: currentTheme.textColor,
                            letterSpacing: 1.2,
                            shadows: [
                              Shadow(
                                offset: const Offset(0, 2),
                                blurRadius: 4.0,
                                color: Colors.black.withOpacity(0.1),
                              ),
                            ],
                          ),
                        ),
                        const SizedBox(height: 8),
                        Container(
                          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                          decoration: BoxDecoration(
                            // 浅色主题下使用稍深的背景色，深色主题下使用白色半透明
                            color: isLightTheme 
                                ? Colors.grey[200] 
                                : Colors.white.withOpacity(0.9),
                            borderRadius: BorderRadius.circular(20),
                            boxShadow: [
                              BoxShadow(
                                color: Colors.black.withOpacity(0.05),
                                blurRadius: 4,
                                offset: const Offset(0, 2),
                              )
                            ],
                          ),
                          child: Row(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              Container(
                                width: 8, height: 8,
                                decoration: BoxDecoration(
                                  color: isOnline ? Colors.green : Colors.red,
                                  shape: BoxShape.circle,
                                ),
                              ),
                              const SizedBox(width: 8),
                              Text(
                                isOnline ? "服务中" : "离线",
                                style: TextStyle(
                                  fontSize: 14,
                                  fontWeight: FontWeight.bold,
                                  color: isOnline 
                                      ? Colors.green[700] 
                                      : Colors.red[700],
                                ),
                              ),
                            ],
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),

        // 主要内容区域
        Positioned.fill(
          child: SafeArea(
            child: Column(
              children: [
                // 顶部区域留空 (25%)
                SizedBox(height: MediaQuery.of(context).size.height * 0.25),
                
                // 填充剩余空间，使圆环位于中下部 (下沉式布局)
                Expanded(
                  child: Column(
                    mainAxisAlignment: MainAxisAlignment.center, // 垂直居中于剩余空间
                    children: [
                      // 信息包裹圆环 - 上方文字
                      Padding(
                        padding: const EdgeInsets.symmetric(horizontal: 40),
                        child: Text(
                          isOnline
                              ? "已准备好接听求助\n您可以将 App 切换至后台，连接将保持"
                              : "正在尝试连接服务器...",
                          textAlign: TextAlign.center,
                          style: TextStyle(
                            fontSize: 15, 
                            color: Colors.grey[600],
                            height: 1.4,
                            fontWeight: FontWeight.w500,
                          ),
                        ),
                      ),

                      const SizedBox(height: 30),

                      // 中心圆环
                      AnimatedBuilder(
                        animation: _rippleController,
                        builder: (context, child) {
                          final double scale = isOnline ? 1.0 + (_rippleController.value * 0.05) : 1.0;
                          final double spread = isOnline ? 15 + (_rippleController.value * 20) : 10;
                          final double blur = isOnline ? 50 + (_rippleController.value * 15) : 30;
                          
                          return Transform.scale(
                            scale: scale,
                            child: Container(
                              width: 200,
                              height: 200,
                              decoration: BoxDecoration(
                                shape: BoxShape.circle,
                                color: Colors.white,
                                boxShadow: [
                                  // 柔化投影 (Soft Glow)
                                  BoxShadow(
                                    color: isOnline 
                                      ? Colors.greenAccent.withOpacity(0.3) 
                                      : Colors.grey.withOpacity(0.1),
                                    blurRadius: blur,
                                    spreadRadius: spread,
                                  ),
                                  // 内部阴影增加立体感
                                  BoxShadow(
                                    color: Colors.white,
                                    blurRadius: 20,
                                    spreadRadius: -5,
                                  ),
                                ],
                              ),
                              child: child,
                            ),
                          );
                        },
                        child: Center(
                          child: Icon(
                            isOnline ? Icons.wifi_tethering : Icons.wifi_off,
                            size: 80,
                            color: statusColor,
                          ),
                        ),
                      ),
                      
                      const SizedBox(height: 20),
                      
                      // 原来的 "在线/离线" 大字现在没那么重要了，可以稍微缩小或者保留在圆环下方辅助
                      Text(
                        isOnline ? "ONLINE" : "OFFLINE",
                        style: TextStyle(
                          fontSize: 18,
                          fontWeight: FontWeight.bold,
                          color: statusColor.withOpacity(0.5),
                          letterSpacing: 2.0,
                        ),
                      ),
                      
                      const SizedBox(height: 40),

                      // 手动重连按钮
                      if (!isOnline)
                        ElevatedButton.icon(
                          onPressed: () {
                            socket.connect();
                          },
                          icon: const Icon(Icons.refresh),
                          label: const Text("手动重连"),
                          style: ElevatedButton.styleFrom(
                            padding: const EdgeInsets.symmetric(horizontal: 32, vertical: 12),
                            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(30)),
                          ),
                        ),
                    ],
                  ),
                ),

                // 底部微调：细微分割线
                Container(
                  height: 1,
                  width: 100,
                  color: Colors.grey.withOpacity(0.1),
                ),
                const SizedBox(height: 10), // 留出一点空间给导航栏阴影
              ],
            ),
          ),
        ),
      ],
    );
  }
}

class _DotGridPainter extends CustomPainter {
  final Color color;
  _DotGridPainter({required this.color});

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = color
      ..strokeWidth = 1.0;

    const double spacing = 30.0;
    
    for (double x = 0; x < size.width; x += spacing) {
      for (double y = 0; y < size.height; y += spacing) {
        canvas.drawCircle(Offset(x, y), 1.5, paint);
      }
    }
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => false;
}
