"""v1 路由聚合。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import sessions_router, system_router, test_router
from app.api.v1.resource_router import build_all_resource_routers

api_router = APIRouter(prefix="/api/v1")

# 顺序有讲究：测试场里带静态子路径的端点（如 `/tools/impls`）必须先注册。
# 否则通用资源路由 `/{item_id}` 会先匹配上，"impls" 被当成 int 解析 → 422。
api_router.include_router(test_router.router)

for resource_router in build_all_resource_routers():
    api_router.include_router(resource_router)

api_router.include_router(sessions_router.router)
api_router.include_router(system_router.router)
