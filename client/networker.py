"""网络管理: 按固件/已验证 usr/network.py 方式入网 + net.getCellInfo() 查询状态.

- 入网: quectel.Network.init() -> query_usim() -> attach()  (与 usr/network.py 一致)
- 状态: net.getCellInfo() 返回 -1 表示未入网, 否则已注册 (与 usr/address_lbs.py 一致)
不再用手动轮询替代固件注册, 只做入网触发与状态查询.
"""
import net
from quectel import Network

from usr.libs.config import get as cfg_get


def register():
    """触发固件入网注册(与 usr/network.py 已验证流程一致)."""
    try:
        n = Network()
        n.init()
        n.query_usim()          # 等待 SIM 用途查询(阻塞到 SIM 就绪)
        n.attach()              # 附着入网
        n.deinit()
        print("[NET] register() done")
    except Exception as e:
        print("[NET] register except:", e)


def is_network_ok():
    """是否已注册: net.getCellInfo() 返回 -1 表示未入网."""
    try:
        return net.getCellInfo() != -1
    except Exception as e:
        print("[NET] is_network_ok error:", e)
        return False


def ensure_online(timeout_s=None):
    """触发入网并等待注册成功, 返回是否在线."""
    timeout_s = timeout_s or cfg_get("network_wait_s", 60)
    # 若已在线直接返回; 否则触发注册后等待
    if not is_network_ok():
        register()
    import utime as time
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if is_network_ok():
            print("[NET] registered")
            return True
        print("[NET] waiting registration")
        time.sleep(1)
    return is_network_ok()