"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。

专用延迟恢复路由需在通用资源路由之前注册，否则 /api/{module} 动态路径会
抢先匹配 /api/lag-observations 等固定路径。
"""
from __future__ import annotations

from app.routers import lag_recovery, resource

ROUTERS = [lag_recovery, resource]
