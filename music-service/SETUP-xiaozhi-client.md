# XiaoZhi Client 方案 · 选型索引（A / B 两份独立文档）

> 本方案用 **`shenjingnan/xiaozhi-client`**（Node/TS，MIT）做桥接，实现"AI 对话 + 本地/在线音乐"完整流程。
> 旧方案（Python 版 xiaozhi-server）见 `SETUP.md`，本索引**不改动**它。
> 音乐有**两条互不依赖的路径**，已拆成两份独立文档，小白任选一份照做即可：
> - **路径 A**：`SETUP-pathA-maggotxy.md` —— 刷 Maggotxy 音乐固件 + 配音乐 MCP，**设备端自播 MP3，免写注入代码**。
> - **路径 B**：`SETUP-pathB-opus.md` —— 本仓库 music-service 转码 + 桥接注入 Opus，**设备只解 Opus，最省设备**。
>
> ⚠️ 凡标注「⚠️ 待核实」的，请以官方仓库 / 实际下载的 `docker-compose.yml` 为准。

---

## 一、为什么转向 xiaozhi-client（桥接模式）

`xiaozhi-client` 一端**终结 ESP32 设备的 WebSocket**，另一端去连上游小智接入点（xiaozhi.me 云端 或 自托管），在中间做协议转译 + 能力增强。

- **设备侧**：设备只连 xiaozhi-client，由它把音频/指令转给上游，并把上游的 Opus 回传设备。
- **上游接入点**：提供真正的 ASR → LLM → TTS（xiaozhi.me 云端，或自托管填 Qwen/字节等 Key）。
- **能力增强（关键）**：因为桥接**拥有设备侧连接**，可直接把本地能力注入设备流——例如路径 B 的本地音乐 Opus 由桥接直接发到设备，**不依赖接入点是否支持本地音乐**。

> **"本地"指桥接部署在盒子上，不等于盒子上跑大模型**：ASR/LLM/TTS 全部走云端 API，盒子只做协议转发 + 音频转码，不需 GPU、不加载模型权重，适合黑豹 X2 这类资源受限盒子。

---

## 二、选 A 还是选 B

| 维度 | 路径 A（Maggotxy 固件 + 音乐 MCP） | 路径 B（music-service + 桥接注入 Opus） |
|---|---|---|
| 设备端改动 | 要刷 Maggotxy 固件（或移植 `play_song`） | **零改动**，复用固件已有 Opus 播放 |
| 是否需要写代码 | 否（纯配置） | 是（桥接后端写 TypeScript 注入，开发任务） |
| 音乐源 | 在线音乐 API（A1 依赖 Maggotxy 后端；A2 自配 Key） | 本地曲库或在线，盒子转码 |
| 设备负载 | 设备端解 MP3（256KB 缓冲 + 解码线程） | 设备只解 Opus（最轻） |
| 依赖 | A1 依赖第三方音乐后端 + MAC 授权 | 不依赖第三方，完全自托管 |
| 适合谁 | 想最快上手、不想写代码 | 低配板 / 想自托管 / 能写桥接代码 |

**本机结论（已确认 2MB PSRAM）**：选 **路径 B**。2MB PSRAM 上固件基线已很紧，A1 还要独占 256KB 缓冲 + MP3 解码线程，brownout 风险高；B 复用已有 Opus 解码通道、几乎不新增内存。只有"不想写注入代码且供电极稳"时才考虑 A1。

---

## 三、共享前置准备（两份文档都含，可直接照做）

两份文档各自都写了完整的"盒子 + 桥接 + AI + 设备指向"前置步骤，无需在索引里重复。概括：
1. 清理旧 Docker；
2. clone 本仓库 `music-service`（路径 A 不需曲库，路径 B 放 `music/`）；
3. 起 `xiaozhi-client` 桥接（Web UI `9999`、设备端口 `3000`，以 `docker compose ps` 为准）；
4. 配 Qwen(DashScope) / 字节 ASR/TTS；
5. 设备 WS 改 `ws://<盒子IP>:3000/`，看"已连接"。

---

## 四、已知待核实项（落地前务必确认）

1. **设备 WebSocket 端口**：官方 compose 里 `3000` 标为"备用端口"，疑似设备口；以实际 `docker-compose.yml` / `docker compose ps` 为准。
2. **桥接拓扑**：确保设备连的是 xiaozhi-client（桥接）而非直连 xiaozhi.me。
3. **Opus 格式**：路径 B 后端回传设备的是**裸 Opus 帧**（非 ogg 容器），与固件 `esp_opus_dec` 一致。
4. **配置字段名**：`llm` / ASR / TTS 的具体字段以官方 `xiaozhi.config.json` 与 Web UI 为准。
5. **music-service 现状**：本仓库 `music-service` 是自研、尚未社区广泛验证的组件，落地前先按路径 B 文档「三/M0·M1」实测转码链。
