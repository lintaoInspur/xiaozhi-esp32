# 路径 B：music-service + 桥接注入 Opus（设备只解 Opus，最省设备）

> 本文档是「路径 B」的**完整、照着做**版本。目标：盒子转码音乐成 Opus，由桥接直接注入设备播放；**设备端零改动**，复用固件已有 Opus 解码通道。
> 选型、优缺点、与路径 A 的区别见 `SETUP-xiaozhi-client.md`（索引）。
> 凡标注「⚠️ 待核实」的，请以官方仓库 / 实际下载的 `docker-compose.yml` 为准。

---

## 〇、B 路径速览（执行清单）

1. **清理旧 Docker** → 见「二」。
2. **放代码 + 准备曲库** → 见「二」。本地曲库 `music/` 放进你的歌曲（在线音乐也可，见三）。
3. **起 music-service（先验证链路）** → `docker compose -f docker-compose.debug.yml up -d --build`，验证 `/list` 与 ffmpeg 转码（M0/M1）。
4. **起 xiaozhi-client 桥接** → `docker compose -f docker-compose.xiaozhi-client.yml up -d --build`；Web UI `9999`、设备端口 `3000`。
5. **配置 AI** → 见「二」。填 Qwen / 字节 ASR/TTS 的 Key。
6. **写桥接注入代码（开发任务）** → 见「四」。把"放歌意图 → 拉 `/opus_stream` → 按 BinaryProtocol 封装 → 发设备"实现进 xiaozhi-client。
7. **设备指向桥接** → 见「二」。WS 改 `ws://<盒子IP>:3000/`，重启，看"已连接"。
8. **验收** → 见「五」。说"播放青花瓷"→ 桥接注入 Opus → 设备出声；"停止"→ 停。

> ✅ **本机优势（2MB PSRAM）**：B 把"搜歌+取流+解码成 Opus"全放盒子，设备只做 Opus 解码 + I2S 播放（Opus 帧很小、解码轻），**几乎不新增内存**，是 2MB 板子最稳的选择（见「六」）。

---

## 一、方案原理

- 设备连 **xiaozhi-client 桥接**（设备侧 WS 由它终结），桥接去连上游小智接入点做 ASR→LLM→TTS。
- 关键：桥接**拥有设备侧连接**，可直接把本地音乐（music-service 转码后的 Opus）**注入**设备同一条 WS，不依赖接入点是否支持本地音乐。
- 固件播放模型（本仓库 `78/xiaozhi-esp32`）：服务端推 **Opus 二进制帧** + `{"type":"music","state":"start/stop"}` 控制，固件只负责解码播放（已有 `self.music.stop`，`main/mcp_server.cc:329`；播放见 `main/application.cc:668-687`）。**设备端不下载、不解 MP3**，所以极省。

```
设备(单条 WS, 连 xiaozhi-client 桥接)
   │
   ▼
xiaozhi-client 桥接（轻量 Node）
   │  ① 终结设备 WS，上游走 ASR→LLM(Qwen)→TTS
   │  ② 本地音乐注入：GET music-service:8800/opus_stream?url=... 拉裸 Opus 帧
   │     再 music start + 逐帧封装 → 发到设备同一条 WS
   ▼
music-service（内部转码后端，不直接连设备）
   │  ffmpeg 任意格式 → PCM 16k mono → opuslib → 裸 Opus 帧
  本地曲库 ./music 或网络流
```

> **资源可行性（针对黑豹 X2 / 2MB PSRAM 板）**：盒子只做协议转发 + ffmpeg 单路转码（CPU 中等，ARM 可承受），不加载模型、不需 GPU；设备侧只用已有 Opus 解码通道，几乎零新增内存。

---

## 二、共享前置准备（盒子 + 桥接 + AI + 设备指向）

> A、B 两份文档共用这一段。

### 2.1 清理旧 Docker（按需）
```sh
docker stop $(docker ps -aq) 2>/dev/null; docker rm $(docker ps -aq) 2>/dev/null
docker image prune -f; docker network prune -f; docker builder prune -f
docker --version && docker compose version    # 确认 docker 在
```

### 2.2 获取代码 + 准备曲库
```sh
cd ~
git clone git@github.com:lintaoInspur/xiaozhi-esp32.git
cd xiaozhi-esp32/music-service
mkdir -p music && cp /你的歌曲/*.mp3 music/   # 本地曲库（在线音乐可不放）
```

### 2.3 起 xiaozhi-client 桥接（Docker，支持 arm64）
镜像 `shenjingnan/xiaozhi-client:latest` 官方提供，**支持 arm64**（黑豹 X2 / 树莓派可跑），约 490MB(压缩)/1.4GB(运行)。

取官方 compose（⚠️ 端口/服务名以实际下载为准）：
```sh
curl -O https://raw.githubusercontent.com/shenjingnan/xiaozhi-client/main/docker-compose.yml
docker compose up -d
docker compose logs -f
```
或本仓库组合（与 music-service 同网络）：
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

## 三、music-service 转码后端（先独立验证链路）

`music-service` 提供 `GET /opus_stream?url=<文件或http>` 持续吐**裸 Opus 帧**（见 `music_service.py`），并用 ffmpeg 把任意格式转成 16k 单声道 PCM → opuslib → 裸 Opus 帧。

### M0：离线验证转码链（不碰设备）
```sh
ffmpeg -i song.mp3 -f s16le -ar 16000 -ac 1 - | head -c 1M > /dev/null && echo "PCM OK"
```

### M1：先单独调通音乐（设备直连 music-service）
```sh
cd ~/xiaozhi-esp32/music-service
docker compose -f docker-compose.debug.yml up -d --build
# 设备 WS 指向 ws://<盒子IP>:8800/
curl -X POST http://<盒子IP>:8800/play -d '{"url":"/music/test.mp3","name":"测试曲"}'
# 唤醒设备 → 应出声
```
这一步绕开桥接注入的复杂性，单独证明 music-service 可用；若不可用，说明转码后端有问题，先解决它。

---

## 四、桥接注入代码（已落地到 xiaozhi-client/src）

> ⚠️ 本方案代码已**直接写入**工作区内的 `xiaozhi-client/src/`（见 4.6 文件清单），与下方一一对应，无需再手动粘贴。
>
> 核心事实（来自 `shenjingnan/xiaozhi-client` 源码，已核对）：
> - `IDeviceConnection`（`src/esp32/interfaces.ts`）提供 `send(msg)`（发 JSON，自动转 snake_case）、`sendBinaryProtocol2(data, ts?)`（按 BinaryProtocol2 封装发二进制，**TTS 同款**）、`getSessionId()`。
> - `ESP32DeviceManager.getConnection(deviceId)`（`src/esp32/esp32-manager.ts:605`）取设备连接。
> - WebServer 在 `src/server/WebServer.ts:189` 建 `esp32Manager`、`:206` 建 `esp32Handler`、`:568-582` 经 `createHandlerDependencies()` 注入依赖——这是挂载音乐服务的**三个插入点**。
> - 固件 `main/application.cc:668` 收到 `{"type":"music","state":"start"}` 进 `Speaking` 并解码下行 Opus；`:1259` `StopMusic()`。设备下行二进制帧就是 BinaryProtocol2 Opus，**音乐直接复用 `sendBinaryProtocol2` 即可**，无需新解码器。
> - **采样率无碍**：Opus 是重采样编解码器，music-service 编 16kHz、固件解码器按板子输出率（多数 24kHz）解，内部自动重采样，**不变速**。

### 4.1 music-service 改造：让 `/opus_stream` 吐可切分帧
当前 `opus_stream_handler` 直接 `resp.write(opus)`（裸包拼接，HTTP 侧无法切分）。已改为用 `wrap_opus()` 包 BinaryProtocol2 头（含 payload_size），桥接侧用现成的 `parseBinaryProtocol2` 即可逐帧切出 Opus：
```python
# music_service.py :: opus_stream_handler 的循环（已改）
ts = 0
while True:
    try:
        pcm = await asyncio.wait_for(proc.stdout.readexactly(OPUS_FRAME_BYTES), timeout=5)
    except asyncio.IncompleteReadError as exc:
        if exc.partial:
            pcm = exc.partial + b"\x00" * (OPUS_FRAME_BYTES - len(exc.partial))
        else:
            break
    if len(pcm) < OPUS_FRAME_BYTES:
        pcm = pcm + b"\x00" * (OPUS_FRAME_BYTES - len(pcm))
    try:
        opus = enc.encode(pcm, OPUS_FRAME_SAMPLES)
    except Exception:
        break
    await resp.write(wrap_opus(opus, ts, 2))   # ← 关键：加 BP2 头（16B + Opus）
    ts += OPUS_FRAME_MS
```
`wrap_opus()` 已存在于 `music_service.py:67`；Dockerfile 已是 `python:3.11 + ffmpeg + opuslib`，**无需改镜像**。

### 4.2 桥接新增 `src/esp32/services/music.service.ts`（核心）
```ts
import type { IDeviceConnection, ILogger } from "../interfaces.js";
import { noopLogger } from "../interfaces.js";

export interface MusicServiceOptions {
  baseUrl?: string; // 默认 http://music-service:8800（同 docker 网络）
  getConnection?: (deviceId: string) => IDeviceConnection | undefined;
  logger?: ILogger;
}

export class MusicService {
  private readonly baseUrl: string;
  private getConnection?: MusicServiceOptions["getConnection"];
  private readonly logger: ILogger;
  private readonly active = new Map<string, { abort: () => void }>();

  constructor(options: MusicServiceOptions = {}) {
    this.baseUrl = options.baseUrl ?? "http://music-service:8800";
    this.getConnection = options.getConnection;
    this.logger = options.logger ?? noopLogger;
  }
  setGetConnection(fn: NonNullable<MusicServiceOptions["getConnection"]>): void {
    this.getConnection = fn;
  }

  async play(deviceId: string, url: string, name = ""): Promise<void> {
    const connection = this.getConnection?.(deviceId);
    if (!connection) { this.logger.error(`[Music] 无设备连接: ${deviceId}`); return; }
    this.stop(deviceId);

    const controller = new AbortController();
    this.active.set(deviceId, { abort: () => controller.abort() });

    // 通知固件进入音乐状态（application.cc:668）
    await connection.send({ type: "music", state: "start", name, session_id: connection.getSessionId() });

    const streamUrl = `${this.baseUrl}/opus_stream?url=${encodeURIComponent(url)}`;
    let res: Response;
    try { res = await fetch(streamUrl, { signal: controller.signal }); }
    catch (err) { this.logger.error(`[Music] 拉流失败: ${streamUrl}`, err); await this.end(connection); return; }
    if (!res.ok || !res.body) { this.logger.error(`[Music] opus_stream 异常: ${res.status}`); await this.end(connection); return; }

    // 从字节流切出 BP2 帧，逐帧下发，按 60ms 实时节奏
    const reader = res.body.getReader();
    let buf = new Uint8Array(0); let ts = 0; const HDR = 16;
    const push = (c: Uint8Array) => { const n = new Uint8Array(buf.length + c.length); n.set(buf); n.set(c, buf.length); buf = n; };
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        if (value) push(value);
        while (buf.length >= HDR) {
          const payloadSize = (buf[12] << 24) | (buf[13] << 16) | (buf[14] << 8) | buf[15];
          const total = HDR + payloadSize;
          if (buf.length < total) break; // 帧未收全，等下一 chunk
          const payload = buf.subarray(HDR, total);
          buf = buf.subarray(total);
          await connection.sendBinaryProtocol2(payload, ts); // 重新封装为 BP2 发给设备
          ts += 60; await new Promise((r) => setTimeout(r, 60));
        }
      }
    } catch (err) { this.logger.warn(`[Music] 流结束/中断: ${deviceId}`, err); }
    finally { this.active.delete(deviceId); await this.end(connection); }
  }

  stop(deviceId: string): void {
    this.active.get(deviceId)?.abort();
    this.active.delete(deviceId);
    this.getConnection?.(deviceId)?.send({ type: "music", state: "stop" }).catch(() => {});
  }

  private async end(connection: IDeviceConnection): Promise<void> {
    try { await connection.send({ type: "music", state: "stop" }); } catch { /* ignore */ }
  }
}
```
> 若 TS 报 `type:"music"` 不在 `ESP32WSMessageType` 联合类型，在 `src/esp32/types.ts` 的该类型里加 `"music"`，或此处用 `as ESP32WSMessage` 断言。

### 4.3 接线到 WebServer（三处插入点）
1. `src/server/WebServer.ts` 声明并实例化（在 `:189` 附近）：
```ts
import { MusicService } from "@/esp32/services/music.service.js";
// ...
this.musicService = new MusicService({
  baseUrl: process.env.MUSIC_SERVICE_URL ?? "http://music-service:8800",
  getConnection: (id) => this.esp32Manager.getConnection(id),
  logger: this.logger,
});
```
2. 新增 `src/server/handlers/music.handler.ts` + `src/server/routes/domains/music.route.ts`（暴露 `POST /api/music/play`、`/api/music/stop`，内部取首个已连设备）：
```ts
// music.handler.ts
import type { ESP32DeviceManager } from "@/esp32";
import type { MusicService } from "@/esp32/services/music.service.js";
import type { Context } from "hono";
import type { AppContext } from "../types/hono.context.js";
import { BaseHandler } from "./base.handler.js";

export class MusicHandler extends BaseHandler {
  constructor(private manager: ESP32DeviceManager, private music: MusicService) { super(); }
  private firstDeviceId(): string | undefined { return this.manager.getConnectedDeviceIds()[0]; }
  async play(c: Context<AppContext>): Promise<Response> {
    const body = await this.parseJsonBody(c, "请求体格式错误").catch(() => ({}) as Record<string, unknown>);
    const url = body?.url as string | undefined; const name = (body?.name as string) ?? "";
    if (!url) return c.fail(400, "missing url", undefined, 400);
    const deviceId = (body?.deviceId as string) ?? this.firstDeviceId();
    if (!deviceId) return c.fail(503, "无已连接设备", undefined, 503);
    await this.music.play(deviceId, url, name);
    return c.json({ status: "playing", url, deviceId });
  }
  async stop(c: Context<AppContext>): Promise<Response> {
    const body = await this.parseJsonBody(c, "请求体格式错误").catch(() => ({}) as Record<string, unknown>);
    const deviceId = (body?.deviceId as string) ?? this.firstDeviceId();
    if (deviceId) this.music.stop(deviceId);
    return c.json({ status: "stopped", deviceId });
  }
}
```
```ts
// routes/domains/music.route.ts
import type { Context } from "hono";
import type { HandlerDependencies } from "../../routes/types.js";
const createMusicHandler = (method: (h: any, c: Context) => Promise<Response>) =>
  async (c: Context) => {
    const deps = c.get("dependencies") as HandlerDependencies;
    const h = deps.musicHandler;
    if (!h) return c.json({ error: "music service not available" }, 503);
    return method(h, c);
  };
export const musicRoutes = [
  { method: "POST", path: "/api/music/play", handler: createMusicHandler((h, c) => h.play(c)) },
  { method: "POST", path: "/api/music/stop", handler: createMusicHandler((h, c) => h.stop(c)) },
];
```
3. 在 `src/esp32/esp32-manager.ts` 增加公开方法（供 MusicHandler 取设备）：
```ts
getConnectedDeviceIds(): string[] { return Array.from(this.connections.keys()); }
```
并在 `WebServer.ts` 的 `createHandlerDependencies()`（`:568-582`）加入 `musicHandler: this.musicHandler`，在 `src/server/routes/types.ts` 的 `HandlerDependencies` 增加 `musicHandler` 字段，在路由注册处（如 `RouteManager`）并入 `musicRoutes`。

### 4.4 语音触发：本地 MCP 工具（推荐）
放一个极简 stdio MCP 服务 `music-service/mcp/xiaozhi_music_mcp.py`，把"播放/停止"转成 HTTP 调 bridge 的 `/api/music`：
```python
#!/usr/bin/env python3
# 极简 stdio MCP：播放/停止 → 调 xiaozhi-client 的 /api/music（不依赖第三方 SDK）
import json, sys, os, urllib.request
BRIDGE = os.environ.get("XIAOZHI_BRIDGE", "http://xiaozhi-client:3000")
MUSIC_DIR = os.environ.get("MUSIC_DIR", "/music")

def _post(path, payload):
    req = urllib.request.Request(BRIDGE + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r: return r.status
    except Exception: return 500

def _resolve(song):
    try:
        for f in os.listdir(MUSIC_DIR):
            if song and song.lower() in f.lower(): return "/music/" + f
    except OSError: pass
    return None  # 本地找不到：在线解析请在此扩展（接入音乐 API）

def handle_call(name, args):
    if name == "play_song":
        song = args.get("song_name", "")
        url = _resolve(song)
        if not url: return {"content":[{"type":"text","text":f"未在本地曲库找到：{song}"}],"isError":True}
        st = _post("/api/music/play", {"url": url, "name": song})
        return {"content":[{"type":"text","text":f"已请求播放 {song} status={st}"}]}
    if name == "stop_music":
        _post("/api/music/stop", {})
        return {"content":[{"type":"text","text":"已请求停止"}]}
    return {"content":[{"type":"text","text":f"未知工具 {name}"}],"isError":True}

def main():
    while True:
        line = sys.stdin.readline()
        if not line: break
        try: msg = json.loads(line)
        except Exception: continue
        mid, method = msg.get("id"), msg.get("method")
        if method == "initialize":
            print(json.dumps({"jsonrpc":"2.0","id":mid,"result":{"protocolVersion":"2024-11-05","capabilities":{},"serverInfo":{"name":"xiaozhi-music","version":"1.0"}}}), flush=True)
        elif method == "tools/list":
            tools=[{"name":"play_song","description":"播放指定歌曲（先按名匹配本地曲库）","inputSchema":{"type":"object","properties":{"song_name":{"type":"string"},"artist_name":{"type":"string"}},"required":["song_name"]}},
                   {"name":"stop_music","description":"停止播放","inputSchema":{"type":"object","properties":{}}}]
            print(json.dumps({"jsonrpc":"2.0","id":mid,"result":{"tools":tools}}), flush=True)
        elif method == "tools/call":
            print(json.dumps({"jsonrpc":"2.0","id":mid,"result":handle_call(msg["params"]["name"], msg["params"].get("arguments",{}))}), flush=True)
        else:
            print(json.dumps({"jsonrpc":"2.0","id":mid,"result":{}}), flush=True)

if __name__ == "__main__": main()
```
在 xiaozhi-client 的 MCP 配置（Web UI 或 `xiaozhi.config.json`）加入该 stdio 服务：
```json
{"name":"xiaozhi-music","command":"python3","args":["/绝对路径/music-service/mcp/xiaozhi_music_mcp.py"],"transport":"stdio"}
```
智能体提示词加：*"想播放音乐时调用 xiaozhi-music__play_song（传入歌曲名），停止调用 xiaozhi-music__stop_music；禁止使用 search_music。"*

### 4.5 验证顺序
- **M0**：`ffmpeg -i song.mp3 -f s16le -ar 16000 -ac 1 - | head -c 1M > /dev/null && echo PCM OK`。
- **M1'（桥接直发）**：设备连 bridge、唤醒；`curl -X POST http://<盒子IP>:3000/api/music/play -d '{"url":"/music/test.mp3","name":"测试"}'` → 设备出声。
- **M2**：对话走 Qwen 正常。
- **M3（语音闭环）**："播放青花瓷" → LLM 调 `play_song` → bridge 拉 `/opus_stream` 代发 → 设备出声；"停止" → `stop_music` 生效。

### 4.6 已落地文件清单（xiaozhi-client/src 内）
- `src/esp32/services/music.service.ts`（新增，核心 Opus 中继）
- `src/esp32/types.ts`（新增 `"music"` 消息类型 + `ESP32MusicMessage` 接口）
- `src/esp32/esp32-manager.ts`（新增 `getConnectedDeviceIds()`）
- `src/server/handlers/music.handler.ts`（新增，`POST /api/music/play`、`/api/music/stop`）
- `src/server/routes/domains/music.route.ts`（新增，`music` 路由组）
- `src/server/handlers/index.ts`、`src/server/routes/types.ts`（导出 / 依赖登记 `musicHandler`）
- `src/server/WebServer.ts`（导入、字段、实例化、依赖注入、`music` 路由注册）
- 服务端 `music-service/music_service.py`（已改 `/opus_stream` 输出 BP2 帧）、`music-service/mcp/xiaozhi_music_mcp.py`（已建语音触发 MCP）

---

## 五、验收（闭环）

1. 设备连 xiaozhi-client；Web UI "已连接"。
2. 唤醒对话走 Qwen（先确认对话正常）。
3. 语音："播放青花瓷" → 桥接拉 `/opus_stream` 代发 Opus → 设备出声。
4. 语音："停止" → `self.music.stop` 或停止指令应能停。
5. 若无声：见「六」头号验收项（裸 Opus 帧）；`/opus_stream` 能否单独访问；桥接日志是否真的在发帧。

---

## 六、排错速查

| 现象 | 排查 |
|---|---|
| Web UI 打不开 | 端口 9999 未映射 / 防火墙；`docker compose ps` |
| 设备连不上后端 | 设备端口(2.3)核实；后端日志看握手；URL 必须 `ws://` |
| 对话无声 / 无回复 | Qwen Key、ASR/TTS Key 是否填对；看后端日志 |
| 音乐不出声 | 见 4.2 裸 Opus 帧核实；`/opus_stream` 能否单独访问；桥接是否真在发帧 |
| 音乐与对话冲突 | 确认设备只连 xiaozhi-client 一个端点（单连接约束） |
| 容器起不来 | 镜像拉取失败（arm64 用官方 latest）；`docker compose logs` |
| ffmpeg 转码失败 | M0 先验证；确认容器内 ffmpeg 可用、曲库路径挂载正确 |

---

## 七、设备资源与性能权衡（2MB PSRAM 板子）

- **为什么 B 在 2MB 上最稳**：A1 在设备端解 MP3，要独占 256KB 缓冲 + 解码线程（见 A 文档「十」）；而 B **复用固件已有 Opus 解码通道**，没有新增大缓冲、没有新解码器，音乐只是和语音共用既有的 Opus 播放管线，几乎不新增内存。
- 解码重活（搜歌、取流、ffmpeg 转码成 Opus）全在盒子（服务端）完成，设备只做轻量 Opus 解码 + I2S 播放。
- 附带好处：不依赖 Maggotxy 第三方音乐后端与 MAC 授权，完全自托管。
- 结论：本机已确认 2MB PSRAM → **优先路径 B**。只有"不想写注入代码、且供电极稳"时才考虑 A1。
