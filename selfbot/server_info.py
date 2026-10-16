"""Server info: real IP, proxy IP, hostname, uptime, system stats."""
import os
import platform
import socket
import time
from datetime import datetime, timezone as dt_timezone
from typing import Optional

import requests

from .logging_setup import log

_start_time = time.time()

try:
    import backoff
    HAS_BACKOFF = True
except ImportError:
    HAS_BACKOFF = False


def _fetch_public_ip(url: str = "https://api.ipify.org",
                     proxy: str = None, timeout: float = 8.0) -> Optional[str]:
    try:
        proxies = None
        if proxy:
            proxies = {"http": proxy, "https": proxy}
        r = requests.get(url, timeout=timeout, proxies=proxies)
        r.raise_for_status()
        return r.text.strip()
    except Exception as e:
        log.warning(f"ip fetch failed ({url}, proxy={proxy}): {e}")
        return None


def get_direct_ip() -> Optional[str]:
    return _fetch_public_ip("https://api.ipify.org", proxy=None)


def get_proxy_ip(proxy_url: str = None) -> Optional[str]:
    from .config import CONFIG
    if proxy_url is None:
        proxy_url = CONFIG.get("proxy_url") or "socks5://127.0.0.1:1080"
    socks5h = proxy_url.replace("socks5://", "socks5h://")
    return _fetch_public_ip("https://api.ipify.org", proxy=socks5h)


def get_ip_country(ip: str) -> str:
    if not ip:
        return "—"
    try:
        r = requests.get(
            f"http://ip-api.com/json/{ip}?fields=country,countryCode,city,isp",
            timeout=6,
        )
        data = r.json()
        if data.get("country"):
            parts = [data.get("country")]
            if data.get("city"):
                parts.append(data["city"])
            if data.get("isp"):
                parts.append(data["isp"])
            return " · ".join(str(p) for p in parts if p)
    except Exception:
        pass
    return "—"


def get_hostname() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return "unknown"


def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        return "unknown"


def get_uptime_seconds() -> float:
    return time.time() - _start_time


def get_uptime_human() -> str:
    secs = int(get_uptime_seconds())
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    if minutes or hours or days:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    return " ".join(parts)


def get_system_stats() -> dict:
    stats = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "machine": platform.machine(),
        "processor": platform.processor() or "—",
    }
    try:
        import psutil  # type: ignore
        stats["cpu_percent"] = psutil.cpu_percent(interval=0.3)
        vm = psutil.virtual_memory()
        stats["ram_total_mb"] = round(vm.total / 1024 / 1024, 1)
        stats["ram_used_mb"] = round(vm.used / 1024 / 1024, 1)
        stats["ram_percent"] = vm.percent
        du = psutil.disk_usage("/")
        stats["disk_total_gb"] = round(du.total / 1024 / 1024 / 1024, 2)
        stats["disk_used_gb"] = round(du.used / 1024 / 1024 / 1024, 2)
        stats["disk_percent"] = du.percent
    except ImportError:
        stats["cpu_percent"] = None
        stats["ram_percent"] = None
        stats["disk_percent"] = None
    return stats


def get_full_info(proxy_url: str = None) -> dict:
    from .config import CONFIG
    if proxy_url is None:
        proxy_url = CONFIG.get("proxy_url") or "socks5://127.0.0.1:1080"
    use_proxy = bool(CONFIG.get("use_proxy"))

    direct_ip = get_direct_ip()
    proxy_ip = get_proxy_ip(proxy_url) if use_proxy else None

    return {
        "hostname": get_hostname(),
        "local_ip": get_local_ip(),
        "direct_ip": direct_ip,
        "direct_country": get_ip_country(direct_ip) if direct_ip else "—",
        "proxy_url": proxy_url,
        "proxy_ip": proxy_ip,
        "proxy_country": get_ip_country(proxy_ip) if proxy_ip else "—",
        "use_proxy": use_proxy,
        "cf_worker": CONFIG.get("cf_worker_domain") or "—",
        "uptime": get_uptime_human(),
        "uptime_seconds": round(get_uptime_seconds(), 1),
        "started_at": datetime.fromtimestamp(
            _start_time, tz=dt_timezone.utc
        ).isoformat(),
        "system": get_system_stats(),
        "checked_at": datetime.now(dt_timezone.utc).isoformat(),
    }


def format_telegram_text(info: dict) -> str:
    lines = [
        "**🖥 Server Info**",
        "",
        f"**Hostname:** `{info['hostname']}`",
        f"**Local IP:** `{info['local_ip']}`",
        f"**Uptime:** {info['uptime']}",
        "",
        "**🌐 Direct:**",
        f"  IP: `{info['direct_ip'] or '—'}`",
        f"  Country: {info['direct_country']}",
        "",
        f"**🔒 Proxy ({'active' if info['use_proxy'] else 'disabled'}):**",
        f"  URL: `{info['proxy_url']}`",
        f"  Exit IP: `{info['proxy_ip'] or '—'}`",
        f"  Country: {info['proxy_country']}",
        "",
        f"**☁️ CF Worker:** `{info['cf_worker']}`",
    ]
    sysinfo = info.get("system") or {}
    if sysinfo.get("cpu_percent") is not None:
        lines.extend([
            "",
            "**📊 System:**",
            f"  CPU: {sysinfo['cpu_percent']}%",
            f"  RAM: {sysinfo['ram_percent']}% "
            f"({sysinfo.get('ram_used_mb', '?')}/{sysinfo.get('ram_total_mb', '?')} MB)",
            f"  Disk: {sysinfo['disk_percent']}% "
            f"({sysinfo.get('disk_used_gb', '?')}/{sysinfo.get('disk_total_gb', '?')} GB)",
        ])
    return "\n".join(lines)