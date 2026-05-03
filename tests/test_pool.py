"""数据源连接池测试。

不真连数据库 —— factory.connect 用 fake，重点验证池行为：
- 同 datasource 连续 borrow → 复用同一连接
- 修 host/密码 → invalidate → 池清掉
- max_size 满 → 调用方拿到新连接但 release 时直接关闭（不入池）
- ping 失败 → 弃池重建
- iter_rows generator finally 触发 release
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from app.dbclients import pool
from app.models import DataSource, DatabaseType


def _ds(**overrides) -> DataSource:
    base = dict(
        id="ds-test",
        name="test",
        db_type=DatabaseType.MYSQL,
        host="h", port=3306, database="db", username="u", password="p",
        extra={},
    )
    base.update(overrides)
    return DataSource(**base)


@pytest.fixture(autouse=True)
def _clear_pool_state():
    pool.clear_all()
    yield
    pool.clear_all()


class _FakeConn:
    """伪驱动连接 —— 只支持 cursor() + close()，cursor.execute('SELECT 1') 走 ping。"""

    closed_count = 0

    def __init__(self, *, ping_ok=True):
        self._ping_ok = ping_ok
        self.closed = False

    def cursor(self):
        cur = MagicMock()
        if not self._ping_ok:
            cur.execute.side_effect = RuntimeError("server gone away")
        cur.fetchone.return_value = (1,)
        return cur

    def close(self):
        self.closed = True
        _FakeConn.closed_count += 1


def test_borrow_reuses_connection_for_same_datasource():
    ds = _ds()
    factory_calls = {"n": 0}

    def factory():
        factory_calls["n"] += 1
        return _FakeConn()

    with pool.borrow(ds, factory) as conn1:
        first = conn1
    # 第一次 release 后再 borrow 应复用
    with pool.borrow(ds, factory) as conn2:
        second = conn2

    assert first is second
    assert factory_calls["n"] == 1


def test_invalidate_clears_pool_and_reconnects():
    ds = _ds()
    factory_calls = {"n": 0}
    def factory():
        factory_calls["n"] += 1
        return _FakeConn()

    with pool.borrow(ds, factory):
        pass
    pool.invalidate(ds.id)
    with pool.borrow(ds, factory):
        pass

    assert factory_calls["n"] == 2  # invalidate 强制重建


def test_fingerprint_change_clears_pool():
    """改 host/port 等连接关键字段 → 同 id 但 fingerprint 变 → 旧池清掉。"""
    ds_old = _ds(host="h1")
    factory_old = lambda: _FakeConn()
    with pool.borrow(ds_old, factory_old):
        pass

    ds_new = _ds(host="h2")  # 同 id 不同 host
    factory_calls = {"n": 0}
    def factory():
        factory_calls["n"] += 1
        return _FakeConn()
    with pool.borrow(ds_new, factory):
        pass

    # 老的池被清，新池重建连接
    assert factory_calls["n"] == 1


def test_disable_pool_extra_skips_pooling():
    ds = _ds(extra={"disable_pool": True})
    factory_calls = {"n": 0}
    def factory():
        factory_calls["n"] += 1
        return _FakeConn()

    with pool.borrow(ds, factory):
        pass
    with pool.borrow(ds, factory):
        pass

    # 不池化 → 每次新建
    assert factory_calls["n"] == 2


def test_ping_failure_replaces_dead_connection():
    """池里旧连接 ping 失败时丢弃重建。"""
    ds = _ds()
    seq = [_FakeConn(ping_ok=False), _FakeConn(ping_ok=True)]
    seq_iter = iter(seq)
    def factory():
        return next(seq_iter)

    with pool.borrow(ds, factory):
        pass  # 第一次借第一个连接（ping_ok=False）
    # 第二次借时 ping 失败，丢弃，新建第二个
    with pool.borrow(ds, factory) as c:
        assert c is seq[1]


def test_broken_connection_not_returned_to_pool():
    """with 块抛异常 → broken=True → 连接不进池。"""
    ds = _ds()
    factory_calls = {"n": 0}
    def factory():
        factory_calls["n"] += 1
        return _FakeConn()

    with pytest.raises(RuntimeError):
        with pool.borrow(ds, factory):
            raise RuntimeError("query crash")

    # 重新借 → 新建（旧的不在池里）
    with pool.borrow(ds, factory):
        pass
    assert factory_calls["n"] == 2


def test_idle_ttl_expires_old_connection(monkeypatch):
    ds = _ds(extra={"pool_idle_seconds": 0.05})
    factory_calls = {"n": 0}
    def factory():
        factory_calls["n"] += 1
        return _FakeConn()

    with pool.borrow(ds, factory):
        pass
    time.sleep(0.1)
    with pool.borrow(ds, factory):
        pass

    assert factory_calls["n"] == 2
