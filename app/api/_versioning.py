"""API v1 alias 注入 —— 给所有 `/api/X` 路由克隆 `/api/v1/X` 同义版本。

设计目标：
- 现在还没有外部调用方（前端 + tests 都是内部），所以现在改造成本最低
- 旧 `/api/...` 路径继续可用（unversioned alias），让老代码 / 第三方文档 /
  bookmarks 不破
- 前端 `api.js` 之后可以切到 `/api/v1/...`，OpenAPI / Swagger 自动按 v1 分 tag
- 未来 v2 出现时，给 v1 设 deprecation window：v1 标 deprecated=True 后再下线

实现：复用 FastAPI 已经构建好的 APIRoute 对象（保持完整 response_model /
dependencies / status_code / 等元数据），用 `add_api_route` 重新 register
带 v1 前缀的版本。注入 `v1` tag 让 OpenAPI 显示为单独分组。
"""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.routing import APIRoute


logger = logging.getLogger(__name__)


_API_PREFIX = "/api/"
_V1_PREFIX = "/api/v1/"


def _v1_path(path: str) -> str | None:
    """`/api/X` → `/api/v1/X`；已经是 v1 / 非 /api 的返回 None。"""
    if not path.startswith(_API_PREFIX) or path.startswith(_V1_PREFIX):
        return None
    return _V1_PREFIX + path[len(_API_PREFIX):]


def install_v1_aliases(app: FastAPI) -> int:
    """遍历 app.routes 把所有 `/api/X` 注册一份 `/api/v1/X` 别名。

    必须在所有 router include 完成后调用。返回新增 route 数量。
    """
    existing_paths = {getattr(r, "path", "") for r in app.routes}
    aliases: list[APIRoute] = []
    for route in list(app.routes):
        if not isinstance(route, APIRoute):
            continue
        v1_path = _v1_path(route.path)
        if not v1_path or v1_path in existing_paths:
            continue
        aliases.append(route)

    added = 0
    for route in aliases:
        v1_path = _v1_path(route.path)
        if not v1_path:
            continue
        try:
            # FastAPI APIRoute 已经把 endpoint / dependencies / response_model
            # 等元数据存在 route 上；add_api_route 会重新建一个 APIRoute 带这些
            # 属性的 v1 版。tags 加 "v1" 让 OpenAPI 自动分组。
            tags = list(route.tags or []) + ["v1"]
            # 注：FastAPI.add_api_route 不接受 callbacks kwarg（那是 APIRouter 的）；
            # 该字段在大多数 endpoint 上为空，跳过即可。
            app.add_api_route(
                path=v1_path,
                endpoint=route.endpoint,
                response_model=route.response_model,
                status_code=route.status_code,
                tags=tags,
                dependencies=route.dependencies,
                summary=route.summary,
                description=route.description,
                response_description=route.response_description,
                responses=route.responses,
                deprecated=route.deprecated,
                methods=list(route.methods or []),
                operation_id=(route.operation_id or route.name or "") + "_v1" if (route.operation_id or route.name) else None,
                response_model_include=route.response_model_include,
                response_model_exclude=route.response_model_exclude,
                response_model_by_alias=route.response_model_by_alias,
                response_model_exclude_unset=route.response_model_exclude_unset,
                response_model_exclude_defaults=route.response_model_exclude_defaults,
                response_model_exclude_none=route.response_model_exclude_none,
                include_in_schema=route.include_in_schema,
                response_class=route.response_class,
                name=(route.name + "_v1") if route.name else None,
            )
            added += 1
        except Exception:
            logger.exception("install_v1_aliases: failed to clone route %s", route.path)
    logger.info("API v1 aliases installed: %d routes", added)
    return added


__all__ = ["install_v1_aliases"]
