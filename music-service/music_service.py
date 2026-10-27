#!/usr/bin/env python3
"""XiaoZhi music streaming service.

This service lets an ESP32 XiaoZhi device play arbitrary audio (local files or
remote HTTP/HTTPS streams) by transcoding them on the fly to Opus 16 kHz mono and
streaming the raw Opus frames to the device over the XiaoZhi binary protocol
(``BinaryProtocol2`` / ``BinaryProtocol3``). The device reuses its existing Opus
playback channel, so no extra decoder is needed on the MCU.

How the device plays:
    The firmware enters the ``Speaking`` state when it receives the JSON control
    message ``{"type":"music","state":"start"}`` and, as long as it stays in that
    state, every downstream Opus binary frame is fed to the decoder. This service
    therefore (1) sends ``music start`` / ``music stop`` JSON and (2) streams the
    Opus frames.

NOTE: the device only accepts downstream binary audio while its audio channel is
open (i.e. during a conversation / while listening). In normal use the channel is
open while the user is talking to the assistant, which is exactly when music is
triggered. For standalone debugging, wake the device / press talk (opens the
channel) and then call ``/play``.

HTTP control API:
    POST /play   {"url": "<file-or-http-url>", "name": "<title>"}   start streaming
    POST /stop                                                    stop playback
    GET  /list                                                    list local library
    GET  /file/<name>                                             stream a local file (Range)

WebSocket:
    The device connects with its standard hello; this service answers with a
    server hello and is then ready to push audio. Point the device's websocket
    URL at ``ws://<host>:<port>/`` (or ``/ws``).

Example:
    python music_service.py --host 0.0.0.0 --port 8800 --music-dir /music
    curl -X POST localhost:8800/play -d '{"url":"/music/song.mp3","name":"Song"}'

Debug auto-play (no HTTP needed): the service starts playing the given source as
soon as the device opens its audio channel:
    python music_service.py --auto-play /music/test.mp3
"""
import argparse
import asyncio
import json
import logging
import mimetypes
import os
import pathlib
import re
import struct

import aiohttp
from aiohttp import web
import opuslib

LOG = logging.getLogger("music_service")

# Opus target format expected by the device decoder (esp_opus_dec, 60 ms frames).
OPUS_SAMPLE_RATE = 16000
OPUS_CHANNELS = 1
OPUS_FRAME_MS = 60
OPUS_FRAME_SAMPLES = OPUS_SAMPLE_RATE * OPUS_FRAME_MS // 1000  # 960 samples
OPUS_FRAME_BYTES = OPUS_FRAME_SAMPLES * 2  # s16le
REALTIME = OPUS_FRAME_MS / 1000.0


def wrap_opus(opus: bytes, ts: int, version: int) -> bytes:
    """Wrap a raw Opus packet into a XiaoZhi binary protocol frame (big-endian)."""
    if version == 3:
        return struct.pack(">BBH", 0, 0, len(opus)) + opus
    # Default / version 2
    return struct.pack(">HHIII", 2, 0, 0, ts, len(opus)) + opus


def music_start_json(name: str) -> str:
    return json.dumps({"type": "music", "state": "start", "name": name or ""})


def music_stop_json() -> str:
    return json.dumps({"type": "music", "state": "stop"})


class Device:
    """A connected device and its negotiated protocol parameters."""

    def __init__(self, ws: web.WebSocketResponse):
        self.ws = ws
        self.version = 2
        self.frame_duration = OPUS_FRAME_MS
        self.channel_open = False  # becomes True once the device sends audio


class MusicService:
    def __init__(self, music_dir: str, auto_play: str | None = None):
        self.music_dir = pathlib.Path(music_dir).resolve()
        self.auto_play = auto_play
        self.devices: set[Device] = set()
        self._play_task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    # ----- device connection ------------------------------------------------
    async def ws_handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        dev = Device(ws)
        self.devices.add(dev)
        LOG.info("device connected (%d total)", len(self.devices))

        try:
            # First text message from the device is its client hello.
            msg = await ws.receive()
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    hello = json.loads(msg.data)
                    dev.version = int(hello.get("version", 2))
                    ap = hello.get("audio_params") or {}
                    dev.frame_duration = int(ap.get("frame_duration", OPUS_FRAME_MS))
                except (ValueError, TypeError):
                    pass
        except Exception:  # noqa: BLE001
            pass

        # Server hello: tells the device the channel is ready and negotiates params.
        await ws.send_str(
            json.dumps(
                {
                    "type": "hello",
                    "transport": "websocket",
                    "session_id": "music-service",
                    "audio_params": {
                        "sample_rate": OPUS_SAMPLE_RATE,
                        "frame_duration": OPUS_FRAME_MS,
                    },
                }
            )
        )

        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.BINARY:
                    # The device only sends binary once its audio channel is open.
                    if not dev.channel_open:
                        dev.channel_open = True
                        LOG.info("device audio channel open")
                        if self.auto_play and (self._play_task is None or self._play_task.done()):
                            asyncio.create_task(self.play(self.auto_play, "auto-play"))
                # TEXT (stt/abort/...) and other messages are ignored here.
        finally:
            self.devices.discard(dev)
            LOG.info("device disconnected (%d total)", len(self.devices))
        return ws

    # ----- playback control -------------------------------------------------
    async def play(self, url: str, name: str) -> None:
        if self._play_task is not None and not self._play_task.done():
            await self.stop()
            await asyncio.sleep(0.1)
        self._stop.clear()
        self._play_task = asyncio.create_task(self._stream(url, name))

    async def stop(self) -> None:
        self._stop.set()
        if self._play_task is not None:
            try:
                await asyncio.wait_for(self._play_task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._play_task.cancel()

    async def _wait_for_channel(self, timeout: float = 4.0) -> bool:
        """Best-effort wait until at least one device has an open audio channel."""
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            if any(d.channel_open for d in self.devices):
                return True
            await asyncio.sleep(0.1)
        return any(d.channel_open for d in self.devices)

    async def _stream(self, url: str, name: str) -> None:
        src = self._resolve_source(url)
        if src is None:
            LOG.error("cannot resolve source: %s", url)
            return

        opened = await self._wait_for_channel()
        if not opened:
            LOG.warning("no device with an open audio channel yet; streaming anyway")

        await self._broadcast(music_start_json(name))

        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            src,
            "-f",
            "s16le",
            "-ar",
            str(OPUS_SAMPLE_RATE),
            "-ac",
            str(OPUS_CHANNELS),
            "-",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        enc = opuslib.Encoder(OPUS_SAMPLE_RATE, OPUS_CHANNELS, opuslib.APPLICATION_AUDIO)
        try:
            enc.bitrate = 96000  # better music quality within 16 kHz
        except Exception:  # noqa: BLE001
            pass

        ts = 0
        sent = 0
        try:
            while not self._stop.is_set():
                try:
                    pcm = await asyncio.wait_for(
                        proc.stdout.readexactly(OPUS_FRAME_BYTES), timeout=5
                    )
                except asyncio.IncompleteReadError as exc:
                    if exc.partial:
                        pcm = exc.partial + b"\x00" * (OPUS_FRAME_BYTES - len(exc.partial))
                    else:
                        break  # clean EOF
                if len(pcm) < OPUS_FRAME_BYTES:
                    pcm = pcm + b"\x00" * (OPUS_FRAME_BYTES - len(pcm))
                try:
                    opus = enc.encode(pcm, OPUS_FRAME_SAMPLES)
                except Exception as exc:  # noqa: BLE001
                    LOG.warning("opus encode error: %s", exc)
                    break
                for dev in list(self.devices):
                    try:
                        await dev.ws.send_bytes(wrap_opus(opus, ts, dev.version))
                    except Exception:  # noqa: BLE001
                        pass
                sent += 1
                ts += OPUS_FRAME_MS
                await asyncio.sleep(REALTIME)
        finally:
            if proc.returncode is None:
                proc.terminate()
                try:
                    await proc.wait()
                except Exception:  # noqa: BLE001
                    pass
            await self._broadcast(music_stop_json())
            LOG.info("streamed %d frames from %s", sent, src)

    # ----- helpers ----------------------------------------------------------
    async def _broadcast(self, text: str) -> None:
        for dev in list(self.devices):
            try:
                await dev.ws.send_str(text)
            except Exception:  # noqa: BLE001
                pass

    def _resolve_source(self, url: str) -> str | None:
        """Return an ffmpeg-readable source for ``url``.

        Accepts http(s) URLs directly and local files relative to ``music_dir``
        (path-traversal protected).
        """
        if url.startswith("http://") or url.startswith("https://"):
            return url
        candidate = (self.music_dir / url).resolve()
        if candidate.is_file() and (self.music_dir in candidate.parents or candidate == self.music_dir):
            return str(candidate)
        LOG.error("refused unresolved/local path: %s", url)
        return None

    # ----- HTTP routes ------------------------------------------------------
    async def play_handler(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except Exception:  # noqa: BLE001
            return web.json_response({"error": "invalid json"}, status=400)
        url = data.get("url")
        name = data.get("name", "")
        if not url:
            return web.json_response({"error": "missing url"}, status=400)
        asyncio.create_task(self.play(url, name))
        return web.json_response({"status": "playing", "url": url, "name": name})

    async def stop_handler(self, request: web.Request) -> web.Response:
        await self.stop()
        return web.json_response({"status": "stopped"})

    async def list_handler(self, request: web.Request) -> web.Response:
        files = sorted(p.name for p in self.music_dir.iterdir() if p.is_file())
        return web.json_response({"files": files, "music_dir": str(self.music_dir)})

    async def file_handler(self, request: web.Request) -> web.Response:
        name = request.match_info["name"]
        path = (self.music_dir / name).resolve()
        if not (self.music_dir in path.parents or path == self.music_dir) or not path.is_file():
            return web.Response(status=404)
        total = path.stat().st_size
        start, end = 0, total - 1
        rng = request.headers.get("Range")
        if rng:
            m = re.match(r"bytes=(\d+)-(\d*)", rng)
            if m:
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else total - 1
        end = min(end, total - 1)
        length = end - start + 1
        with open(path, "rb") as fh:
            fh.seek(start)
            chunk = fh.read(length)
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        return web.Response(
            body=chunk,
            status=206 if rng else 200,
            headers={
                "Content-Range": f"bytes {start}-{end}/{total}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(length),
                "Content-Type": ctype,
            },
        )

    async def opus_stream_handler(self, request: web.Request) -> web.StreamResponse:
        """Backend endpoint for xiaozhi-server integration.

        Streams raw Opus packets (one per chunk) for ``url`` so that the
        conversation server can pull frames and forward them to the device over
        the *same* WebSocket connection it already owns. The server is
        responsible for sending ``music start`` / ``music stop`` JSON and for
        pacing/timestamping the frames (BinaryProtocol2/3 wrapping).
        """
        url = request.query.get("url")
        if not url:
            return web.Response(status=400, text="missing url")
        src = self._resolve_source(url)
        if src is None:
            return web.Response(status=404, text="cannot resolve source")

        resp = web.StreamResponse(
            headers={"Content-Type": "application/octet-stream"}
        )
        await resp.prepare(request)

        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            src,
            "-f",
            "s16le",
            "-ar",
            str(OPUS_SAMPLE_RATE),
            "-ac",
            str(OPUS_CHANNELS),
            "-",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        enc = opuslib.Encoder(OPUS_SAMPLE_RATE, OPUS_CHANNELS, opuslib.APPLICATION_AUDIO)
        try:
            enc.bitrate = 96000
        except Exception:  # noqa: BLE001
            pass
        ts = 0
        try:
            while True:
                try:
                    pcm = await asyncio.wait_for(
                        proc.stdout.readexactly(OPUS_FRAME_BYTES), timeout=5
                    )
                except asyncio.IncompleteReadError as exc:
                    if exc.partial:
                        pcm = exc.partial + b"\x00" * (OPUS_FRAME_BYTES - len(exc.partial))
                    else:
                        break  # clean EOF
                if len(pcm) < OPUS_FRAME_BYTES:
                    pcm = pcm + b"\x00" * (OPUS_FRAME_BYTES - len(pcm))
                try:
                    opus = enc.encode(pcm, OPUS_FRAME_SAMPLES)
                except Exception:  # noqa: BLE001
                    break
                # BP2 帧（16 字节头 + Opus 包），桥接侧用 parseBinaryProtocol2 逐帧切分
                await resp.write(wrap_opus(opus, ts, 2))
                ts += OPUS_FRAME_MS
        finally:
            if proc.returncode is None:
                proc.terminate()
                try:
                    await proc.wait()
                except Exception:  # noqa: BLE001
                    pass
        await resp.write_eof()
        return resp


def make_app(music_dir: str, auto_play: str | None) -> web.Application:
    svc = MusicService(music_dir, auto_play)
    app = web.Application()
    app.router.add_get("/ws", svc.ws_handler)
    app.router.add_get("/", svc.ws_handler)  # device may connect to the root path
    app.router.add_post("/play", svc.play_handler)
    app.router.add_post("/stop", svc.stop_handler)
    app.router.add_get("/list", svc.list_handler)
    app.router.add_get("/file/{name}", svc.file_handler)
    app.router.add_get("/opus_stream", svc.opus_stream_handler)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="XiaoZhi music streaming service")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8800)
    parser.add_argument("--music-dir", default="./music")
    parser.add_argument(
        "--auto-play",
        default=None,
        help="Source (file or http URL) to auto-play once a device opens its channel (debug)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    os.makedirs(args.music_dir, exist_ok=True)
    app = make_app(args.music_dir, args.auto_play)
    web.run_app(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
