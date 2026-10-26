# XiaoZhi Music Service · 部署与调试指南

适用场景：在 **黑豹 X2（ARM / aarch64）** 盒子上用 Docker 跑 music-service，让小智 ESP32 设备通过 Opus 通道播放任意格式音乐。

> 核心思路：设备二进制音频通道只认 **Opus 16kHz mono**，固件里没有 MP3/FLAC/AAC 解码器。
> 因此格式兼容放在**服务端**做——music-service 用 `ffmpeg` 把任意格式实时转成 PCM 16k mono，
> 再用 `opuslib` 编码成裸 Opus 60ms 包，按 XiaoZhi `BinaryProtocol2/3` 封装后下发。
> 设备永远只看到 Opus，加新格式只要 ffmpeg 支持，MCU 一行不用改。

---

## 一、架构与数据流

```
MP3 / FLAC / AAC / WAV / m4a / ogg / 网络电台流
        │  (music-service 内 ffmpeg 实时转码)
        ▼
    Opus 16k mono  ──>  WS 二进制包  ──>  设备 Opus 解码器  ──>  喇叭
```

- 设备复用 **TTS 的 Opus 播放通道**，不是独立通道：
  - 服务端先发 JSON `{"type":"music","state":"start"}` → 设备进入 `Speaking` 状态；
  - 之后持续推 Opus 二进制包，设备当成语音播放；
  - 结束发 `{"type":"music","state":"stop"}`。
- **重要**：设备只在「音频通道打开」时（即监听 / 对话中）才收下行音频。
  正常使用时通道在用户对助手说话时打开；**独立调试要先唤醒设备（或按对话键）打开通道，再触发播放**。

### 协议包结构（与固件一致）

- `BinaryProtocol2`（version=2，大端）：
  ```
  uint16 version=2, uint16 type=0(OPUS), uint32 reserved=0,
  uint32 timestamp(ms), uint32 payload_size, uint8 payload[]  (裸 Opus 60ms@16k=960 samples)
  ```
- `BinaryProtocol3`（version=3）：`uint8 0, uint8 0, uint16 len, payload[]`。

### 转码参数（代码已固定，无需改）

| 参数 | 值 |
|---|---|
| 采样率 | 16000 Hz |
| 声道 | 1（mono） |
| 帧时长 | 60 ms（960 samples / 1920 bytes s16le） |
| Opus 码率 | 96000 bps（在 16k 内尽量好） |
| 封装 | 裸 Opus 帧（非 ogg 容器） |

---

## 二、清理黑豹 X2 上的旧 Docker 环境

### 1. 先看现状（避免误删）
```sh
docker ps -a          # 容器
docker images         # 镜像
docker volume ls      # 数据卷
docker network ls     # 网络
```

### 2. 停止并删除旧容器
```sh
docker stop $(docker ps -aq) 2>/dev/null
docker rm   $(docker ps -aq) 2>/dev/null
```

### 3. 清理悬空镜像 / 网络 / 构建缓存
```sh
docker image prune -f
docker network prune -f
docker builder prune -f
```
确认所有旧镜像都不要了，再整体清（慎重）：
```sh
docker system prune -a -f          # 删全部未使用镜像
# 确认无主数据卷也要清时再加: --volumes
```

### 4. 清理之前的挂载目录
```sh
rm -rf ~/xiaozhi/server-data ~/xiaozhi/music      # 按你之前的实际路径删
```

### 5. 确认 docker 仍在（黑豹 X2 一般已装）
```sh
docker --version && docker compose version
```
若 `docker compose` 缺失：
```sh
sudo apt update && sudo apt install -y docker.io docker-compose-plugin
sudo usermod -aG docker $USER     # 退出重登后免 sudo
```

---

## 三、把代码弄到盒子上

代码在仓库 `music-service/` 目录（已推 GitHub）。二选一：

**方式 A（推荐）：盒子直接 clone**
```sh
cd ~
git clone git@github.com:lintaoInspur/xiaozhi-esp32.git
cd xiaozhi-esp32/music-service
```

**方式 B：开发机 scp**
```sh
# 在开发机上
scp -r /home/lintao/env/AI/esp32s3/xiaozhi-esp32/music-service user@盒子IP:~/
```

### 放入音乐文件
容器内挂载的是 `./music:/music:ro`，把歌曲放进 `music/`：
```sh
mkdir -p ~/xiaozhi-esp32/music-service/music
cp /你的歌曲/*.mp3 ~/xiaozhi-esp32/music-service/music/
```

---

## 四、构建并运行

```sh
cd ~/xiaozhi-esp32/music-service
docker compose up -d --build
```
- 首次会拉 `python:3.11-slim`（多架构，ARM 可直接用）+ 装 ffmpeg/libopus，可能要几分钟。
- 检查状态：
```sh
docker compose ps
docker compose logs -f music-service        # 看有无报错
curl http://localhost:8800/list             # 应返回你放进去的歌曲列表
```
- 放行端口（局域网直连需要）：
```sh
sudo ufw allow 8800        # 或按盒子实际防火墙处理
```

---

## 五、配置设备（最小闭环：设备直连 music-service）

1. 在设备上把 **WebSocket URL** 设为：
   ```
   ws://<盒子局域网IP>:8800/
   ```
   （设备配网 / 管理后台修改，或用你之前的配置入口。`/` 与 `/ws` 都支持。）

2. **关键**：设备只有「音频通道打开」后才收下行音频。
   - 唤醒设备（说唤醒词 / 按对话键）→ 设备进入监听、打开通道；
   - 然后用 HTTP 触发播放（见下）。

---

## 六、控制命令（HTTP API）

| 命令 | 说明 |
|---|---|
| `POST /play` | 开始播放。body：`{"url":"<文件或http地址>","name":"<标题>"}` |
| `POST /stop` | 停止播放 |
| `GET /list` | 列出本地音乐库文件 |
| `GET /file/<name>` | 以 Range 方式流式返回本地文件 |

示例：
```sh
# 播本地文件（url 相对 music-dir，即 ./music 下的文件名）
curl -X POST http://<盒子IP>:8800/play -d '{"url":"/music/song.mp3","name":"测试曲"}'

# 播网络流（直接给 http/https 地址）
curl -X POST http://<盒子IP>:8800/play -d '{"url":"http://example.com/radio.m3u8","name":"电台"}'

# 停止
curl -X POST http://<盒子IP>:8800/stop

# 列出曲库
curl http://<盒子IP>:8800/list
```

> 注意：不管用 `/play` 还是自动播放，服务都会等**至少一个设备通道已打开**才开始推流；
> 所以先唤醒设备，再发 `/play`。

---

## 七、调试步骤（先跑通 Opus 通道）

### M0：离线验证转码链（不碰设备）
```sh
# 任意一首歌 -> 验证能出 16k mono PCM
ffmpeg -i song.mp3 -f s16le -ar 16000 -ac 1 - | head -c 1M > /dev/null && echo "PCM OK"
```

### M1：自动播放（最省事，无需手动触发）
启动时指定 `--auto-play`，设备一打开通道就开播。改 `docker-compose.yml` 的 `music-service` 命令：
```yaml
    command: ["python", "music_service.py", "--host", "0.0.0.0", "--port", "8800",
              "--music-dir", "/music", "--auto-play", "/music/test.mp3"]
```
然后：
```sh
docker compose up -d --build
```
唤醒设备 → 喇叭应直接出声，串口日志出现 `device audio channel open` + `streamed N frames`。

### M2：手动播放（更贴近真实控制）
`docker-compose.yml` 保持默认命令（不带 `--auto-play`），唤醒设备后：
```sh
curl -X POST http://<盒子IP>:8800/play -d '{"url":"/music/test.mp3","name":"测试曲"}'
```
预期同样出声。

### M3：停止验证
```sh
curl -X POST http://<盒子IP>:8800/stop
```
设备应停声并回到 Idle；真实环境也可由固件 MCP 工具 `self.music.stop` 触发（固件已实现）。

---

## 八、排错速查

| 现象 | 可能原因 | 排查 |
|---|---|---|
| `curl /list` 为空 | 歌曲没放进 `music/` 或挂载错 | 看 compose 里 `./music:/music:ro`；进容器 `ls /music` |
| 设备连不上 | IP 不通 / 8800 未放行 / URL 写错 | 必须是 `ws://`；`curl http://盒子IP:8800/list` 自检 |
| 唤醒了但没声音 | 设备通道没开（只在监听时收音频） | 先唤醒再 `/play`；看日志 `device audio channel open` |
| 声音断续 / 爆音 | 帧时长不对 | 代码固定 60ms，勿改；检查盒子上 ffmpeg 是否正常 |
| 杂音 / 速度异常 | 采样率非 16k | 代码固定 `-ar 16000 -ac 1`，勿改 |
| 容器起不来 | apt/ffmpeg 装失败（ARM 源问题） | `docker compose logs`；必要时换国内 apt 源 |
| 一直没进 Speaking | `music start` JSON 没到 | 看设备串口是否收到 `type:music`；确认 WS JSON 未被当 binary |
| decode 报错 | 包头字节序 | 代码用大端 / BinaryProtocol3 短头，与固件一致 |

---

## 九、接入大模型与 MCP 服务（两种路线）

要让设备真正"用语音点歌 + 聊天"，需要一个**对话/控制服务端**（提供 LLM 与 MCP）。
注意一个关键架构约束：**设备只有一条 WebSocket 连接**，同一时刻只能连一个服务端。
因此下面的"路线 A / B"决定设备最终连谁，也决定 music-service 怎么接入。

### 9.1 两条路线对比

| | 路线 A：xiaozhi.me 云端 | 路线 B：Armbian 自托管 xiaozhi-server |
|---|---|---|
| LLM | 控制台选 Qwen（你已 GitHub 认证可用） | 自己填 DashScope API Key 用 Qwen |
| MCP | 网页控制台配置 | 配置文件 `mcp_server_settings.json` |
| 本地 music-service 能否注入 Opus | ❌ 不能（连接不在你手里） | ✅ 能（服务端代发，需服务端集成） |
| 适合 | 只想聊天/控制，不care本地音乐集成 | 想把本地音乐 + Qwen 串起来 |
| 设备 WS 指向 | `wss://api.xiaozhi.me/...`（控制台给） | `ws://<armbian-ip>:8000/xiaozhi/v1/` |

> 你已能在 xiaozhi.me 用 Qwen（路线 A 已具备对话能力）。**本地音乐播放目前只能走"设备直连 music-service"（见第六/七节）调试**；
> 若要让"说一句'放首歌'"经过 Qwen 再触发本地音乐，必须走路线 B 并做服务端集成（9.4）。

### 9.2 路线 A：xiaozhi.me 云端（已认证，直接用 Qwen）

1. 浏览器打开 <https://xiaozhi.me> → 用 GitHub 登录。
2. 「我的设备」→「添加新设备」→ 输入设备播报的 6 位验证码完成绑定。
3. 配置智能体 →「模型与记忆」里选 **千问 QWen 实时（推荐）**，也可切 DeepSeek / DouBao / QWen Max。
4. MCP：在控制台「MCP / 工具」里添加你需要的 MCP 服务（云端已内置若干，或填自己的 SSE/HTTP 端点）。
5. 设备固件里的 WS 地址保持默认的 xiaozhi.me 即可（或控制台生成的专属地址）。

> 此路线下 `self.music.stop`（固件侧 MCP 工具）由云端对话通过 function call 触发，
> 但**音乐流本身无法从本地 music-service 注入云端连接**——所以纯云端路线只能控制、不能播本地曲库。

### 9.3 路线 B：Armbian 自托管 xiaozhi-server（含 Qwen + MCP）

仓库：`xinnan-tech/xiaozhi-esp32-server`（Python + Docker，ARM/aarch64 原生支持）。

#### 9.3.1 获取并启动
```sh
cd ~
git clone https://github.com/xinnan-tech/xiaozhi-esp32-server.git
cd xiaozhi-esp32-server/main/xiaozhi-server

# 准备数据目录与配置模板
mkdir -p data
cp config.yaml data/.config.yaml          # 之后改这个 data/.config.yaml

# （可选）本地 ASR 模型 SenseVoiceSmall，放 models/SenseVoiceSmall/model.pt
# 不下载则需在 config 里改用云端 ASR（如讯飞流式）

docker compose up -d
docker compose logs -f                    # 看到 WebSocket / OTA 地址即就绪
```
- 控制台（智控台）：浏览器开 `http://<armbian-ip>:8002`，注册管理员、建智能体。
- 设备 WS 地址：`ws://<armbian-ip>:8000/xiaozhi/v1/`（OTA：`http://<armbian-ip>:8002/xiaozhi/ota/`）。

#### 9.3.2 配置通义千问（DashScope）
在 `data/.config.yaml` 里把 LLM 指向阿里云百炼（需先到 <https://dashscope.aliyuncs.com> 申请 API Key）：

```yaml
llm:
  provider: "qwen-flash"                  # 阿里百炼实时模型
  api_key: "sk-你的DashScopeKey"
  base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
  model_name: "qwen-turbo"                # 或 qwen-plus / qwen-max
```
> 不同版本字段名略有差异（也有 `QwenLLM:` / `DashScopeLLM:` 或 `llm.config.api_key` 写法）。
> **以仓库里 `config.yaml` 自带的中文注释为准**，上面是常见形态。改完 `docker compose restart`。

#### 9.3.3 配置 MCP 工具
MCP 服务端连接写在 `mcp_server_settings.json`（首次把仓库根目录的同名文件复制到 `data/` 并重命名为 `.mcp_server_settings.json`）：

```json
{
  "mcpServers": {
    "Home Assistant": {
      "command": "mcp-proxy",
      "args": ["http://YOUR_HA_HOST/mcp_server/sse"],
      "env": { "API_ACCESS_TOKEN": "YOUR_TOKEN" }
    }
  }
}
```
- 每个条目 = 一个外部 MCP 服务（command/args 本地进程，或 SSE/HTTP 远程）。
- 设备**侧**工具（如 `get_time`、`get_weather`，以及我们固件里的 `self.music.stop`）由服务端 `plugins_func/functions/` 注册，靠 LLM 的 function calling 触发，无需在此列。

#### 9.3.4 设备指向本地服务端
把设备固件/配网的 WebSocket URL 改成 `ws://<armbian-ip>:8000/xiaozhi/v1/`，重启设备即可走本地 Qwen + MCP。

### 9.4 既能 AI 对话、又能播本地音乐？（结论）

**能做到，但前提是"设备只连一个服务端"。** 设备只有一条 WebSocket，不能一边连 xiaozhi-server 聊 Qwen、一边连 music-service 播歌。正确架构是：

```
         设备(单条 WS)
             │  ws://<盒子IP>:8000/xiaozhi/v1/
             ▼
      xiaozhi-server  ──(AI 对话 / MCP / TTS，LLM=Qwen)
             │  放歌指令时：
             │   GET http://music-service:8800/opus_stream?url=...  ← 拉裸 Opus 帧
             │   然后 music start + 逐帧封装 BinaryProtocol2/3 → 代发到设备同一条 WS
             ▼
      music-service（内部转码后端，不直接连设备）
             │  ffmpeg 任意格式 → PCM 16k mono → opuslib → 裸 Opus 帧
             ▼
          本地曲库 ./music 或网络流
```

- **AI 对话 / MCP**：全部走 xiaozhi-server（Qwen），设备感知不到 music-service 的存在。
- **本地音乐**：由 xiaozhi-server 在收到"放歌"意图/工具调用后，从 music-service 拉流并**复用同一条设备连接**下发。
- 因此"对话"和"播歌"共用一条连接，互不冲突——**两者兼得**。

> 关键点：music-service 已新增 `/opus_stream` 后端端点（见 `music_service.py`），只吐裸 Opus 帧；
> 设备握手、`music start/stop`、帧的时间戳封装都由 xiaozhi-server 负责。两者通过 `xiaozhi-net` 内部网络通信。

### 9.5 全栈编排与集成（落地步骤）

#### 9.5.1 编排文件
仓库里现在有两份 compose：
- `docker-compose.yml`：**全栈**。设备连 xiaozhi-server(8000)，music-service 仅在内部网络给 xiaozhi-server 拉流（不对外暴露设备 WS）。
- `docker-compose.debug.yml`：**仅 music-service**，设备直连它(`ws://<IP>:8800/`)，用于先单独调通音频链路（无 LLM）。

使用前先把服务端克隆到同级目录，供全栈 compose 的构建上下文使用：
```sh
cd ~/xiaozhi-esp32
git clone https://github.com/xinnan-tech/xiaozhi-esp32-server.git
```
全栈启动（设备最终指向 xiaozhi-server）：
```sh
cd ~/xiaozhi-esp32/music-service
docker compose up -d --build
# 设备 WS: ws://<盒子IP>:8000/xiaozhi/v1/   控制台: http://<盒子IP>:8002
```
> `docker-compose.yml` 里 `xiaozhi-server` 的 `build` 上下文随版本变化，请以官方仓库 docker-compose 为准；
> 若官方提供镜像，把 `build` 换成 `image:` 即可。

#### 9.5.2 仍需补的"服务端集成"代码（在 xiaozhi-server 仓库侧）
全栈 infra 已就绪，最后一步是让 xiaozhi-server 真的去拉流并代发。需在其代码里加一个音乐模块（伪代码，落地时按该仓库结构放置，例如 `plugins/music/` 或自定义 `handle`）：

```python
# 在 xiaozhi-server 侧（示意）：收到 LLM 的 play_music(url,name) 工具调用后
import aiohttp, struct

MUSIC = "http://music-service:8800/opus_stream"

async def play_music(conn, url: str, name: str):
    await conn.send_json({"type": "music", "state": "start", "name": name})
    ts = 0
    async with aiohttp.ClientSession() as s:
        async with s.get(MUSIC, params={"url": url}) as r:
            async for frame in r.content.iter_chunked(4096):   # 每个 chunk = 一个裸 Opus 包
                # 用设备协商的协议版本封装（version 2 大端 / version 3 短头）
                pkt = struct.pack(">HHIII", 2, 0, 0, ts, len(frame)) + frame
                await conn.send_bytes(pkt)
                ts += 60
                await asyncio.sleep(0.06)
    await conn.send_json({"type": "music", "state": "stop"})

# 对应注册一个 MCP/function 工具 play_music(url,name) 与 stop_music()，
# 并复用固件已有的设备侧 MCP 工具 self.music.stop 做"语音停止"。
```
- `conn` 即 xiaozhi-server 持有的设备 WebSocket 连接（同一连接既跑对话又跑音乐）。
- 设备侧 `self.music.stop` 已在我们固件 `mcp_server.cc` 实现，可作为"语音停止"入口。

> 这一步是**跨仓库**的最后一块拼图：infra（两份 compose + `/opus_stream`）已齐，
> 只差把上面这段接入 xiaozhi-server 的请求处理链。调通 9.5.1 的音频链路（debug compose）后，再做此集成。

---

## 十、关键文件

| 文件 | 作用 |
|---|---|
| `music_service.py` | 主服务：WS 协商 + ffmpeg 转码 + opuslib 编码 + 下发 |
| `Dockerfile` | 基于 `python:3.11-slim`，装 ffmpeg/libopus |
| `requirements.txt` | `aiohttp`、`opuslib` |
| `docker-compose.yml` | 一键编排（含 xiaozhi-server 占位） |
