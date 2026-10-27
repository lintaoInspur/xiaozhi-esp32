#!/usr/bin/env python3
# 极简 stdio MCP 服务器：把"播放 / 停止"意图转成 HTTP 调用到 xiaozhi-client 的 /api/music。
# 不依赖第三方 SDK，按 MCP JSON-RPC 最小实现（initialize / tools/list / tools/call）。
#
# 接入方式（xiaozhi-client 的 MCP 配置，Web UI 或 xiaozhi.config.json）：
#   {"name":"xiaozhi-music","command":"python3",
#    "args":["/绝对路径/music-service/mcp/xiaozhi_music_mcp.py"],"transport":"stdio"}
#
# 智能体提示词加：想播放音乐时调用 xiaozhi-music__play_song（传入歌曲名），
# 停止调用 xiaozhi-music__stop_music；禁止使用 search_music。

import json
import os
import sys
import urllib.request

BRIDGE = os.environ.get("XIAOZHI_BRIDGE", "http://xiaozhi-client:3000")
MUSIC_DIR = os.environ.get("MUSIC_DIR", "/music")


def _post(path: str, payload: dict) -> int:
    req = urllib.request.Request(
        BRIDGE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status
    except Exception:
        return 500


def _resolve(song: str):
    """本地曲库优先：文件名包含 song 即匹配（在线解析请在此扩展，接入音乐 API）。"""
    try:
        for f in os.listdir(MUSIC_DIR):
            if song and song.lower() in f.lower():
                return "/music/" + f
    except OSError:
        pass
    return None


def handle_call(name: str, args: dict) -> dict:
    if name == "play_song":
        song = args.get("song_name", "")
        url = _resolve(song)
        if not url:
            return {
                "content": [{"type": "text", "text": f"未在本地曲库找到：{song}"}],
                "isError": True,
            }
        status = _post("/api/music/play", {"url": url, "name": song})
        return {
            "content": [{"type": "text", "text": f"已请求播放 {song} ({url}) status={status}"}]
        }
    if name == "stop_music":
        _post("/api/music/stop", {})
        return {"content": [{"type": "text", "text": "已请求停止"}]}
    return {"content": [{"type": "text", "text": f"未知工具 {name}"}], "isError": True}


def main() -> None:
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        try:
            msg = json.loads(line)
        except Exception:
            continue
        mid = msg.get("id")
        method = msg.get("method")
        if method == "initialize":
            print(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": mid,
                        "result": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {},
                            "serverInfo": {"name": "xiaozhi-music", "version": "1.0"},
                        },
                    }
                ),
                flush=True,
            )
        elif method == "tools/list":
            tools = [
                {
                    "name": "play_song",
                    "description": "播放指定歌曲（先按名匹配本地曲库，找不到可扩展在线解析）",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "song_name": {"type": "string"},
                            "artist_name": {"type": "string"},
                        },
                        "required": ["song_name"],
                    },
                },
                {
                    "name": "stop_music",
                    "description": "停止播放",
                    "inputSchema": {"type": "object", "properties": {}},
                },
            ]
            print(
                json.dumps({"jsonrpc": "2.0", "id": mid, "result": {"tools": tools}}),
                flush=True,
            )
        elif method == "tools/call":
            result = handle_call(
                msg["params"]["name"], msg["params"].get("arguments", {})
            )
            print(
                json.dumps({"jsonrpc": "2.0", "id": mid, "result": result}),
                flush=True,
            )
        else:
            print(json.dumps({"jsonrpc": "2.0", "id": mid, "result": {}}), flush=True)


if __name__ == "__main__":
    main()
