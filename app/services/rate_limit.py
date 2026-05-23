"""Auth endpoint rate limiter —— 防爆破。

本应用部署在公网 IP(自签 HTTPS),最现实的攻击是机器人扫 admin 密码。
bcrypt verify ~100ms 一次 + 单进程 CPU,无 rate limit 时持续 brute force
能把服务器 CPU 打满到 100%。

设计：
- 单进程 in-memory sliding window —— 用 deque[timestamp] 维护每 key 的命中
  时间戳,过期戳左侧 popleft。够 dev / 单机部署用;多 worker 部署需 redis。
- 双 key:per-IP + per-username。per-IP 防单机暴力,per-username 防分布式
  扫单一账号(僵尸网络从 N 个 IP 试同一 admin 账号)。
- 命中 429 + Retry-After header(秒级)。
- env `DATAOPS_RATELIMIT_ENFORCE=false` 临时关(默认 true)。
- env `DATAOPS_RATELIMIT_LOGIN_PER_MIN`(默认 10) /
  `DATAOPS_RATELIMIT_USER_PER_MIN`(默认 5) 阈值。
- 命中写指标 `auth_rate_limit_hits_total{endpoint,key_type}`。
"""
from __future__ import annotations

import logging
import os
import threading
from collections import defaultdict, deque
from dataclasses import dataclass
from time import monotonic

from fastapi import Request

logger = logging.getLogger(__name__)

# ─── env ──
_RATELIMIT_ENFORCE = os.getenv("DATAOPS_RATELIMIT_ENFORCE", "true").lower() != "false"
_LOGIN_LIMIT_PER_MIN = int(os.getenv("DATAOPS_RATELIMIT_LOGIN_PER_MIN", "10"))
_USER_LIMIT_PER_MIN = int(os.getenv("DATAOPS_RATELIMIT_USER_PER_MIN", "5"))
_WINDOW_SECONDS = 60.0


@dataclass
class CheckResult:
    allowed: bool
    retry_after: float = 0.0     # seconds 距离最早一个 hit 出窗
    remaining: int = 0            # 窗口内还剩多少额度
    key_type: str = ""            # "ip" / "user" / "" —— 命中时报哪条限速


class RateLimiter:
    """Sliding-window counter rate limiter。

    `check(key)` 一次性原子地:把过期戳从 deque 左侧 popleft → 看当前 count
    是否达上限 → 未达就 append 当前 now 戳 + 返 allowed=True;达上限返
    allowed=False(不写戳,不污染窗口)。`threading.Lock` 同步同 key 写。
    """

    def __init__(self, limit: int, window_seconds: float) -> None:
        if limit < 1:
            raise ValueError(f"limit must be >= 1, got {limit}")
        if window_seconds <= 0:
            raise ValueError(f"window_seconds must be > 0, got {window_seconds}")
        self.limit = limit
        self.window = window_seconds
        self._store: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> CheckResult:
        now = monotonic()
        cutoff = now - self.window
        with self._lock:
            q = self._store[key]
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= self.limit:
                retry_after = self.window - (now - q[0])
                return CheckResult(
                    allowed=False,
                    retry_after=max(0.1, retry_after),
                    remaining=0,
                )
            q.append(now)
            return CheckResult(allowed=True, remaining=self.limit - len(q))

    def reset(self, key: str | None = None) -> None:
        """重置一个 key 或全部 —— 测试 / 管理员手工清单时用。"""
        with self._lock:
            if key is None:
                self._store.clear()
            else:
                self._store.pop(key, None)


# 全局 limiter —— 模块加载时按 env 初始化;测试用 reset_all_limiters() 清状态
login_ip_limiter = RateLimiter(_LOGIN_LIMIT_PER_MIN, _WINDOW_SECONDS)
login_user_limiter = RateLimiter(_USER_LIMIT_PER_MIN, _WINDOW_SECONDS)


def reset_all_limiters() -> None:
    """测试间 fixture 用 —— 把所有 limiter 状态清空,避免互相串话。"""
    login_ip_limiter.reset()
    login_user_limiter.reset()


def client_ip_from_request(request: Request) -> str:
    """提取客户端 IP。

    本应用通常部署在 nginx-rp 反代后,nginx 加 `X-Forwarded-For: <client_ip>`
    头(可能含多跳 `client, proxy1, proxy2`)。我们信任最左 IP 是原始客户端
    ——单层反代场景成立。直连 / 无 XFF 时回退 `request.client.host`。

    安全注意:XFF 是 header,任何客户端都能伪造。生产环境必须确保只有可信
    反代能直达,且反代覆写 / 加 XFF。本应用默认部署假设这点成立。
    """
    xff = request.headers.get("x-forwarded-for") or request.headers.get("X-Forwarded-For")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def check_auth_rate_limit(
    request: Request,
    *,
    username: str | None = None,
    endpoint: str = "login",
) -> CheckResult | None:
    """检 IP + (可选) username 双限速。

    命中任一即返 `CheckResult(allowed=False, ...)`,caller 应抛 429 +
    Retry-After header。两侧都过返 `CheckResult(allowed=True, ...)`。
    rate limit 关闭(env DATAOPS_RATELIMIT_ENFORCE=false)直接返 None,
    caller 把它当 allow 处理。

    `endpoint` 是字符串(如 "login" / "mfa_challenge"),用来做 key namespace
    隔离 —— 同 IP 在 login 和 mfa 之间额度独立。
    """
    if not _RATELIMIT_ENFORCE:
        return None
    ip = client_ip_from_request(request)
    ip_result = login_ip_limiter.check(f"{endpoint}:ip:{ip}")
    if not ip_result.allowed:
        ip_result.key_type = "ip"
        _record_hit(endpoint, "ip", ip, username)
        return ip_result
    if username:
        # username 走 lowercase 归一,防 'Admin' / 'admin' 大小写绕过
        user_result = login_user_limiter.check(f"{endpoint}:user:{username.lower()}")
        if not user_result.allowed:
            user_result.key_type = "user"
            _record_hit(endpoint, "user", ip, username)
            return user_result
    return CheckResult(allowed=True, remaining=ip_result.remaining, key_type="ok")


def _record_hit(endpoint: str, key_type: str, ip: str, username: str | None) -> None:
    """限速命中时打指标 + 日志。"""
    try:
        from app.services.metrics import auth_rate_limit_hits_total

        auth_rate_limit_hits_total.inc(labels={"endpoint": endpoint, "key_type": key_type})
    except Exception:  # noqa: BLE001 —— metrics 失败不阻塞主路径
        pass
    # 日志脱敏:IP 留前两段（"192.168.x.x"）/ username 留 prefix
    masked_ip = _mask_ip(ip)
    masked_user = _mask_username(username)
    logger.warning(
        "auth rate limit hit endpoint=%s key_type=%s ip=%s username=%s",
        endpoint, key_type, masked_ip, masked_user,
    )


def _mask_ip(ip: str) -> str:
    """`192.168.1.42` → `192.168.x.x`;IPv6 同理只保留前 32 bit。"""
    if not ip or ip == "unknown":
        return ip or "unknown"
    if "." in ip:
        parts = ip.split(".")
        if len(parts) == 4:
            return f"{parts[0]}.{parts[1]}.x.x"
    if ":" in ip:
        parts = ip.split(":")
        return ":".join(parts[:2] + ["x"] * (len(parts) - 2)) if len(parts) > 2 else ip
    return ip


def _mask_username(username: str | None) -> str:
    """`adminuser` → `ad***er`;短名直接 `***`。审计日志里别完整打用户名,
    避免一旦日志泄露暴露用户名字典。"""
    if not username:
        return "-"
    s = str(username).strip()
    if len(s) <= 4:
        return "***"
    return f"{s[:2]}***{s[-2:]}"
