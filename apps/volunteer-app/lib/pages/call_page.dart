import 'package:flutter/material.dart';
import 'package:flutter_webrtc/flutter_webrtc.dart';
import 'package:socket_io_client/socket_io_client.dart' as IO;
import 'dart:io';
import '../config/app_config.dart';

class CallPage extends StatefulWidget {
  final IO.Socket socket;
  final String callerSid;
  final String username;

  const CallPage(
      {super.key,
      required this.socket,
      required this.callerSid,
      required this.username});

  @override
  State<CallPage> createState() => _CallPageState();
}

class _CallPageState extends State<CallPage> {
  final RTCVideoRenderer _remoteRenderer = RTCVideoRenderer();
  RTCPeerConnection? _peerConnection;
  MediaStream? _localAudioStream;
  bool isConnecting = true;
  String iceStatus = "Checking";
  bool _isExiting = false; // 防止重复退出
  Future<void>? _disconnectGraceFuture;
  int _disconnectGraceToken = 0;
  
  // 缩放状态
  double _scale = 1.0;
  double _baseScale = 1.0;
  Offset _offset = Offset.zero;
  Offset _startOffset = Offset.zero;
  Offset _lastOffset = Offset.zero;
  
  // 画面适配模式
  bool _isFitContain = false; // 默认裁剪铺满 (Cover)，点击切换为完整显示 (Contain)
  
  // 音频控制状态已移除

  @override
  void initState() {
    super.initState();
    _initCall();
  }

  Future<void> _initCall() async {
    try {
      await _remoteRenderer.initialize();
      await _createPeerConnection();
      _setupSocketListeners();
      
      print("🚀 发送 accept_help...");
      widget.socket.emit('accept_help', {'caller_sid': widget.callerSid});
    } catch (e) {
      print("初始化通话失败: $e");
      if(mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("错误: $e")));
    }
  }

  Future<void> _createPeerConnection() async {
    _peerConnection = await createPeerConnection(AppConfig.iceConfiguration);

    // 视频始终只接收；音频在拿到本地麦克风后走 sendrecv，避免协商成“只能收不能发”。
    await _peerConnection!.addTransceiver(
      kind: RTCRtpMediaType.RTCRtpMediaTypeVideo,
      init: RTCRtpTransceiverInit(direction: TransceiverDirection.RecvOnly),
    );

    _peerConnection!.onIceCandidate = (candidate) {
      widget.socket.emit('candidate', {
        'target': widget.callerSid,
        'candidate': candidate.toMap(),
      });
    };

    _peerConnection!.onConnectionState = (state) {
      print("📡 WebRTC State: $state");
      if (mounted) setState(() => iceStatus = state.toString().split('.').last);
      
      if (state == RTCPeerConnectionState.RTCPeerConnectionStateConnected) {
         _cancelDisconnectGrace();
         if (mounted) setState(() => isConnecting = false);
      } else if (state == RTCPeerConnectionState.RTCPeerConnectionStateFailed ||
                 state == RTCPeerConnectionState.RTCPeerConnectionStateClosed) {
         print("❌ WebRTC 连接断开，自动退出通话");
         if (mounted && !_isExiting) {
           _isExiting = true;
           ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("连接已断开")));
           Navigator.of(context).pop();
         }
      } else if (state == RTCPeerConnectionState.RTCPeerConnectionStateDisconnected) {
         _scheduleDisconnectGrace("WebRTC");
      }
    };

    _peerConnection!.onIceConnectionState = (state) {
      print("❄️ ICE State: $state");
      if (mounted) setState(() => iceStatus = state.toString().split('.').last);
      
      if (state == RTCIceConnectionState.RTCIceConnectionStateConnected ||
          state == RTCIceConnectionState.RTCIceConnectionStateCompleted) {
         _cancelDisconnectGrace();
      } else if (state == RTCIceConnectionState.RTCIceConnectionStateFailed ||
          state == RTCIceConnectionState.RTCIceConnectionStateClosed) {
         print("❌ ICE 连接断开，自动退出通话");
         if (mounted && !_isExiting) {
           _isExiting = true;
           ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("信号中断，通话结束")));
           Navigator.of(context).pop();
         }
      } else if (state == RTCIceConnectionState.RTCIceConnectionStateDisconnected) {
         _scheduleDisconnectGrace("ICE");
      }
    };

    _peerConnection!.onTrack = (event) {
      final trackKind = event.track.kind;
      print("📥 收到远程轨道: $trackKind");

      if ((Platform.isAndroid || Platform.isIOS) && trackKind == 'audio') {
        Helper.setSpeakerphoneOn(true);
      }

      if (trackKind == 'video' && event.streams.isNotEmpty) {
        print("🎥 收到远程视频流");
        if (mounted) {
          setState(() {
            try {
              _remoteRenderer.srcObject = event.streams[0];
            } catch (e) {
              print("❌ 设置远程视频流失败: $e");
            }
          });
        }
      }
    };
    
    // 添加本地音频。若获取不到麦克风，则回退为只接收远端音频。
    final mediaConstraints = {'audio': true, 'video': false};
    try {
      _localAudioStream = await navigator.mediaDevices.getUserMedia(mediaConstraints);
      final localAudioTracks = _localAudioStream!.getAudioTracks();
      if (localAudioTracks.isEmpty) {
        throw Exception('本地麦克风轨道为空');
      }
      for (final track in localAudioTracks) {
        track.enabled = true;
        _peerConnection!.addTrack(track, _localAudioStream!);
      }
      print('🎤 已添加本地麦克风音轨: ${localAudioTracks.length}');
    } catch (e) {
      print('⚠️ 获取麦克风失败 (可能无设备): $e');
      await _peerConnection!.addTransceiver(
        kind: RTCRtpMediaType.RTCRtpMediaTypeAudio,
        init: RTCRtpTransceiverInit(direction: TransceiverDirection.RecvOnly),
      );
      print('🔇 已回退为音频仅接收模式');
    }
  }

  void _cancelDisconnectGrace() {
    _disconnectGraceToken++;
    _disconnectGraceFuture = null;
  }

  void _scheduleDisconnectGrace(String source) {
    if (_disconnectGraceFuture != null || _isExiting) return;
    print("⏳ $source 进入断线宽限期，等待自动恢复...");
    final token = ++_disconnectGraceToken;
    _disconnectGraceFuture = Future<void>.delayed(const Duration(seconds: 8), () {
      if (!mounted || _isExiting) return;
      final pc = _peerConnection;
      final connState = pc?.connectionState;
      final iceState = pc?.iceConnectionState;
      final stillDisconnected =
          connState == RTCPeerConnectionState.RTCPeerConnectionStateDisconnected ||
          iceState == RTCIceConnectionState.RTCIceConnectionStateDisconnected;
      if (stillDisconnected) {
        _isExiting = true;
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("信号中断超过 8 秒，通话结束")));
        Navigator.of(context).pop();
      }
      if (_disconnectGraceToken == token) {
        _disconnectGraceFuture = null;
      }
    });
  }

  void _setupSocketListeners() {
    // 监听 Socket 断开
    widget.socket.onDisconnect((_) {
      print("❌ 信令服务器断开，退出通话");
      if (mounted && !_isExiting) {
         _isExiting = true;
         ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("服务器连接中断")));
         Navigator.of(context).pop();
      }
    });

    // 监听 Offer
    widget.socket.on('offer', (data) async {
      print("📩 收到 Offer");
      try {
        await _peerConnection!.setRemoteDescription(
            RTCSessionDescription(data['sdp'], data['type']));
        
        RTCSessionDescription answer = await _peerConnection!.createAnswer();
        await _peerConnection!.setLocalDescription(answer);

        print("📤 发送 Answer");
        widget.socket.emit('answer', {
          'target': widget.callerSid,
          'sdp': answer.sdp,
          'type': answer.type,
        });
      } catch (e) {
        print('Offer 处理错误: $e');
      }
    });

    // 监听 Candidate
    widget.socket.on('candidate', (data) async {
      try {
        dynamic candidateData = data['candidate'];
        if (candidateData != null) {
          RTCIceCandidate candidate = RTCIceCandidate(
            candidateData['candidate'],
            candidateData['sdpMid'],
            candidateData['sdpMLineIndex'],
          );
          await _peerConnection!.addCandidate(candidate);
        }
      } catch (e) {
        print('Candidate 错误: $e');
      }
    });

    // 监听挂断
    widget.socket.on('bye', (_) {
      print("👋 对方挂断");
      if (mounted && !_isExiting) {
        _isExiting = true;
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("对方已挂断")));
        Navigator.of(context).pop();
      }
    });
  }

  void _hangUp() {
    if (_isExiting) return;
    _isExiting = true;
    widget.socket.emit('bye', {'target': widget.callerSid});
    if (mounted) Navigator.of(context).pop();
  }

  void _toggleFitMode() {
    setState(() {
      _isFitContain = !_isFitContain;
      // 切换模式时重置缩放
      _scale = 1.0;
      _offset = Offset.zero;
    });
  }

  @override
  void dispose() {
    // 清理监听器，避免回到大厅重复监听
    widget.socket.off('offer');
    widget.socket.off('candidate');
    widget.socket.off('bye');
    widget.socket.off('disconnect'); // 清理 disconnect 监听

    final localAudioStream = _localAudioStream;
    if (localAudioStream != null) {
      for (final track in localAudioStream.getTracks()) {
        track.stop();
      }
      _localAudioStream = null;
    }

    _remoteRenderer.dispose();
    _peerConnection?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.black,
      body: Stack(
        children: [
          // 视频画面 (支持双指缩放 - 仅变换，不裁剪)
          Center(
            child: _remoteRenderer.srcObject != null 
                ? GestureDetector(
                    onScaleStart: (details) {
                      _baseScale = _scale;
                      _startOffset = details.focalPoint;
                      _lastOffset = _offset;
                    },
                    onScaleUpdate: (details) {
                      setState(() {
                        // 1. 计算缩放
                        _scale = (_baseScale * details.scale).clamp(1.0, 5.0);
                        
                        // 2. 计算位移 (只有放大时才允许移动)
                        if (_scale > 1.0) {
                           // 计算相对于开始点的位移
                           final delta = details.focalPoint - _startOffset;
                           // 叠加到上次的位移上
                           _offset = _lastOffset + delta;
                        } else {
                           _offset = Offset.zero;
                        }
                      });
                    },
                    child: Transform(
                      transform: Matrix4.identity()
                        ..translate(_offset.dx, _offset.dy)
                        ..scale(_scale),
                      alignment: Alignment.center,
                      child: RTCVideoView(
                        _remoteRenderer,
                        objectFit: _isFitContain 
                            ? RTCVideoViewObjectFit.RTCVideoViewObjectFitContain 
                            : RTCVideoViewObjectFit.RTCVideoViewObjectFitCover,
                      ),
                    ),
                  )
                : const CircularProgressIndicator(color: Colors.white),
          ),
          
          // 缩放提示 (仅在默认比例时显示)
          if (_scale == 1.0 && _remoteRenderer.srcObject != null)
            Positioned(
              bottom: 150,
              left: 0,
              right: 0,
              child: Center(
                child: Container(
                  padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                  decoration: BoxDecoration(
                    color: Colors.black54,
                    borderRadius: BorderRadius.circular(20),
                  ),
                  child: const Text(
                    "双指捏合可放大画面细节",
                    style: TextStyle(color: Colors.white70, fontSize: 12),
                  ),
                ),
              ),
            ),
          
          // 画面适配切换按钮 (右侧)
          if (_remoteRenderer.srcObject != null)
            Positioned(
              right: 20,
              bottom: 120,
              child: FloatingActionButton(
                heroTag: "fit_toggle",
                backgroundColor: Colors.black54,
                mini: true,
                onPressed: _toggleFitMode,
                child: Icon(
                  _isFitContain ? Icons.fullscreen_exit : Icons.fullscreen,
                  color: Colors.white,
                ),
              ),
            ),

          // 顶部状态
          Positioned(
            top: 40,
            left: 20,
            child: Text(
              isConnecting ? "正在建立连接..." : "通话中",
              style: const TextStyle(color: Colors.white, fontSize: 16, shadows: [Shadow(blurRadius: 2, color: Colors.black)]),
            ),
          ),

          // 底部控制栏 (仅挂断)
          Positioned(
            bottom: 60,
            left: 0,
            right: 0,
            child: Center(
              child: Container(
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  boxShadow: [
                    BoxShadow(
                      color: Colors.red.withOpacity(0.4),
                      blurRadius: 20,
                      spreadRadius: 5,
                    )
                  ],
                ),
                child: FloatingActionButton.large(
                  heroTag: "hangup_btn",
                  backgroundColor: Colors.redAccent,
                  elevation: 10,
                  onPressed: _hangUp,
                  child: const Icon(Icons.call_end, size: 40),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
