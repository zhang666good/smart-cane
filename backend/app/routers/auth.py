"""角色账号登录与 JWT 签发。

demo 环境内建三类账号(也可从 DB accounts 表读取):
  - 老人设备:  device/dev-001
  - 家人:      family/demo-family
  - 管理员:    admin/demo-admin

返回 token,payload 含 role/scope/device_id。
"""
from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..config import get_settings
from ..security import create_token

router = APIRouter(prefix="/api/auth", tags=["auth"])

DEMO_ACCOUNTS = {
    # user_id -> (role, password, device_id, name)
    "dev-001":     ("elder",  "123456",              "dev-001", "老王"),
    "family-demo": ("family", "family123",           "dev-001", "张女士"),
    "admin-demo":  ("admin",  "admin123",            None,      "运营管理员"),
}


class LoginBody(BaseModel):
    user_id: str
    password: str
    device_id: str | None = Field(default=None, description="可选,覆盖绑定设备")


@router.post("/token")
def issue_token(body: LoginBody):
    """登录并签发角色化 JWT。demo: 校验通过即签发,真实环境可接账户体系。"""
    cfg = get_settings()
    # 1) 设备端: 校验 x-device-token 等价物(仍支持旧设备头) + 内置设备账号
    found = DEMO_ACCOUNTS.get(body.user_id)
    if found:
        role, pwd, dev, name = found
        if body.password != pwd:
            raise HTTPException(status_code=401, detail="bad password")
        device_id = body.device_id or dev
    else:
        # 从 DB accounts 表查找
        acct = db.get_account(body.user_id)
        if acct is None or acct.get("password") != body.password:
            raise HTTPException(status_code=401, detail="bad account or password")
        role, device_id = acct["role"], acct.get("device_id") or body.device_id
        name = acct.get("name", "")
    token = create_token(user_id=body.user_id, role=role, device_id=device_id)
    return {
        "token": token,
        "token_type": "bearer",
        "role": role,
        "user_id": body.user_id,
        "name": name,
        "device_id": device_id,
        "expires_in": 3600 * 12,
    }