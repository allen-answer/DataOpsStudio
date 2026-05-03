"""每数据源 LIFO 连接池。

目标：减少 compare task / preview / lineage analyze 频繁建连接的开销
（每次 pymysql.connect 含 TCP 三次握手 + 鉴权往返，本地 ~5ms，跨机房 100ms+）。

设计原则：
- 我们只跑只读 SELECT —— 连接复用安全，不会有 dirty session state（主流驱动
  cursor.execute 之间已经隔离）。
- 每 `datasource_id` 单独一个池。修改 datasource 配置（host / 密码）后池被清空
  让连接重新建立。
- LIFO（栈）回收：最近还的连接最近用 —— 维持连接热度，超时连接最容易先空闲老化。
- max_size 默认 4：跟 ThreadPoolExecutor(max_workers=2) 协调；workflow 节点级
  并发 + 主线程 preview 同时占用最多 ~3 个 worker，留一个余量。
- TTL：连接放回池里超过 idle_seconds 视作过期，acquire 时丢掉重建。MySQL
  服务端 wait_timeout 默认 28800s，远大于这里 600s，不会撞墙。
- acquire 拿不到（pool 已满 + 全在用）就直接 driver.connect() 新建一个，调用方
  release 时如果池也满了就 close —— 不阻塞调用方排队。
- ping：连接拿出来前用 cursor.execute('SELECT 1') 试探（成本远低于建连接）；
  ping 失败丢弃重建。**仅 MySQL** 走 ping 路径——其它驱动 ping 行为不同，
  保守起见走"acquire 直接用 + release 异常时弃池"。
- 配置 `extra.disable_pool=true` 跳过池（troubleshoot / 老应用兼容）。
"""
from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable

from app.models import DataSource, DatabaseType

logger = logging.getLogger(__name__)

DEFAULT_MAX_SIZE = 4
DEFAULT_IDLE_SECONDS = 600  # 连接空闲 10 分钟后丢弃


@dataclass
class _PooledConnection:
    conn: Any
    created_at: float
    returned_at: float


class _DatasourcePool:
    """单数据源池。LIFO 栈，配 lock + idle TTL。"""

    def __init__(self, max_size: int, idle_seconds: float) -> None:
        self.max_size = max_size
        self.idle_seconds = idle_seconds
        self._stack: list[_PooledConnection] = []
        self._in_use = 0
        self._lock = threading.Lock()

    def acquire(self) -> _PooledConnection | None:
        """尝试从池里拿一个未过期的连接，没有时返 None（让调用方走新建路径）。"""
        now = time.monotonic()
        with self._lock:
            while self._stack:
                item = self._stack.pop()
                if now - item.returned_at > self.idle_seconds:
                    _safe_close(item.conn)
                    continue
                self._in_use += 1
                return item
            return None

    def release(self, item: _PooledConnection, *, broken: bool = False) -> None:
        with self._lock:
            self._in_use = max(0, self._in_use - 1)
            if broken:
                _safe_close(item.conn)
                return
            if len(self._stack) >= self.max_size:
                _safe_close(item.conn)
                return
            item.returned_at = time.monotonic()
            self._stack.append(item)

    def register_external(self) -> _PooledConnection:
        """池满时调用方自己新建的连接也要登记，避免 _in_use 漏算。"""
        with self._lock:
            self._in_use += 1
        return _PooledConnection(conn=None, created_at=time.monotonic(), returned_at=0.0)

    def clear(self) -> None:
        with self._lock:
            stack, self._stack = self._stack, []
        for item in stack:
            _safe_close(item.conn)


def _safe_close(conn: Any) -> None:
    if conn is None:
        return
    try:
        conn.close()
    except Exception:
        pass


# 全局池表：datasource_id → fingerprint → _DatasourcePool。
# fingerprint 包含 host/port/user/database —— 改 datasource 配置后旧池失效。
_pools: dict[str, tuple[str, _DatasourcePool]] = {}
_pools_lock = threading.Lock()


def _datasource_fingerprint(source: DataSource) -> str:
    return f"{source.db_type.value}|{source.host}|{source.port}|{source.username}|{source.database}"


def _get_pool(source: DataSource) -> _DatasourcePool | None:
    """池化关闭时返 None。"""
    if source.extra.get("disable_pool"):
        return None
    fingerprint = _datasource_fingerprint(source)
    with _pools_lock:
        existing = _pools.get(source.id)
        if existing and existing[0] == fingerprint:
            return existing[1]
        # 配置变了：清旧池
        if existing:
            existing[1].clear()
        max_size = int(source.extra.get("pool_max_size", DEFAULT_MAX_SIZE))
        idle = float(source.extra.get("pool_idle_seconds", DEFAULT_IDLE_SECONDS))
        pool = _DatasourcePool(max_size=max(1, max_size), idle_seconds=max(0.0, idle))
        _pools[source.id] = (fingerprint, pool)
        return pool


def invalidate(source_id: str) -> None:
    """删 datasource / 修密码后清池。services/datasource_store 删/改时调。"""
    with _pools_lock:
        existing = _pools.pop(source_id, None)
    if existing:
        existing[1].clear()


def clear_all() -> None:
    """测试 / 手动维护用。"""
    with _pools_lock:
        existing, _pools_clear = list(_pools.values()), _pools.clear()
    for _, pool in existing:
        pool.clear()


@contextmanager
def borrow(source: DataSource, connect_factory: Callable[[], Any]):
    """从池里借一个连接，with 块结束自动 release。

    pool 关闭 / 池里无可用 / acquire 失败时直接 connect_factory() 新建，
    使用完就 close（不进池）。这样能保证调用方不被池阻塞。
    """
    pool = _get_pool(source)
    if pool is None:
        conn = connect_factory()
        try:
            yield conn
        finally:
            _safe_close(conn)
        return

    item = pool.acquire()
    if item is not None:
        # 拿到旧连接，先 ping 一下验活（仅 MySQL 实现，其它驱动 yield 后异常路径回收）
        if source.db_type == DatabaseType.MYSQL and not _ping_mysql(item.conn):
            _safe_close(item.conn)
            item = None

    if item is None:
        # 新建连接（不一定能进池，等 release 时决定）
        conn = connect_factory()
        item = _PooledConnection(conn=conn, created_at=time.monotonic(), returned_at=0.0)
        # 占 _in_use 槽位（让下次 acquire 知道有连接在外）—— 统计用，不阻塞
        pool.register_external()

    broken = False
    try:
        yield item.conn
    except Exception:
        broken = True
        raise
    finally:
        pool.release(item, broken=broken)


def _ping_mysql(conn: Any) -> bool:
    try:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        finally:
            try:
                cursor.close()
            except Exception:
                pass
        return True
    except Exception:
        logger.debug("pool ping failed, will rebuild")
        return False
