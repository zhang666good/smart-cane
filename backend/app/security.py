"""角色化 JWT 安全模块。

三角色: elder(老人/设备端) | family(家人/App) | admin(后台管理员)。
签发含 sub / role / scope / device_id 的 JWT;提供 FastAPI 依赖 require_role
用于 REST 路由,以及工具级 scope 校验 require_scope。

角色 scope 常量与架构文档 6.2(e) 权限矩阵对应。
"""
from __future__ import annotations

import time
from typing import Any, Iterable

import jwt
from fastapi import Depends, HTTPException, Header
from jwt import InvalidTokenError

# ---------------------------------------------------------------- 常量
SECRET_KEY = "cane-demo-secret-change-me"   # TODO: 移入 .env
ALGORITHM = "HS256"
DEFAULT_TTL = 3600 * 12                     # 12h

ROLE_ELDER = "elder"
ROLE_FAMILY = "family"
ROLE_ADMIN = "admin"
ROLES = (ROLE_ELDER, ROLE_FAMILY, ROLE_ADMIN)

# 全局工具 scope 清单(与架构文档 6.2(e) 对齐)
SCOPE_SENSORS = "sensors"            # 传感读取(绑定设备)
SCOPE_SENSORS_FULL = "sensors_full"  # 传感读取(任意设备)
SCOPE_SOS = "sos"                    # 触发求救
SCOPE_USER_OWN = "user_own"          # 本人/绑定设备画像读写
SCOPE_USER_ALL = "user_all"          # 任意设备画像
SCOPE_RECORD_OWN = "record_own"      # 本人会话
SCOPE_RECORD_MSG = "record_msg"      # 留言/关怀
SCOPE_RECORD_ALL = "record_all"      # 全量会话
SCOPE_ALERT_READ = "alert_read"
SCOPE_ALERT_WRITE = "alert_write"
SCOPE_PUSH_OWN = "push_own"          # 本人/绑定设备下发
SCOPE_PUSH_ALL = "push_all"          # 任意设备下发
SCOPE_DEVICE_CTRL = "device_ctrl"    # 远程运维/运营管控
SCOPE_DEVICE_MGMT = "device_mgmt"    # 设备清单/配置


# 每个角色默认拥有的 scope
ROLE_SCOPES: dict[str, set[str]] = {
    ROLE_ELDER: {SCOPE_SOS, SCOPE_USER_OWN, SCOPE_RECORD_OWN, SCOPE_PUSH_OWN},
    ROLE_FAMILY: {SCOPE_SENSORS, SCOPE_USER_OWN, SCOPE_RECORD_MSG,
                  SCOPE_ALERT_READ, SCOPE_PUSH_OWN},
    ROLE_ADMIN: {SCOPE_SENSORS_FULL, SCOPE_SOS, SCOPE_USER_ALL, SCOPE_RECORD_ALL,
                 SCOPE_ALERT_READ, SCOPE_ALERT_WRITE, SCOPE_PUSH_OWN,
                 SCOPE_PUSH_ALL, SCOPE_DEVICE_CTRL, SCOPE_DEVICE_MGMT},
}


# ---------------------------------------------------------------- 签发
def create_token(user_id: str, role: str, device_id: str | None = None,
                 scopes: Iterable[str] | None = None,
                 ttl: int = DEFAULT_TTL) -> str:
    """签发 JWT。scopes 缺省取该角色的默认 scope 集。"""
    if role not in ROLES:
        raise ValueError(f"unknown role: {role}")
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": user_id,
        "role": role,
        "scopes": list(scopes) if scopes is not None else sorted(ROLE_SCOPES[role]),
        "iat": now,
        "exp": now + ttl,
    }
    if device_id:
        payload["device_id"] = device_id
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    """解码并校验签名/过期;失败抛 HTTPException(401)。"""
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail=f"invalid token: {exc}")


# ---------------------------------------------------------------- FastAPI 解析
def resolve_claims(authorization: str = Header(default="")) -> dict[str, Any]:
    """从 Authorization: Bearer <jwt> 解析 claims;无 token 视为匿名。"""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing Bearer token")
    return decode_token(authorization[7:].strip())


def require_role(*roles: str):
    """返回依赖,要求当前 token 的角色在 *roles 中。

    用法: @router.post(..., dependencies=[Depends(require_role('admin'))])
    """
    if not roles:
        roles = ROLES

    def deps(claims: dict[str, Any] = Depends(resolve_claims)) -> dict[str, Any]:
        if claims.get("role") not in roles:
            raise HTTPException(status_code=403,
                                detail=f"role {claims.get('role')} not allowed")
        return claims

    return deps


# ---------------------------------------------------------------- 工具级 scope
def has_scope(claims: dict[str, Any], scope: str) -> bool:
    return scope in (claims.get("scopes") or [])


def require_scope(claims: dict[str, Any], *scopes: str) -> None:
    """工具/接口内校验 scope,不满足抛 403。"""
    owned = set(claims.get("scopes") or [])
    if not (owned & set(scopes)):
        raise HTTPException(
            status_code=403,
            detail=f"require any of scopes {scopes}, got {sorted(owned)}",
        )


def can_access_device(claims: dict[str, Any], device_id: str,
                      scope_for_owner: str) -> None:
    """校验调用者对 device_id 的访问权:
       - admin 可访问任意设备
       - 否则须 device_id 匹配 claims.device_id 且具备 owner scope
    """
    if claims.get("role") == ROLE_ADMIN:
        return
    if claims.get("device_id") != device_id:
        raise HTTPException(status_code=403, detail="not bound to this device")
    require_scope(claims, scope_for_owner)


def claims_from_dict(d: dict) -> dict[str, Any]:
    """把 claims 转成普通 dict(MCP 上下文用)。"""
    return dict(d)