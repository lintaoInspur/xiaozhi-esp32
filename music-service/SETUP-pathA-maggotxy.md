# 路径 A：Maggotxy 音乐固件 + 音乐 MCP（小白向，免写注入代码）

> 本文档是「路径 A」的**完整、照着做**版本。目标：不写任何后端注入代码，让设备能语音点歌/播放。
> 选型、优缺点、与路径 B 的区别见 `SETUP-xiaozhi-client.md`（索引）。
> 凡标注「⚠️ 待核实」的，请以官方仓库 / 实际下载的 `docker-compose.yml` 为准。

---

## 〇、A 路径速览（执行清单）

1. **清理旧 Docker** → 见「二」。
2. **放代码 / 取桥接** → 见「二」。路径 A 用在线音乐，**不需要**本地曲库 `music/`。
3. **起 xiaozhi-client 桥接** → 见「三」。Web UI `9999`、设备端口 `3000`（以 `docker compose ps` 为准）。
4. **配置 AI** → 见「四」。填 Qwen(DashScope) / 字节 ASR/TTS 的 Key。
5. **设备指向桥接** → 见「五」。WS 改 `ws://<盒子IP>:3000/`，重启，看"已连接"。
6. **选固件并刷机** → 见「六」。A1 刷 Maggotxy 音乐固件；A2 用标准固件 + 自托管音乐 MCP。
7. **挂音乐 MCP + 写提示词** → 见「七 / 八」。
8. **验收** → 见「九」。说"播放青花瓷"→ 固件自播；"停止"→ 停。

> ⚠️ **本机约束提醒（2MB PSRAM）**：A1 在**设备端**解 MP3，对 RAM/供电紧张的板子有压力（见「十」）。若你板子确认 2MB PSRAM，请认真读「十」再决定；供电极稳才用 A1。

---

## 一、方案原理

- 设备连 **xiaozhi-client 桥接**（设备侧 WS 由它终结），桥接再去连上游小智接入点（xiaozhi.me 或自托管），上游提供 ASR→LLM→TTS。
- 路径 A 的核心是：**固件自己会播**。用 `Maggotxy/xiaozhi-esp32-music` 固件，它新增 `self.music.play_song` 工具；LLM 调它后，**固件自己下载音频并用 `mp3dec`(minimp3) 解 MP3 直接播放**——完全不需要服务端写帧注入代码。
- 音乐源由**音乐 MCP 服务**提供（在线音乐 API），不是盒子本地 mp3：
  - **A1**：依赖 Maggotxy 的音乐后端（"为爱发电"）+ v1.8.5 起的设备 MAC 授权，最简单。
  - **A2**：标准固件 + `xiaozhi-mcp-music`，音乐源用你自己的 API Key（酷我/网易云/咪咕/酷狗），不依赖第三方音乐后端。

```
设备(单条 WS, 连 xiaozhi-client 桥接)
   │
   ▼
xiaozhi-client 桥接（轻量 Node）
   │  ① 终结设备 WS，上游走 ASR→LLM(Qwen)→TTS
   │  ② 把"音乐 MCP 工具"透传给 LLM（play_song 等）
   ▼
LLM 调 self.music.play_song → 设备端自己下载 + 解 MP3 + I2S 播放  （A 的关键：播放在设备端）
```

---

## 二、共享前置准备（盒子 + 桥接 + AI + 设备指向）

> A、B 两份文档共用这一段。路径 A 不需要 music-service 转码后端，但桥接与 AI 都要。

### 2.1 清理旧 Docker（按需）
```sh
docker stop $(docker ps -aq) 2>/dev/null; docker rm $(docker ps -aq) 2>/dev/null
docker image prune -f; docker network prune -f; docker builder prune -f
docker --version && docker compose version    # 确认 docker 在
```

### 2.2 获取代码
```sh
cd ~
git clone git@github.com:lintaoInspur/xiaozhi-esp32.git
cd xiaozhi-esp32/music-service
# 路径 A 用在线音乐，不需要本地曲库；建个空目录也无妨
mkdir -p music
```

### 2.3 起 xiaozhi-client 桥接（Docker，支持 arm64）
镜像 `shenjingnan/xiaozhi-client:latest` 官方提供，**支持 arm64**（黑豹 X2 / 树莓派可跑），约 490MB(压缩)/1.4GB(运行)。

取官方 compose（⚠️ 端口/服务名以实际下载为准）：
```sh
curl -O https://raw.githubusercontent.com/shenjingnan/xiaozhi-client/main/docker-compose.yml
docker compose up -d
docker compose logs -f
```
或本仓库已附的组合（与 music-service 同网络）：
```sh
cd ~/xiaozhi-esp32/music-service
docker compose -f docker-compose.xiaozhi-client.yml up -d --build
```

已知端口/路径（⚠️ 待核实）：
| 项 | 值 |
|---|---|
| Web UI | `9999` → `http://<盒子IP>:9999` |
| 后端 / 设备 WebSocket | `3000`（疑似设备连接口；**务必用 `docker compose ps` 核实**） |
| 配置持久化挂载 | `-v ~/xiaozhi-client:/workspaces` |
| 默认管理员 | 账号 `admin` / 密码 `xiaozhi123`（建议改掉） |

### 2.4 配置 AI（Qwen + ASR/TTS）
`llm` 走 OpenAI 兼容接口（Web UI 或 `xiaozhi.config.json`）：
```json
{
  "llm": {
    "provider": "openai",
    "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "apiKey": "sk-你的DashScopeKey",
    "model": "qwen-plus"
  }
}
```
> ⚠️ 字段名以官方 `xiaozhi.config.json` / Web UI 为准。DashScope Key 在 <https://dashscope.aliyuncs.com> 申请。
> ASR/TTS 默认字节跳动（火山引擎），需 `app-id`/`token` 等凭证。

### 2.5 设备指向桥接（WebSocket 地址）
把设备固件/配网的 WebSocket URL 改成桥接设备端口：
```
ws://<盒子IP>:3000/        # 端口以 2.3 核实结果为准
```
- 默认 URL 由 xiaozhi.me 激活/OTA 下发；改法三选一：① 自托管 OTA 让设备指向你的桥接；② 设备本地配置后台改"服务器地址"；③ 编译期改 `CONFIG_OTA_URL` 或直写 NVS `websocket.url`。
- 重启设备 → Web UI 看"已连接" → 唤醒说话，应能走 Qwen 对话（先验证对话，再验证音乐）。

> ⚠️ 若设备连不上：确认 2.3 的设备端口；`docker compose logs -f` 看握手；URL 必须 `ws://` 且路径正确。

---

## 三、选固件（A1 / A2）

- **A1（最快上手，有依赖）**：用 `Maggotxy/xiaozhi-esp32-music`。`self.music.play_song` 在**设备端**自己下载并用 `mp3dec` 解 MP3 播放，依赖 Maggotxy 音乐后端与 MAC 授权。
- **A2（开放/自托管，推荐长期）**：用你现在的官方固件（需固件支持 MCP 资源播放；近版官方固件支持 `resource://read_from_http` 边读边播）。音乐源用你自己配的 API Key，不依赖第三方音乐后端。若你的固件没有该能力，A2 不适用，退回 A1 或改走路径 B。

---

## 四、A1：刷 Maggotxy 音乐固件（小白向）

1. **获取固件**：
   - 方式一（推荐）：到仓库 Release / 文档找**预编译 `.bin`**，用小智官方网页烧录工具（<https://xiaozhi.me>）或 `esptool` 烧录（务必选对你开发板，如黑豹 X2 对应板型）。
   - 方式二（自编译）：`git clone https://github.com/Maggotxy/xiaozhi-esp32-music.git` → 按官方 `78/xiaozhi-esp32` 的 ESP-IDF 教程 `idf.py set-target` / `build` / `flash`。
2. **配网激活**：设备连 WiFi、激活（连 xiaozhi.me 官方即可拿到 Qwen 对话能力）。
3. ⚠️ **授权**：v1.8.5 起部分功能需用设备 MAC 去项目 QQ 群（826072986）获取授权，按仓库 README 操作；先确认未授权是否仍可播，别卡在授权上。

---

## 五、A2：标准固件 + xiaozhi-mcp-music（自托管音乐源）

在**盒子**或任意联网机器上跑这个 MCP（它主动连小智 MCP 接入点）：
```sh
git clone https://github.com/ABUGG-007/xiaozhi-mcp-music.git
cd xiaozhi-mcp-music
pip install -r requirements.txt
```
编辑 `.env`：
```
XIAOZHI_MCP_ENDPOINT=wss://api.xiaozhi.me/mcp/?token=你的智能体token   # 从小智控制台"MCP接入点"复制
MUSIC_SOURCES=kuwo,netease,migu,kugou
LOCAL_PLAYER=none
```
启动：`python bridge.py`（看到 `Successfully connected to WebSocket server` 即连上；每 60s 心跳）。
- 若你用 **xiaozhi-client 桥接**而非直连 xiaozhi.me：把该 MCP 作为桥接的 MCP 子进程接入（Web UI 添加 stdio 类型，命令指向仓库里的 `music_mcp` 入口），或直接用上面 `bridge.py` 连 xiaozhi.me。
- 备选音乐 MCP：`willisfeng/xiaozhi-music-mcp`（免费音乐 MCP 服务器，按官方方式接入）。

---

## 六、音乐 MCP 接入（把 MCP 挂到对话里）

两种挂法，选一：
- **挂到 xiaozhi.me 控制台**：登录 → 对应智能体"配置角色" → 右下角"MCP 接入点" → 添加。
  - willisfeng 版（stdio，最简单）配置示例：
    ```json
    {"name":"免费音乐服务器","command":"python3","args":["/绝对路径/music_mcp_server.py"],"transport":"stdio"}
    ```
  - 或用 五 的 `bridge.py`（WebSocket 接入点）方式。
- **挂到 xiaozhi-client 桥接**：在桥接 Web UI（`http://<盒子IP>:9999`）的 MCP 页添加同样的 stdio/SSE 配置，桥接会把工具透传给设备。

---

## 七、提示词（关键，避免大模型乱用工具）

在智能体"人物介绍/角色配置"末尾加上（按你实际用的工具名调整）：
```
收到音乐相关的需求时，只使用 MCP 工具 self.music.play_song（或你 MCP 提供的播放工具）来播放，禁止使用 search_music 功能。
```
Maggotxy 社区原话：*"选择 DeepSeekV3 大语言模型，在人物介绍中填入：收到音乐相关的需求时，只使用 MPC tool self.music.play_song 工具，同时禁止使用 search_music 功能。"*

---

## 八、验收（闭环）

1. 设备在线、桥接"已连接"。
2. 语音："播放青花瓷" / "来一首周杰伦的歌" → 应自动搜歌并播放；屏幕（若有）显示歌名/歌词或频谱。
3. 语音："停止播放" → 停。
4. 若无声：看设备日志/桥接日志；确认 MCP 已连、提示词已加、工具名匹配。

---

## 九、排错速查

| 现象 | 排查 |
|---|---|
| Web UI 打不开 | 端口 9999 未映射 / 防火墙；`docker compose ps` |
| 设备连不上后端 | 设备端口(2.3)核实；后端日志看握手；URL 必须 `ws://` |
| 对话无声 / 无回复 | Qwen Key、ASR/TTS Key 是否填对；看后端日志 |
| 音乐不出声 | MCP 是否连上；提示词是否加了"只用 play_song"；工具名是否匹配 |
| A1 授权卡住 | 去 Maggotxy QQ 群(826072986) 用 MAC 取授权，或先确认未授权能否播 |
| 容器起不来 | 镜像拉取失败（arm64 用官方 latest）；`docker compose logs` |

---

## 十、设备资源与性能权衡（2MB PSRAM 板子）

- **RAM 不是主矛盾**：Maggotxy 用 minimp3（`mp3dec.h`）解码，解码器状态只有几 KB；真正占内存的是音频缓冲（默认上限 256KB，放在 PSRAM，可降到 32KB）。即便只有 2MB PSRAM，256KB 也只占约 13%。
- **真正吃力的是 CPU + 供电并发**：HTTP 下载 + MP3 解码（独立线程）+ I2S 播放（独立线程）+ WiFi 同跑。Maggotxy 源码把缓冲从更大值降到 256KB/32KB，注释明确写"降低以减少 brownout 风险"——**brownout 是电压跌落（供电/功耗），不是内存**；弱供电板会重启/掉线。
- **先确认板子规格**：看上电日志 `SPI RAM` 行，或 `sdkconfig` 的 `CONFIG_SPIRAM_SIZE`。
- **本机结论（已确认 2MB PSRAM）**：A1 在 2MB 上还要独占 256KB 缓冲 + MP3 解码线程，风险高；若供电不是极稳，**更稳妥的是路径 B（见 `SETUP-pathB-opus.md`）**——它把解码全放盒子，设备只解 Opus，几乎不新增内存。A1 仅作"愿承担 brownout 风险且供电极稳"时的备选。
- **仍坚持 A1**：务必给足供电（独立 5V/≥2A）、确认 PSRAM 已开启，必要时把 `MAX_BUFFER_SIZE` 调更小以缓解 brownout。

---

## 十一、把 play_song 移植进本仓库固件（进阶，非小白）

若你**不想刷 Maggotxy、想在自己这份固件里加 play_song**，需移植以下改动（已对照 Maggotxy 源码确认）：
- **新增文件**：`main/boards/common/music.h`（Music 抽象类：`Download` / `StartStreaming` / `StopStreaming`…）、`esp32_music.h`、`esp32_music.cc`（基于 `mp3dec` 的设备端 MP3 下载+解码+播放，约 52KB，含歌词/频谱显示）。
- **改 `main/mcp_server.cc`**（参考 Maggotxy `mcp_server.cc:108-131`）：
  ```cpp
  auto music = board.GetMusic();
  if (music) {
      AddTool("self.music.play_song", "播放指定的歌曲…",
          PropertyList({Property("song_name", kPropertyTypeString),
                        Property("artist_name", kPropertyTypeString, "")}),
          [music](const PropertyList& p) -> ReturnValue {
              if (!music->Download(p["song_name"].value<std::string>(),
                                  p["artist_name"].value<std::string>()))
                  return "{\"success\": false, \"message\": \"获取音乐资源失败\"}";
              return "{\"success\": true, \"message\": \"音乐开始播放\"}";
          });
  }
  ```
- **改 `main/boards/common/board.h/.cc`**：加 `virtual Music* GetMusic() { return nullptr; }` 及具体板实现。
- **改 `main/application.cc`**：把音乐播放接入音频输出（参考 Maggotxy 的 `application.cc` 改动）。
- ⚠️ 这是**真实固件开发任务**：要 ESP-IDF 编译、处理 `mp3dec` 依赖与采样率；且 Maggotxy 音乐后端授权可能影响可用性。**小白建议直接刷 Maggotxy 预编译固件（A1）**，不要自己移植。
