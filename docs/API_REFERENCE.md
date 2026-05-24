# API 接口参考文档

## 1. 说明
本文件汇总当前仓库中对外暴露的主要 HTTP API、WebSocket 入口和 Socket.IO 事件，帮助新的开发者快速定位接口代码、理解权限模型并构造调用请求。

## 2. 服务划分
### 2.1 FastAPI 主业务服务
- 代码文件：`apps/cloud-server/backend.py`
- 默认端口：`8000`
- 作用：主业务 API、后台页面、原生 WebSocket

### 2.2 Socket.IO / 社区 / 信令服务
- 代码文件：`apps/cloud-server/signaling_server.py`
- 默认端口：`6000`
- 作用：Socket.IO 事件、WebRTC 信令、社区和用户资料相关接口

## 3. 鉴权说明
### 3.1 管理后台鉴权
- 通过 `POST /api/admin/login` 获取后台登录态
- 成功后会写入 `admin_token` Cookie
- 管理类接口通常要求该 Cookie

### 3.2 普通接口
- 当前部分接口未看到统一 Token 鉴权
- 调用前请根据前端实际逻辑和部署环境补充接入层限制

### 3.3 Socket.IO
- 当前以连接事件与业务事件驱动为主
- 可在上层网关或部署环境中增加进一步权限控制

## 4. FastAPI 主业务接口
### 4.1 登录与基础接口
| 方法 | 路径 | 代码位置 | 权限 | 入参 | 出参 |
| --- | --- | --- | --- | --- | --- |
| POST | `/api/user/login` | `apps/cloud-server/backend.py` | 无 | JSON 登录信息 | 登录结果 |
| POST | `/api/login` | `apps/cloud-server/backend.py` | 无 | JSON 登录信息 | 登录结果 |
| POST | `/api/admin/login` | `apps/cloud-server/backend.py` | 无 | `username` `password` | `success` 并写入 Cookie |
| POST | `/api/admin/logout` | `apps/cloud-server/backend.py` | 已登录后台 | 无 | `success` |

#### 示例：后台登录
```bash
curl -X POST "http://127.0.0.1:8000/api/admin/login" \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"admin\",\"password\":\"change_me\"}"
```

### 4.2 后台管理与路由记忆
| 方法 | 路径 | 权限 | 代码位置 | 入参 | 出参 |
| --- | --- | --- | --- | --- | --- |
| GET | `/api/admin/debug_db` | 管理员 | `backend.py` | 无 | 数据库调试结果 |
| POST | `/api/debug/set_user_trust` | 管理员 | `backend.py` | 用户与信任参数 | 更新结果 |
| GET | `/api/admin/route_memories` | 管理员 | `backend.py` | 无 | 路由记忆列表 |
| POST | `/api/admin/route_memories/save` | 管理员 | `backend.py` | `client_id` `alias` `lat` `lng` | 保存结果 |
| DELETE | `/api/admin/route_memories/{mem_id}` | 管理员 | `backend.py` | 路径参数 | 删除结果 |
| POST | `/api/admin/navigation/simulate` | 管理员 | `backend.py` | 模拟导航参数 | 模拟结果 |
| GET | `/api/admin/users` | 管理员 | `backend.py` | 无 | 用户列表 |
| PUT | `/api/admin/users/{user_id}/status` | 管理员 | `backend.py` | 状态参数 | 更新结果 |
| DELETE | `/api/admin/users/{user_id}` | 管理员 | `backend.py` | 路径参数 | 删除结果 |

#### 示例：保存路由记忆
```bash
curl -X POST "http://127.0.0.1:8000/api/admin/route_memories/save" \
  -H "Content-Type: application/json" \
  -b "admin_token=<your_cookie>" \
  -d "{\"client_id\":\"dev-001\",\"alias\":\"宿舍\",\"lat\":31.1125,\"lng\":120.8442}"
```

### 4.3 标注与审核接口
| 方法 | 路径 | 权限 | 代码位置 | 入参 | 出参 |
| --- | --- | --- | --- | --- | --- |
| GET | `/api/admin/annotations` | 管理员 | `backend.py` | 查询参数 | 标注列表 |
| POST | `/api/admin/annotations/analyze_stream` | 管理员 | `backend.py` | 标注分析请求 | 流式分析结果 |
| POST | `/api/admin/annotations/analyze` | 管理员 | `backend.py` | 标注分析请求 | 分析结果 |
| POST | `/api/admin/annotations/batch_analyze` | 管理员 | `backend.py` | 批量分析请求 | 任务启动结果 |
| GET | `/api/admin/annotations/batch_analyze/status` | 管理员 | `backend.py` | 任务 ID 等 | 状态结果 |

### 4.4 障碍物、店铺与争议数据
| 方法 | 路径 | 权限 | 代码位置 | 入参 | 出参 |
| --- | --- | --- | --- | --- | --- |
| GET | `/api/obstacles` | 公开 / 业务侧 | `backend.py` | 查询参数 | 障碍物列表 |
| POST | `/api/obstacles` | 业务侧 | `backend.py` | 障碍物 JSON | 创建结果 |
| DELETE | `/api/obstacles/{obstacle_id}` | 管理员或业务侧 | `backend.py` | 路径参数 | 删除结果 |
| PUT | `/api/admin/obstacles/{obstacle_id}` | 管理员 | `backend.py` | 更新 JSON | 更新结果 |
| GET | `/api/disputes` | 业务侧 | `backend.py` | 无 | 争议列表 |
| POST | `/api/dispute/vote` | 业务侧 | `backend.py` | 投票参数 | 投票结果 |
| POST | `/api/admin/dispute/{dispute_id}/resolve` | 管理员 | `backend.py` | 处理参数 | 处理结果 |
| POST | `/api/admin/disputes/{dispute_id}/resolve` | 管理员 | `backend.py` | 处理参数 | 处理结果 |
| GET | `/api/shops` | 业务侧 | `backend.py` | 查询参数 | 店铺列表 |
| POST | `/api/shops` | 业务侧 | `backend.py` | 店铺 JSON | 创建结果 |
| PUT | `/api/admin/shops/{shop_id}` | 管理员 | `backend.py` | 更新 JSON | 更新结果 |
| DELETE | `/api/shops/{shop_id}` | 管理员或业务侧 | `backend.py` | 路径参数 | 删除结果 |
| POST | `/api/shops/{shop_id}/image` | 业务侧 | `backend.py` | 图片文件 | 图片 URL |
| POST | `/api/obstacles/{obstacle_id}/image` | 业务侧 | `backend.py` | 图片文件 | 图片 URL |

#### 示例：获取障碍物列表
```bash
curl "http://127.0.0.1:8000/api/obstacles"
```

### 4.5 语音、模式与地理接口
| 方法 | 路径 | 权限 | 代码位置 | 入参 | 出参 |
| --- | --- | --- | --- | --- | --- |
| POST | `/api/asr/update` | 盲人端 / 语音模块 | `backend.py` | ASR 文本、客户端 ID 等 | 对话或导航结果 |
| POST | `/api/asr/preview` | 盲人端 / 调试 | `backend.py` | 预览文本参数 | 预览结果 |
| POST | `/api/set_mode` | 业务侧 | `backend.py` | 模式参数 | 设置结果 |
| POST | `/api/set_source` | 业务侧 | `backend.py` | 来源参数 | 设置结果 |
| POST | `/api/geocode` | 业务侧 | `backend.py` | 地址文本 | 地理编码结果 |

#### 示例：推送 ASR 文本
```bash
curl -X POST "http://127.0.0.1:8000/api/asr/update" \
  -H "Content-Type: application/json" \
  -d "{\"client_id\":\"dev-001\",\"text\":\"前面有什么\"}"
```

### 4.6 投稿、评论与轨迹验证
| 方法 | 路径 | 权限 | 代码位置 | 入参 | 出参 |
| --- | --- | --- | --- | --- | --- |
| POST | `/api/contribution/add` | 业务侧 | `backend.py` | 投稿 JSON | 创建结果 |
| GET | `/api/contribution/list` | 业务侧 | `backend.py` | 查询参数 | 投稿列表 |
| POST | `/api/contribution/vote` | 业务侧 | `backend.py` | 投票 JSON | 投票结果 |
| POST | `/api/contribution/{contribution_id}/comment` | 业务侧 | `backend.py` | 评论 JSON | 评论结果 |
| DELETE | `/api/contribution/{contribution_id}/comment/{comment_id}` | 业务侧 | `backend.py` | 路径参数 | 删除结果 |
| DELETE | `/api/contribution/{contribution_id}` | 业务侧或管理员 | `backend.py` | 路径参数 | 删除结果 |
| POST | `/api/contribution/upload_image` | 业务侧 | `backend.py` | 图片文件 | 上传结果 |
| POST | `/api/admin/contribution/{contribution_id}/approve` | 管理员 | `backend.py` | 审批参数 | 审批结果 |
| PUT | `/api/admin/contribution/{contribution_id}/audit_period` | 管理员 | `backend.py` | 审核周期参数 | 更新结果 |
| DELETE | `/api/admin/contribution/{contribution_id}` | 管理员 | `backend.py` | 路径参数 | 删除结果 |
| DELETE | `/api/admin/contribution/{contribution_id}/comment/{comment_id}` | 管理员 | `backend.py` | 路径参数 | 删除结果 |
| POST | `/api/verify/trajectory` | 业务侧 | `backend.py` | 轨迹验证参数 | 验证结果 |
| GET | `/api/admin/action_logs` | 管理员 | `backend.py` | 查询参数 | 操作日志 |
| POST | `/api/admin/rollback` | 管理员 | `backend.py` | 回滚参数 | 回滚结果 |

### 4.7 志愿者呼叫接口
| 方法 | 路径 | 权限 | 代码位置 | 入参 | 出参 |
| --- | --- | --- | --- | --- | --- |
| POST | `/api/volunteer/call` | 业务侧 | `backend.py` | 呼叫参数 | 呼叫结果 |

### 4.8 认证接口
| 方法 | 路径 | 权限 | 代码位置 | 入参 | 出参 |
| --- | --- | --- | --- | --- | --- |
| POST | `/api/auth/register` | 无 | `backend.py` | 注册 JSON | 注册结果 |
| POST | `/api/auth/login` | 无 | `backend.py` | 登录 JSON | 登录结果 |
| POST | `/api/auth/send-code` | 无 | `backend.py` | 邮箱 / 手机等 | 验证码发送结果 |
| POST | `/api/auth/reset-password` | 无 | `backend.py` | 重置参数 | 重置结果 |

## 5. WebSocket 接口
| 协议 | 路径 | 代码位置 | 作用 | 权限 |
| --- | --- | --- | --- | --- |
| WS | `/ws/stream` | `backend.py` | 云端消息流、前端监控与回复通道 | 业务侧 |
| WS | `/ws/video/{client_id}` | `backend.py` | 视频流相关通道 | 业务侧 |
| WS | `/ws/client_input` | `backend.py` | 盲人端输入与状态通道 | 业务侧 |
| WS | `/ws/esp32_cam` | `backend.py` | 兼容摄像头推流入口 | 业务侧 |

#### 示例：连接消息流
```javascript
const ws = new WebSocket("ws://127.0.0.1:8000/ws/stream");
ws.onmessage = (event) => console.log(event.data);
```

## 6. Socket.IO 与社区 / 用户接口
### 6.1 HTTP 路由
| 方法 | 路径 | 代码位置 | 权限 | 入参 | 出参 |
| --- | --- | --- | --- | --- | --- |
| POST | `/api/auth/send-code` | `signaling_server.py` | 无 | 验证码参数 | 发送结果 |
| POST | `/api/auth/register` | `signaling_server.py` | 无 | 注册参数 | 注册结果 |
| POST | `/api/auth/login` | `signaling_server.py` | 无 | 登录参数 | 登录结果 |
| POST | `/api/auth/reset-password` | `signaling_server.py` | 无 | 重置参数 | 结果 |
| POST | `/api/community/upload` | `signaling_server.py` | 用户侧 | 帖子 / 图片 | 创建结果 |
| GET | `/api/community/list` | `signaling_server.py` | 用户侧 | 查询参数 | 帖子列表 |
| POST | `/api/community/vote` | `signaling_server.py` | 用户侧 | 投票参数 | 投票结果 |
| POST | `/api/community/comment` | `signaling_server.py` | 用户侧 | 评论参数 | 评论结果 |
| POST | `/api/community/comment/delete` | `signaling_server.py` | 用户侧 | 删除参数 | 删除结果 |
| GET | `/api/community/comments` | `signaling_server.py` | 用户侧 | 查询参数 | 评论列表 |
| GET | `/api/community/user/comments` | `signaling_server.py` | 用户侧 | 查询参数 | 当前用户评论列表 |
| GET | `/api/community/user/posts` | `signaling_server.py` | 用户侧 | 查询参数 | 当前用户帖子列表 |
| POST | `/api/community/post/delete` | `signaling_server.py` | 用户侧 | 删除参数 | 删除结果 |
| POST | `/api/user/profile/update` | `signaling_server.py` | 用户侧 | 用户资料参数 | 更新结果 |
| GET | `/api/user/profile` | `signaling_server.py` | 用户侧 | 查询参数 | 用户资料 |

### 6.2 Socket.IO 事件
| 事件名 | 代码位置 | 作用 | 典型载荷 |
| --- | --- | --- | --- |
| `connect` | `signaling_server.py` | 建立连接 | 系统事件 |
| `disconnect` | `signaling_server.py` | 断开连接 | 系统事件 |
| `join` | `signaling_server.py` | 加入房间并注册角色 | `room` `role` `userId` |
| `call_request` | `signaling_server.py` | 发起呼叫 | 呼叫方信息 |
| `cancel_request` | `signaling_server.py` | 取消呼叫 | 呼叫方信息 |
| `accept_call` | `signaling_server.py` | 志愿者接单 | `caller_sid` |
| `accept_help` | `signaling_server.py` | 辅助接单 | 业务数据 |
| `offer` | `signaling_server.py` | WebRTC offer | SDP |
| `answer` | `signaling_server.py` | WebRTC answer | SDP |
| `candidate` | `signaling_server.py` | ICE candidate 交换 | Candidate |
| `bye` | `signaling_server.py` | 结束通话 | 结束信号 |

#### 示例：Socket.IO 连接
```javascript
import { io } from "socket.io-client";

const socket = io("http://127.0.0.1:6000", {
  transports: ["websocket"]
});

socket.emit("join", {
  room: "stream_room",
  role: "volunteer",
  userId: "demo-user"
});
```

## 7. 开发定位建议
### 如果你要找这些入口
- 后台登录与 Cookie 鉴权：`apps/cloud-server/backend.py`
- 管理后台页面：`apps/cloud-server/static/admin_login.html` `admin_dashboard.html`
- 盲人端语音推送：`apps/cloud-server/backend.py` 中 `/api/asr/update`
- 盲人端实时连接：`apps/cloud-server/backend.py` 中 `/ws/client_input`
- 志愿者端 Socket.IO：`apps/cloud-server/signaling_server.py`
- 志愿者 App 连接配置：`apps/volunteer-app/lib/config/app_config.dart`

## 8. 注意事项
- 当前代码中部分接口存在重复或别名路由，接手时建议先统一接口规范。
- 社区与用户接口主要位于 `6000` 服务，不要只检查 `8000`。
- 生产部署前建议为所有对外接口补统一鉴权、中间件日志和限流策略。
