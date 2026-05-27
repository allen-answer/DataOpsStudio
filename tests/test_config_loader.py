"""config_loader 单测 —— 验证 yml 加载、env 优先、错误隔离、敏感字段脱敏。"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from app.config_loader import _SCHEMA, load_config


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """测试前后都清掉本模块涉及的所有 env var,避免外部污染 / 测试间污染。

    注意:load_config() 直接 os.environ[...] = ... 绕过 monkeypatch,所以
    需要 yield 后手动清理 —— 不然这个 module 的测试会污染下游测试(比如
    test_resource_guard 也读 DATAOPS_ENV)。
    """
    schema_envs = list(_SCHEMA.values())
    saved = {k: os.environ.get(k) for k in schema_envs}
    for k in schema_envs:
        monkeypatch.delenv(k, raising=False)
    yield
    # 还原:测试期间被 load_config 写入的 env 都恢复成 fixture 进入时的状态
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _write_yml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "config.yml"
    p.write_text(content, encoding="utf-8")
    return p


def test_load_simple_values(tmp_path):
    """基本 happy path —— 顶层 + 嵌套都能映射成 env var。"""
    p = _write_yml(tmp_path, """
env: prod
auth:
  jwt_secret: my-secret-abc
  jwt_ttl_seconds: 7200
guard:
  results_min_free_gb: 20
""")
    applied = load_config(p)
    assert applied["DATAOPS_ENV"] == "prod"
    assert applied["DATAOPS_JWT_SECRET"] == "my-secret-abc"
    assert applied["DATAOPS_JWT_TTL_SECONDS"] == "7200"
    assert applied["DATAOPS_RESULTS_MIN_FREE_GB"] == "20"
    # 实际灌进 os.environ 了
    assert os.environ["DATAOPS_ENV"] == "prod"


def test_env_var_takes_precedence(tmp_path, monkeypatch):
    """已 set 的 env var 不被 yml 覆盖 —— docker compose / CI 设的值最高优先级。"""
    monkeypatch.setenv("DATAOPS_JWT_SECRET", "from-env")
    p = _write_yml(tmp_path, """
auth:
  jwt_secret: from-yml
""")
    applied = load_config(p)
    assert "DATAOPS_JWT_SECRET" not in applied
    assert os.environ["DATAOPS_JWT_SECRET"] == "from-env"


def test_missing_file_no_error(tmp_path):
    """文件不存在静默返回空 dict —— 纯可选机制,不让缺文件卡启动。"""
    applied = load_config(tmp_path / "does-not-exist.yml")
    assert applied == {}


def test_yaml_parse_error_logged_not_raised(tmp_path, caplog):
    """坏 yml 不抛 —— log warning 后继续启动,免得一个 typo 让服务起不来。"""
    p = _write_yml(tmp_path, "env: prod\n  bad indent: oops")
    with caplog.at_level(logging.WARNING, logger="app.config_loader"):
        applied = load_config(p)
    assert applied == {}
    assert any("parse error" in r.message for r in caplog.records)


def test_unknown_keys_logged_and_ignored(tmp_path, caplog):
    """未知键不抛错,log warning 提示 typo。"""
    p = _write_yml(tmp_path, """
env: dev
typo_section:
  some_key: 123
""")
    with caplog.at_level(logging.WARNING, logger="app.config_loader"):
        applied = load_config(p)
    assert applied["DATAOPS_ENV"] == "dev"
    assert any("unknown key" in r.message for r in caplog.records)


def test_bool_serialized_as_lowercase_string(tmp_path):
    """yml true/false → 'true'/'false',因为 service 代码用 .lower() in {"true", "1"} 判定。"""
    p = _write_yml(tmp_path, """
rate_limit:
  enforce: true
scheduler:
  enabled: false
""")
    applied = load_config(p)
    assert applied["DATAOPS_RATELIMIT_ENFORCE"] == "true"
    assert applied["DATAOPS_SCHEDULER_ENABLED"] == "false"


def test_empty_string_value_skipped(tmp_path):
    """yml 显式空字符串不灌 env(否则会把 OPTIONAL env 变成空串改变行为)。"""
    p = _write_yml(tmp_path, """
auth:
  jwt_secret: ""
""")
    applied = load_config(p)
    # 空字符串跳过(raw_value 是空 str 但不是 None)
    assert "DATAOPS_JWT_SECRET" not in applied
    assert os.environ.get("DATAOPS_JWT_SECRET", "<unset>") == "<unset>"


def test_null_value_skipped(tmp_path):
    """yml null 等于不写,完全跳过。"""
    p = _write_yml(tmp_path, """
auth:
  jwt_secret: null
""")
    applied = load_config(p)
    assert "DATAOPS_JWT_SECRET" not in applied


def test_sensitive_fields_masked_in_log(tmp_path, caplog):
    """jwt_secret / api_key / admin_password 不打到日志。"""
    p = _write_yml(tmp_path, """
auth:
  jwt_secret: super-sensitive-token
  admin_password: my-password
ai:
  api_key: sk-very-secret
""")
    with caplog.at_level(logging.INFO, logger="app.config_loader"):
        load_config(p)
    log_text = " ".join(r.message for r in caplog.records)
    assert "super-sensitive-token" not in log_text
    assert "my-password" not in log_text
    assert "sk-very-secret" not in log_text
    assert "***" in log_text


def test_non_mapping_root_rejected(tmp_path, caplog):
    """yml 根是 list / 标量 → log warning + 返回空。"""
    p = _write_yml(tmp_path, "- foo\n- bar\n")
    with caplog.at_level(logging.WARNING, logger="app.config_loader"):
        applied = load_config(p)
    assert applied == {}
    assert any("must be a mapping" in r.message for r in caplog.records)


def test_int_values_serialized(tmp_path):
    """int 值 → "1234" 字符串,跟 env var 用法对齐。"""
    p = _write_yml(tmp_path, """
guard:
  results_min_free_gb: 50
  db_statement_timeout_seconds: 1800
""")
    applied = load_config(p)
    assert applied["DATAOPS_RESULTS_MIN_FREE_GB"] == "50"
    assert applied["DATAOPS_DB_STATEMENT_TIMEOUT_SECONDS"] == "1800"


def test_idempotent(tmp_path):
    """重复 load 没副作用 —— 第二次 load 时所有 key 已被第一次 set,新增 applied 为空。"""
    p = _write_yml(tmp_path, """
env: dev
auth:
  jwt_secret: x
""")
    first = load_config(p)
    second = load_config(p)
    assert "DATAOPS_ENV" in first
    assert second == {}  # 第二次没新增,因为 env var 已 set


def test_default_path_when_no_arg(tmp_path, monkeypatch):
    """不传 config_path → 默认读 ./config/config.yml(相对 cwd)。"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "config.yml").write_text("env: stage\n", encoding="utf-8")
    applied = load_config()
    assert applied["DATAOPS_ENV"] == "stage"


def test_schema_covers_all_documented_keys():
    """_SCHEMA 必须涵盖 config.yml.example 里所有用到的键(防 example 和 loader 失配)。

    用 example 文件 + 简单 yaml 解析做契约测试。
    """
    import yaml
    example_path = Path(__file__).resolve().parent.parent / "config" / "config.yml.example"
    if not example_path.exists():
        pytest.skip("config.yml.example not present")
    with example_path.open() as fh:
        data = yaml.safe_load(fh) or {}

    def collect_paths(prefix: tuple, node):
        if isinstance(node, dict):
            for k, v in node.items():
                yield from collect_paths(prefix + (k,), v)
        else:
            yield prefix

    example_keys = set(collect_paths((), data))
    missing = example_keys - set(_SCHEMA.keys())
    assert not missing, f"config.yml.example 用了 _SCHEMA 没登记的键: {missing}"
