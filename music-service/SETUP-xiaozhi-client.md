# XiaoZhi Client 方案 · 环境配置与调试指南

> 本文档基于 **`shenjingnan/xiaozhi-client`**（Node/TS，MIT）实现"AI 对话 + 本地音乐"完整流程。
> 旧方案（Python 版 xiaozhi-server）见 `SETUP.md`，本文**不改动**它。
> 本文档中凡是标注「⚠️ 待核实」的，请以其官方文档 / 实际下载的 `docker-compose.yml` 为准。

---

## 〇、部署步骤速览（先读这份，黑豹 X2 实战清单）

xiaozhi-client 是一个**桥接应用**：设备连它（设备侧 WS 由它终结），它再去连上游小智接入点（xiaozhi.me 云端 或 自托管），并在设备侧注入本地音乐。按以下顺序执行，每步都有详细章节：

1. **清理旧 Docker 环境** → 见「二、2.1」。停/删旧容器与悬空镜像，确认 `docker` 在。
2. **放代码 + 曲库** → 见「二、2.2」。clone 仓库，把 mp3 放进 `music/`。
3. **起 music-service（转码后端）** → `docker compose -f docker-compose.debug.yml up -d --build`，先验证 `/list` 与 ffmpeg 转码（M1）。
4. **起 xiaozhi-client（桥接）** → `docker compose -f docker-compose.xiaozhi-client.yml up -d --build`（或官方 compose）；`docker compose ps` 核实设备端口（3000）与 Web UI（9999）。
5. **配置桥接的上游接入点 + AI** → 见「四」。Web UI 填：上游 xiaozhi.me（或自托管）、Qwen(DashScope) / 字节 ASR/TTS 的 Key。
6. **配置本地音乐注入** → 见「六」。让桥接在收到"放歌"意图时拉 `music-service:8800/opus_stream` 并注入设备（需补集成代码，见 6.2）。
7. **设备指向桥接** → WS 改 `ws://<盒子IP>:3000/`，重启；Web UI 看"已连接"。
8. **验收** → 唤醒对话走 Qwen；说"放首歌"→ 桥接注入 Opus → 设备出声；"停止"→ 停（M2/M3）。

> **单连接约束**：设备只连桥接一个端点；AI 对话与本地音乐都由桥接统一调度，不会冲突。

---

## 一、为什么转向 xiaozhi-client

`xiaozhi-client` 是一个**桥接应用（bridge）**：它一端**终结 ESP32 设备的 WebSocket**（设备连它），
另一端**去连上游小智接入点**（xiaozhi.me 云端 或 自托管 xiaozhi-server），在中间做协议转译 + 能力增强。

- **设备侧**：设备只连 xiaozhi-client，由它把音频/指令转给上游，并把上游的 Opus 回传设备。
- **上游接入点**：提供真正的 ASR → LLM → TTS。可以是 xiaozhi.me（云端，你已 GitHub 认证可用 Qwen），
  也可以是自托管服务端（你自填 DashScope/字节等 Key）。
- **能力增强（关键）**：因为桥接**拥有设备侧连接**，它可以把本地能力直接注入设备流——
  例如本地音乐（music-service 转码后的 Opus）可以由桥接直接发到设备，**不依赖接入点是否支持本地音乐**。
  此外桥接还擅长 MCP（可视化增删工具）。

⚠️ 注意：这里的"本地"指**桥接部署在盒子上**，并不等于"在盒子上跑大模型"。
实际的 ASR / LLM / TTS **全部走云端 API**（LLM=通义千问 DashScope、ASR/TTS=字节跳动等），
盒子只做协议转发 + 音频转码，**不需要 GPU、不加载模型权重**，因此适合黑豹 X2 这类资源受限盒子。

我们的目标（AI 对话 + 本地音乐）走**桥接模式**：设备只连 xiaozhi-client（桥接），
本地音乐由 music-service 转码、被桥接在设备侧连接上**直接注入**；AI 对话则由桥接上游的接入点（xiaozhi.me 或自托管）提供。

> **资源可行性（针对黑豹 X2 这类资源受限盒子）**：桥接模式 不要求盒子本地推理。
> - LLM / ASR / TTS 都是**云端 API 调用**（Qwen-DashScope、字节跳动…），盒子不加载任何模型权重、不需要 GPU。
> - 盒子本地真正干的活只有两件：① xiaozhi-client 后端做 WebSocket 协议与流程编排（轻量 Node 进程）；
>   ② music-service 用 ffmpeg 把音乐转成 16k 单声道 PCM→Opus（单路转码，CPU 占用中等，ARM 盒子可承受）。
> - 因此"盒子跑不了大模型"完全不影响本方案——它本来就只当**网关 + 转码器**，重活在云上。

```
         设备(单条 WS, 连 xiaozhi-client 桥接)
             │
             ▼
   xiaozhi-client 桥接（部署在盒子，轻量 Node）
             │  ① 设备侧：终结设备 WS，把音频/指令转给上游、把上游 Opus 回传设备
             │  ② 上游接入点(云端)：ASR(字节跳动) → LLM(Qwen/DashScope) → TTS(字节跳动)
             │  ③ 本地音乐注入（桥接拥有设备侧连接，可直接发）：
             │       GET http://music-service:8800/opus_stream?url=...  ← 拉裸 Opus 帧
             │       再 music start + 逐帧封装 → 发到设备同一条 WS
             ▼
   music-service（内部转码后端，不直接连设备）
             │  ffmpeg 任意格式 → PCM 16k mono → opuslib → 裸 Opus 帧
           本地曲库 ./music 或网络流
```

---

## 二、环境准备（黑豹 X2 / ARM）

### 2.1 清理旧 Docker（与 SETUP.md 同，按需执行）
```sh
docker stop $(docker ps -aq) 2>/dev/null; docker rm $(docker ps -aq) 2>/dev/null
docker image prune -f; docker network prune -f; docker builder prune -f
# 可选整体清： docker system prune -a -f
docker --version && docker compose version    # 确认 docker 在
```

### 2.2 获取 music-service 代码
```sh
cd ~
git clone git@github.com:lintaoInspur/xiaozhi-esp32.git
cd xiaozhi-esp32/music-service
mkdir -p music && cp /你的歌曲/*.mp3 music/
```

---

## 三、安装并启动 xiaozhi-client（Docker，支持 arm64）

镜像 `shenjingnan/xiaozhi-client:latest` 官方提供，**支持 arm64**（可在黑豹 X2 / 树莓派跑），
镜像约 490MB（压缩）/ 1.4GB（运行）。

### 3.1 取官方 compose（⚠️ 待核实端口与服务名）
> 下面这份是**按搜索到的官方片段整理**的；请以你盒子实际能下载到的官方 `docker-compose.yml` 为准：
```sh
curl -O https://raw.githubusercontent.com/shenjingnan/xiaozhi-client/main/docker-compose.yml
# 若 GitHub 不通，用 Gitee 镜像（路径可能不同，按官方说明）：
# curl -O https://gitee.com/shenjingnan/xiaozhi-client/raw/main/docker-compose.yml
docker compose up -d
docker compose logs -f
```

### 3.2 已知端口 / 路径（来自官方片段，⚠️ 待核实）
| 项 | 值 |
|---|---|
| Web UI | `9999` → `http://<盒子IP>:9999` |
| 后端 / 设备 WebSocket | `3000`（官方 compose 标为"备用端口"，疑似设备连接口；**务必用 `docker compose ps` 或官方 compose 核实**） |
| 配置持久化挂载 | `-v ~/xiaozhi-client:/workspaces`（配置落在 `/workspaces`） |
| 默认管理员 | 账号 `admin` / 密码 `xiaozhi123`（Web UI 登录，建议改掉） |

### 3.3 组合部署（xiaozhi-client + music-service 同一网络）
仓库已附 `docker-compose.xiaozhi-client.yml`，直接用它：
```sh
cd ~/xiaozhi-esp32/music-service
docker compose -f docker-compose.xiaozhi-client.yml up -d --build
```
- 设备最终连 xiaozhi-client 的设备端口（见 3.2）；music-service 仅在 `xiaozhi-net` 内部给 xiaozhi-client 拉流。
- 想单独先调音乐，用 `docker-compose.debug.yml`（设备直连 music-service）。

---

## 四、基础配置

### 4.1 LLM 用通义千问（Qwen / DashScope）
xiaozhi-client 的 `llm` 走 **OpenAI 兼容**接口，可在 Web UI 或 `xiaozhi.config.json` 配置：
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
> ⚠️ 字段名（baseUrl / apiKey / model 等）以官方 `xiaozhi.config.json` 结构与 Web UI 为准。
> DashScope Key 在 <https://dashscope.aliyuncs.com> 申请。

### 4.2 ASR / TTS（默认字节跳动，需 Key）
后端默认 ASR/TTS 走**字节跳动（火山引擎）**，需要 `app-id` / `token` 等凭证，在 Web UI 或配置文件填。
若不想要字节跳动，可改用其他受支持的引擎（具体以官方文档为准）。

### 4.3 MCP（它的强项）
- Web UI（`http://<盒子IP>:9999`）里**可视化**增删改 MCP 服务，支持 stdio / SSE / StreamableHTTP。
- 内置 `datetime-mcp`、`calculator-mcp`；可加自定义/远程 MCP。
- 还能按设备开关单个工具，避免工具过多拖慢大模型。
- 配置命令（CLI 方式，参考）：`xiaozhi config set mcpEndpoint ""`（接入点留空=本地自托管模式 A）。

---

## 五、设备指向 xiaozhi-client（模式 A）

把设备固件/配网的 WebSocket URL 改成后端设备端口，例如：
```
ws://<盒子IP>:3000/        # 端口以 3.2 核实结果为准
```
重启设备 → 在 Web UI 看连接状态变"已连接" → 唤醒说话，应能走 Qwen 对话。

> ⚠️ 若设备连不上：先确认 3.2 的设备端口；用 `docker compose logs -f` 看后端是否收到握手；
> 确认设备 URL 必须是 `ws://` 且路径正确（路径以官方协议为准）。

---

## 六、本地音乐集成（music-service 作为转码后端）

### 6.1 music-service 已具备的能力
- 新增端点 `GET /opus_stream?url=<文件或http地址>`：以 **HTTP chunked** 持续吐**裸 Opus 帧**（每 chunk 一帧）。
- 设备握手、`music start/stop`、帧的 BinaryProtocol 封装，由 xiaozhi-client 后端负责。

### 6.2 仍需补的"服务端集成"代码（在 xiaozhi-client 后端，TypeScript）
全栈 infra 已就绪，最后一步是让 xiaozhi-client 后端在收到"放歌"意图/工具调用时，
拉 `/opus_stream` 并逐帧发给设备（伪代码，落地按其后端结构写）：
```ts
// 在 xiaozhi-client 后端（收到 play_music(url,name) 意图后）
async function playMusic(conn, url, name) {
  await conn.sendJson({ type: "music", state: "start", name });
  let ts = 0;
  const res = await fetch(`http://music-service:8800/opus_stream?url=${encodeURIComponent(url)}`);
  const reader = res.body.getReader();
  while (true) {
    const { done, value } = await reader.read();   // value = 一个裸 Opus 包
    if (done) break;
    const pkt = buildBinaryProtocol2(ts, value);   // 大端 HHIII + payload（或 v3 短头）
    await conn.sendBytes(pkt);
    ts += 60;
    await sleep(60);
  }
  await conn.sendJson({ type: "music", state: "stop" });
}
```
- 同时注册 `play_music` / `stop_music` 工具（或在 Web UI 配 MCP）；
- 设备侧已有的 MCP 工具 `self.music.stop`（固件实现）可作"语音停止"。

> ⚠️ **关键核实**：你的固件 `esp_opus_dec` 要的是 **BinaryProtocol 里的裸 Opus 帧**。
> 务必确认 xiaozhi-client 后端 TTS 回传设备的也是**裸 Opus**（而非 ogg 容器），否则设备解不出声。
> 这条是头号验收项。

---

## 七、调试步骤

### M0：离线验证转码链（不碰设备）
```sh
ffmpeg -i song.mp3 -f s16le -ar 16000 -ac 1 - | head -c 1M > /dev/null && echo "PCM OK"
```

### M1：先单独调通音乐（设备直连 music-service）
```sh
docker compose -f docker-compose.debug.yml up -d --build
# 设备 WS 指向 ws://<盒子IP>:8800/
curl -X POST http://<盒子IP>:8800/play -d '{"url":"/music/test.mp3","name":"测试曲"}'
# 唤醒设备 → 应出声
```

### M2：调通 xiaozhi-client 对话（设备连它）
- 启动 `docker-compose.xiaozhi-client.yml` 或官方 compose；
- 填好 Qwen（4.1）与 ASR/TTS（4.2）；
- 设备 WS 指向 3.2 端口 → 唤醒对话，确认能走 Qwen 回话。

### M3：音乐集成（做完 6.2 后）
- 设备连 xiaozhi-client；触发放歌意图 → 后端拉 `/opus_stream` 代发 → 设备出声；
- `self.music.stop` 或停止指令应能停。

---

## 八、排错速查

| 现象 | 排查 |
|---|---|
| Web UI 打不开 | 端口 9999 未映射 / 防火墙；`docker compose ps` |
| 设备连不上后端 | 设备端口(3.2)核实；后端日志看握手；URL 必须 `ws://` |
| 对话无声 / 无回复 | Qwen Key、ASR/TTS Key 是否填对；看后端日志报错 |
| 音乐不出声 | 见 6.2 的 Opus 裸帧核实；`/opus_stream` 能否单独访问 |
| 音乐与对话冲突 | 确认设备只连 xiaozhi-client 一个端点（单连接约束） |
| 容器起不来 | 镜像拉取失败（arm64 用官方 latest）；`docker compose logs` |

---

## 十、ESP32 固件侧配置

> 基于本仓库 `xiaozhi-esp32` 固件代码核对（不是凭记忆）。结论：**音乐播放能力固件已内置，无需改代码**；设备侧只需把 WebSocket 地址从默认的 xiaozhi.me 改成指向桥接。

### 10.1 固件已内置的能力（不用动代码）

- 设备收到 `{"type":"music","state":"start"}` → 进入 Speaking 态并播放后续 Opus 帧；`"stop"` → 停止：
  `main/application.cc:668-687`
- `StopMusic()` 实现：`main/application.cc:1259`
- 语音停止工具 `self.music.stop`（LLM/桥接调用即可停）：`main/mcp_server.cc:329-334`
- Speaking 态下自动把收到的二进制帧当 Opus 解码播放（`websocket_protocol.cc` 的 `OnData`）。

> 即"播放本地音乐"链路，固件侧**已齐备**，只差服务端把 Opus 推过来。

### 10.2 你需要在设备上改的：WebSocket 地址

设备连哪个服务器，存在 NVS 的 `websocket` 命名空间（`url` / `token` / `version`），代码这样读：
`main/protocols/websocket_protocol.cc:79-83`

默认这三项由 **xiaozhi.me 激活/OTA 下发**（`main/ota.cc:180-196`），所以官方/出厂固件默认连 xiaozhi.me。

**把它改成指向桥接**（`ws://<盒子IP>:3000/`，端口以 `docker compose ps` 为准），三种方法任选：

1. **自托管 OTA（最稳，推荐）**：让设备激活指向你自己的 OTA 服务（如 `xiaozhi-esp32-server` 自带的 OTA），由它返回的 `websocket` 段写成你的桥接地址。这是小智官方自托管的标准做法。
2. **设备本地配置后台**：设备连上 WiFi 后，浏览器打开设备 IP 的配置页，在"服务器地址"里改 WebSocket URL（部分构建带此页面，以你烧录的固件为准）。
3. **编译期 / NVS 直写**：改 `CONFIG_OTA_URL`，或直接写 NVS `websocket.url`。

### 10.3 协议版本

设备 hello 带 `version` 字段；`music-service` 同时支持 v2 / v3（默认 v2），一般无需改动。

### 10.4 验证顺序（避免被上游鉴权绕晕）

> 若桥接上游仍是 xiaozhi.me，设备可能还需要 xiaozhi.me 的 `websocket.token` 才能过上游鉴权——取决于桥接实现。建议先绕开：

1. 先用 `docker-compose.debug.yml` 让**设备直连 music-service**（WS 改 `ws://<盒子IP>:8800/`），唤醒后 `curl /play` 验证音频链路（此时不碰任何 token）。
2. 链路通了，再把设备 WS 改指向 xiaozhi-client 桥接端口，填好上游 Qwen / ASR / TTS，验证"对话 + 放歌"。

---

## 十一、已知待核实项（落地前务必确认）

1. **设备 WebSocket 端口**：官方 compose 里 `3000` 标为"备用端口"，疑似设备口；以实际 `docker-compose.yml` / `docker compose ps` 为准。
2. **桥接拓扑确认**：确保设备连的是 xiaozhi-client（桥接）而非直连 xiaozhi.me；桥接的上游接入点填 xiaozhi.me 或自托管，且桥接已开启本地音乐注入（见六/6.2）。
3. **Opus 格式**：确认后端回传设备的是**裸 Opus 帧**，与固件 `esp_opus_dec` 一致。
4. **配置字段名**：`llm` / ASR / TTS 的具体字段（baseUrl、apiKey、model、appId…）以官方 `xiaozhi.config.json` 与 Web UI 为准。
