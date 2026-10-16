"""Proxy handling: SOCKS5 bridge, custom connector for Telethon."""
import asyncio
import socket
from typing import Optional
from urllib.parse import urlparse

import requests

from .config import CONFIG
from .logging_setup import log
from .retry import http_retry

try:
    from python_socks.async_.asyncio import Proxy as SocksProxy
    HAS_SOCKS = True
except ImportError:
    HAS_SOCKS = False

try:
    import websockets  # type: ignore
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False


_rich_message_available = True


def get_rich_message_available() -> bool:
    return _rich_message_available


def disable_rich_message() -> None:
    global _rich_message_available
    _rich_message_available = False
    log.info("sendRichMessage disabled for this session")


def parse_proxy(url: str) -> Optional[dict]:
    if not url:
        return None
    try:
        u = urlparse(url)
        scheme = (u.scheme or "").lower()
        if scheme not in ("socks5", "socks4", "http", "https"):
            return None
        host = u.hostname
        port = u.port
        if not host or not port:
            return None
        return {
            "scheme": scheme,
            "host": host,
            "port": int(port),
            "username": u.username,
            "password": u.password,
        }
    except Exception:
        return None


def get_proxy_url() -> Optional[str]:
    if not CONFIG.get("use_proxy"):
        return None
    return CONFIG.get("proxy_url") or "socks5://127.0.0.1:1080"


async def proxy_connector(ip: str, port: int, *, proxy_url: str):
    if not HAS_SOCKS:
        return await asyncio.open_connection(host=ip, port=port)
    proxy = SocksProxy.from_url(proxy_url)
    sock = await proxy.connect(dest_host=ip, dest_port=port)
    return await asyncio.open_connection(sock=sock)


@http_retry(max_tries=4, max_time=20.0)
def _probe_ipv4(proxies: dict) -> str:
    r = requests.get("https://api.ipify.org", timeout=8, proxies=proxies)
    r.raise_for_status()
    return r.text.strip()


@http_retry(max_tries=4, max_time=20.0)
def _probe_ipv6(proxies: dict) -> str:
    r = requests.get("https://api64.ipify.org", timeout=8, proxies=proxies)
    r.raise_for_status()
    return r.text.strip()


def test_proxy(proxy_url: Optional[str] = None) -> dict:
    url = proxy_url or CONFIG.get("proxy_url")
    parsed = parse_proxy(url or "")
    if not parsed:
        return {"ok": False, "ipv4": None, "ipv6": None,
                "error": "invalid proxy url"}

    scheme = parsed["scheme"]
    auth = ""
    if parsed["username"]:
        auth = f"{parsed['username']}:{parsed['password']}@"
    proxy_str = f"{scheme}://{auth}{parsed['host']}:{parsed['port']}"
    proxies = {"http": proxy_str, "https": proxy_str}

    result = {"ok": False, "ipv4": None, "ipv6": None, "error": None}
    try:
        result["ipv4"] = _probe_ipv4(proxies)
        result["ok"] = True
    except Exception as e:
        result["error"] = f"ipv4: {e}"
    try:
        result["ipv6"] = _probe_ipv6(proxies)
    except Exception as e:
        if not result["error"]:
            result["error"] = f"ipv6: {e}"
    return result


class SocksToWorkerBridge:
    """Minimal SOCKS5 server that pipes bytes to a CF Worker over WebSocket."""

    def __init__(self, listen_host: str = "127.0.0.1",
                 listen_port: int = 1080,
                 worker_domain: Optional[str] = None):
        self.host = listen_host
        self.port = listen_port
        self.worker = (worker_domain or CONFIG.get("cf_worker_domain") or "").strip()
        self._server: Optional[asyncio.AbstractServer] = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        if not HAS_WEBSOCKETS:
            log.error("websockets not installed; SOCKS5 bridge disabled")
            return
        if not self.worker:
            log.error("CF_WORKER_DOMAIN not set; SOCKS5 bridge disabled")
            return
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        self._running = True
        log.info(f"SOCKS5 bridge listening on {self.host}:{self.port} -> wss://{self.worker}")

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:
                pass
        self._running = False
        log.info("SOCKS5 bridge stopped")

    async def _handle(self, reader: asyncio.StreamReader,
                      writer: asyncio.StreamWriter) -> None:
        try:
            hdr = await reader.readexactly(2)
            if hdr[0] != 0x05:
                writer.close()
                return
            nmethods = hdr[1]
            await reader.readexactly(nmethods)
            writer.write(b"\x05\x00")
            await writer.drain()

            req = await reader.readexactly(4)
            if req[0] != 0x05 or req[1] != 0x01:
                writer.write(b"\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00")
                await writer.drain()
                writer.close()
                return
            atyp = req[3]
            if atyp == 0x01:
                dst_ip = socket.inet_ntoa(await reader.readexactly(4))
            elif atyp == 0x03:
                length = (await reader.readexactly(1))[0]
                dst_ip = (await reader.readexactly(length)).decode()
            elif atyp == 0x04:
                raw = await reader.readexactly(16)
                dst_ip = socket.inet_ntop(socket.AF_INET6, raw)
            else:
                writer.close()
                return
            dst_port = int.from_bytes(await reader.readexactly(2), "big")

            url = f"wss://{self.worker}/apiws?dst={dst_ip}&port={dst_port}"
            try:
                ws = await websockets.connect(
                    url, max_size=None, ping_interval=None, open_timeout=15
                )
            except Exception as e:
                log.warning(f"bridge ws connect failed ({url}): {e}")
                writer.write(b"\x05\x04\x00\x01\x00\x00\x00\x00\x00\x00")
                await writer.drain()
                writer.close()
                return

            writer.write(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
            await writer.drain()

            await asyncio.gather(
                self._pipe_reader_to_ws(reader, ws),
                self._pipe_ws_to_writer(ws, writer),
                return_exceptions=True,
            )

        except asyncio.IncompleteReadError:
            pass
        except Exception as e:
            log.debug(f"socks bridge conn error: {e}")
        finally:
            try:
                writer.close()
            except Exception:
                pass
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def _pipe_reader_to_ws(self, reader, ws) -> None:
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                await ws.send(data)
        except Exception:
            pass
        finally:
            try:
                await ws.close()
            except Exception:
                pass

    async def _pipe_ws_to_writer(self, ws, writer) -> None:
        try:
            async for msg in ws:
                if isinstance(msg, str):
                    msg = msg.encode()
                if not msg:
                    continue
                writer.write(msg)
                await writer.drain()
        except Exception:
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass