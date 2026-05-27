"""统一配置加载器 —— 把 config/config.yml 的值灌进 os.environ。

设计原则:
- **后端代码零改动**:所有 service module 仍然 os.getenv(...),不需要改读 yml
- **env var 优先**:os.environ 已设的键不覆盖。docker compose / CI / 启动脚本 set
  的 env var 仍然是最高优先级,yml 只填没设的默认值
- **失败安全**:yml 文件不存在 / 解析失败 / 键不认识,全部 log warning 后继续启动,
  不让用户因为一个 typo 卡在启动阶段
- **静默幂等**:重复 load 没副作用(env var 已 set 就跳过),pytest fixture 友好

调用时机:必须在所有 service module import 前调用,因为很多 service 在 module-level
就 os.getenv 读配置(JWT_SECRET / RATELIMIT_ENFORCE / SCHEDULER_INTERVAL 等)。
所以 main.py 顶部 from __future__ 之后第一件事就是 import 本模块并 load_config()。

yml 结构示例(嵌套,内部 flatten 映射到 DATAOPS_* env var):

```yaml
env: prod
auth:
  jwt_secret: "xxxxx"
  jwt_ttl_seconds: 28800
guard:
  max_rows_per_side: 50000000
  query_timeout_seconds: 1800
  results_min_free_gb: 20
memory:
  cap_mb: 4096
```

映射规则在 _SCHEMA 表里集中维护,加新键改一处就好。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# yml key path → 环境变量名映射表。
# tuple key 表达嵌套路径(("auth", "jwt_secret") → DATAOPS_JWT_SECRET)。
# 加新键时在这里登记一行即可,_apply_value 会自动处理。
_SCHEMA: dict[tuple[str, ...], str] = {
    # 顶层环境
    ("env",): "DATAOPS_ENV",
    ("log_format",): "DATAOPS_LOG_FORMAT",
    # 认证
    ("auth", "jwt_secret"): "DATAOPS_JWT_SECRET",
    ("auth", "jwt_ttl_seconds"): "DATAOPS_JWT_TTL_SECONDS",
    ("auth", "refresh_ttl_seconds"): "DATAOPS_REFRESH_TTL_SECONDS",
    ("auth", "admin_password"): "DATAOPS_ADMIN_PASSWORD",
    ("auth", "bootstrap_admin_once"): "DATAOPS_BOOTSTRAP_ADMIN_ONCE",
    ("auth", "config_secret"): "DATAOPS_CONFIG_SECRET",
    # 资源 guard(磁盘 / 行数 / 任务超时)
    ("guard", "results_min_free_gb"): "DATAOPS_RESULTS_MIN_FREE_GB",
    ("guard", "results_max_disk_usage_percent"): "DATAOPS_RESULTS_MAX_DISK_USAGE_PERCENT",
    ("guard", "compare_auto_stream_bytes"): "DATAOPS_COMPARE_AUTO_STREAM_BYTES",
    ("guard", "compare_deny_bytes"): "DATAOPS_COMPARE_DENY_BYTES",
    ("guard", "compare_writer_flush_bytes"): "DATAOPS_COMPARE_WRITER_FLUSH_BYTES",
    ("guard", "db_statement_timeout_seconds"): "DATAOPS_DB_STATEMENT_TIMEOUT_SECONDS",
    ("guard", "sql_preflight_enforce"): "DATAOPS_SQL_PREFLIGHT_ENFORCE",
    ("guard", "disable_legacy_result_path"): "DATAOPS_DISABLE_LEGACY_RESULT_PATH",
    # 内存 guard
    ("memory", "guard_mode"): "DATAOPS_MEMORY_GUARD_MODE",
    ("memory", "soft_limit_mb"): "DATAOPS_MEMORY_SOFT_LIMIT_MB",
    ("memory", "hard_limit_mb"): "DATAOPS_MEMORY_HARD_LIMIT_MB",
    # Rate limit
    ("rate_limit", "enforce"): "DATAOPS_RATELIMIT_ENFORCE",
    ("rate_limit", "login_per_min"): "DATAOPS_RATELIMIT_LOGIN_PER_MIN",
    ("rate_limit", "user_per_min"): "DATAOPS_RATELIMIT_USER_PER_MIN",
    # 调度器
    ("scheduler", "enabled"): "DATAOPS_SCHEDULER_ENABLED",
    ("scheduler", "interval_seconds"): "DATAOPS_SCHEDULER_INTERVAL_SECONDS",
    ("scheduler", "max_retries"): "DATAOPS_SCHEDULER_MAX_RETRIES",
    ("scheduler", "sensor_interval_seconds"): "DATAOPS_SENSOR_INTERVAL_SECONDS",
    ("scheduler", "sensor_cooldown_seconds"): "DATAOPS_SENSOR_COOLDOWN_SECONDS",
    # Jobs
    ("jobs", "ttl_seconds"): "DATAOPS_JOB_TTL_SECONDS",
    ("jobs", "max_retries"): "DATAOPS_JOB_MAX_RETRIES",
    # 下载 token
    ("download", "url_ttl_seconds"): "DATAOPS_DOWNLOAD_URL_TTL_SECONDS",
    # 通知 (webhook / wecom / email)
    ("notify", "timeout_seconds"): "DATAOPS_NOTIFY_TIMEOUT_SECONDS",
    ("notify", "webhook_url"): "DATAOPS_NOTIFY_WEBHOOK_URL",
    ("notify", "wecom_webhook"): "DATAOPS_NOTIFY_WECOM_WEBHOOK",
    ("notify", "email_to"): "DATAOPS_NOTIFY_EMAIL_TO",
    ("notify", "smtp_host"): "DATAOPS_SMTP_HOST",
    ("notify", "smtp_port"): "DATAOPS_SMTP_PORT",
    ("notify", "smtp_from"): "DATAOPS_SMTP_FROM",
    ("notify", "smtp_user"): "DATAOPS_SMTP_USER",
    ("notify", "smtp_password"): "DATAOPS_SMTP_PASSWORD",
    ("notify", "smtp_tls"): "DATAOPS_SMTP_TLS",
    # OpenLineage
    ("openlineage", "webhook_url"): "DATAOPS_OPENLINEAGE_WEBHOOK_URL",
    ("openlineage", "namespace"): "DATAOPS_OPENLINEAGE_NAMESPACE",
    ("openlineage", "timeout_seconds"): "DATAOPS_OPENLINEAGE_TIMEOUT_SECONDS",
    ("openlineage", "marquez_url"): "DATAOPS_MARQUEZ_URL",
    ("openlineage", "datahub_url"): "DATAOPS_DATAHUB_URL",
    ("openlineage", "datahub_token"): "DATAOPS_DATAHUB_TOKEN",
    # Lineage AI
    ("ai", "provider"): "DATAOPS_LINEAGE_AI_PROVIDER",
    ("ai", "api_key"): "DATAOPS_LINEAGE_AI_API_KEY",
    ("ai", "model"): "DATAOPS_LINEAGE_AI_MODEL",
    ("ai", "base_url"): "DATAOPS_LINEAGE_AI_BASE_URL",
    ("ai", "timeout_seconds"): "DATAOPS_LINEAGE_AI_TIMEOUT_SECONDS",
    ("ai", "max_tokens"): "DATAOPS_LINEAGE_AI_MAX_TOKENS",
    ("ai", "include_raw"): "DATAOPS_LINEAGE_AI_INCLUDE_RAW",
    ("ai", "enable_inference"): "DATAOPS_LINEAGE_AI_ENABLE_INFERENCE",
    ("ai", "enable_auto_translation"): "DATAOPS_LINEAGE_AI_ENABLE_AUTO_TRANSLATION",
}


def _stringify(value: Any) -> str:
    """yml 解析后值可能是 int / float / bool / str / None,统一转成 env var 用的字符串。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        # yaml 把 yes/no/true/false 都解析成 bool,env var 用 "true"/"false" 小写串
        return "true" if value else "false"
    return str(value)


def _walk(prefix: tuple[str, ...], node: Any, out: dict[tuple[str, ...], Any]) -> None:
    """递归把嵌套 dict flatten 成 path tuple → value。"""
    if isinstance(node, dict):
        for k, v in node.items():
            _walk(prefix + (str(k),), v, out)
    else:
        out[prefix] = node


def load_config(config_path: str | os.PathLike[str] | None = None) -> dict[str, str]:
    """读 yml 配置 + 灌进 os.environ。返回实际生效的 key→value 映射(给测试 / debug 看)。

    config_path 不传 → 默认 ./config/config.yml(相对 cwd)。文件不存在直接返回 {},
    不报错(纯可选机制)。
    """
    path = Path(config_path) if config_path else Path("config") / "config.yml"

    if not path.exists():
        logger.debug("config.yml not found at %s, skipping yml-based config", path)
        return {}

    try:
        import yaml  # 延迟 import 避免没装 PyYAML 时启动失败
    except ImportError:
        logger.warning("PyYAML not installed, cannot load %s", path)
        return {}

    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except yaml.YAMLError as exc:
        logger.warning("config.yml parse error at %s: %s", path, exc)
        return {}
    except OSError as exc:
        logger.warning("config.yml read error at %s: %s", path, exc)
        return {}

    if not isinstance(data, dict):
        logger.warning("config.yml root must be a mapping, got %s", type(data).__name__)
        return {}

    flat: dict[tuple[str, ...], Any] = {}
    _walk((), data, flat)

    applied: dict[str, str] = {}
    unknown: list[str] = []

    for path_tuple, raw_value in flat.items():
        env_name = _SCHEMA.get(path_tuple)
        if env_name is None:
            unknown.append(".".join(path_tuple))
            continue
        # env var 优先 —— 已 set 的不覆盖,yml 只填空缺。这样 docker compose env /
        # CI / 启动脚本 set 的值是最高优先级。
        if os.environ.get(env_name):
            continue
        if raw_value is None:
            # yml `key: null` 视同没写,跳过
            continue
        value_str = _stringify(raw_value)
        if value_str == "":
            # 空字符串 yml 值跳过,免得把 OPTIONAL env 强行 set 成 "" 改变行为
            continue
        os.environ[env_name] = value_str
        applied[env_name] = value_str

    if unknown:
        logger.warning(
            "config.yml has %d unknown key(s) (ignored): %s",
            len(unknown), ", ".join(sorted(unknown)[:10]) + (" ..." if len(unknown) > 10 else ""),
        )

    if applied:
        # 敏感字段日志脱敏 —— jwt_secret / api_key / smtp_password 不打到日志里
        sensitive = {"DATAOPS_JWT_SECRET", "DATAOPS_LINEAGE_AI_API_KEY", "DATAOPS_SMTP_PASSWORD",
                     "DATAOPS_ADMIN_PASSWORD", "DATAOPS_CONFIG_SECRET", "DATAOPS_DATAHUB_TOKEN"}
        masked = {k: ("***" if k in sensitive else v) for k, v in applied.items()}
        logger.info("config.yml applied %d env var(s): %s", len(applied), masked)

    return applied
