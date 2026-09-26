"""路由契约回归：创建 / 修改接口必须把入参识别为「请求体」而不是 query 参数。

背景：这六类路由由注册表动态生成。一旦入参注解写成运行时变量（`payload: spec.schema_in`），
FastAPI 在注册时无法解析成 Pydantic 模型，会退化成 query 参数，
表现就是创建/修改永远返回 422（missing query payload）—— 界面上等于"保存不了"。
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.routing import APIRoute

from app.api.v1.resource_router import SPEC_BY_SLUG, build_resource_router
from app.main import app

SLUGS = sorted(SPEC_BY_SLUG)


def _route(slug: str, method: str) -> APIRoute:
    router = build_resource_router(SPEC_BY_SLUG[slug])
    for route in router.routes:
        if isinstance(route, APIRoute) and method in route.methods:
            return route
    raise AssertionError(f"{slug} 缺少 {method} 路由")


@pytest.mark.parametrize("slug", SLUGS)
def test_post_expects_body(slug: str) -> None:
    route = _route(slug, "POST")
    assert route.body_field is not None, f"{slug} 的 POST 没有请求体：入参注解没被识别"
    assert route.body_field.name == "payload"


@pytest.mark.parametrize("slug", SLUGS)
def test_put_expects_body(slug: str) -> None:
    route = _route(slug, "PUT")
    assert route.body_field is not None, f"{slug} 的 PUT 没有请求体：入参注解没被识别"
    assert route.body_field.name == "payload"


@pytest.mark.parametrize("slug", SLUGS)
def test_list_route_has_no_body(slug: str) -> None:
    route = _route(slug, "GET")
    assert route.body_field is None, f"{slug} 的 GET 不该带请求体"


@pytest.mark.parametrize("slug", SLUGS)
def test_delete_route_registered(slug: str) -> None:
    # 删除接口会返回 {deleted, unbound}（支持"解绑并删除"），所以是 200
    assert _route(slug, "DELETE").status_code in (200, 204)


async def test_impls_endpoint_returns_registered_impls() -> None:
    """`/tools/impls` 不能被 `/tools/{item_id}` 抢走。

    抢走的症状：通用详情路由把 "impls" 当成 item_id（int）解析 → 422，
    前端工具页的「实现」下拉一直是空的 —— 看起来像"页面没接数据库"。
    """
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/tools/impls")
    assert response.status_code != 422, f"静态子路径被动态路由吞掉了：{response.text}"
    assert response.status_code == 200, response.text
    names = {item["impl_key"] for item in response.json()["impls"]}
    assert {"kb_search", "http_request", "pdf_extract"} <= names
