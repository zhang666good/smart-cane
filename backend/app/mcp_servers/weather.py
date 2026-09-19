"""天气工具: 对接 ModelScope 托管的 XingYuWeather MCP server(streamable_http).

暴露一个同步函数 query_weather(), 支持按城市名(含区县级)或 WGS84 经纬度精准查询,
返回结构化天气(当前 + 未来 7 天)。内部每次调用在独立线程 + 独立事件循环中用
mcp client 连接远程 server 发起工具调用, 因此对外仍是同步可调用,
与现有 Tool.from_function + langchain exec.invoke 的执行方式无缝兼容。
"""
import json
import logging
from concurrent.futures import ThreadPoolExecutor

_log = logging.getLogger("cane.backend.ai.weather")

# ModelScope 托管天气 MCP 的 Streamable HTTP 端点
_WEATHER_MCP_URL = "https://mcp.api-inference.modelscope.net/b45d3447a3884e/mcp"

# 按地名查询: 第三方解析对行政区后缀敏感, "綦江区/重庆市綦江区"会命中失败,
# 需尝试去除 "市/区/县/省/州" 等后缀后用精简名再查
_ADMIN_SUFFIXES = ("市", "省", "区", "县", "州", "盟", "旗", "自治州", "地区", "特别行政区")


def _strip_admin_suffix(area: str) -> list[str]:
    """生成地名候选(原样 + 尾部剥离行政区后缀 + 市/省后缀之后的子串)。"""
    out: list[str] = []

    def push(s: str):
        if s and s not in out:
            out.append(s)

    if not area:
        return out
    area = area.strip()
    push(area)

    def _strip_tail_chain(s: str):
        push(s)
        cur = s
        while True:
            nxt = None
            for suf in _ADMIN_SUFFIXES:
                if cur.endswith(suf) and len(cur) > len(suf):
                    nxt = cur[:-len(suf)]
                    break
            if nxt is None:
                break
            push(nxt)
            cur = nxt

    # 1) 尾部逐级剥离行政区后缀(如 "綦江区" -> "綦江"; "重庆市綦江区" -> "重庆市綦江")
    _strip_tail_chain(area)

    # 2) 取 "市/省/自治州" 等后缀之后的子串再剥离(如 "重庆市綦江区" -> "綦江区" -> "綦江")
    for sep in ("市", "省", "自治州", "地区"):
        i = area.rfind(sep)
        if 0 < i < len(area) - 1:
            _strip_tail_chain(area[i + 1:])

    return out


def _collect_result(r) -> dict:
    """把远程 CallToolResult 归一为结构化 dict。"""
    sc = getattr(r, "structured_content", None)
    if sc is not None:
        if isinstance(sc, dict):
            return sc
        try:
            return json.loads(str(sc))
        except Exception:
            return {"raw": str(sc)}
    # 无 structured_content 时从 text 内容里尝试解析
    texts = []
    for c in getattr(r, "content", []) or []:
        t = getattr(c, "text", None)
        if t:
            texts.append(t)
    joined = "\n".join(texts).strip()
    if not joined:
        return {}
    try:
        return json.loads(joined)
    except Exception:
        return {"raw": joined}


async def _remote_call_async(tool_name: str, args: dict) -> dict:
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with streamable_http_client(_WEATHER_MCP_URL) as (rr, ww):
        async with ClientSession(rr, ww) as sess:
            init = await sess.initialize()
            _log.info("天气MCP 连接: %s %s", init.server_info.name,
                      init.server_info.version)
            res = await sess.call_tool(tool_name, args)
            return _collect_result(res)


def _remote_call(tool_name: str, args: dict, timeout: float = 45.0) -> dict:
    """在线程中跑独立事件循环调远程 MCP(避免与已有 running loop 冲突)。"""
    import asyncio

    async def _guarded():
        return await asyncio.wait_for(_remote_call_async(tool_name, args),
                                      timeout=timeout)

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_guarded())
    finally:
        try:
            loop.close()
        except Exception:
            pass


def query_weather(area: str | None = None,
                  longitude=None, latitude=None) -> dict:
    """查询天气。

    支持两种入参(二选一):
      - 按城市/区县名: area, 如 "重庆" / "綦江"(含行政区后缀也可自动精简);
      - 按 WGS84 经纬度: longitude + latitude, 如 106.6500 / 28.9667。

    返回结构化天气(当前实况 + 未来7天): {code, data:{city, now, dayWeathers}}。
    无法解析或网络异常时返回 ok:false + 可读 error,供 AI 转述给用户。
    """
    if longitude is not None and latitude is not None:
        tool, args = "weather_query_by_lgtlat", {
            "longitude": str(longitude), "latitude": str(latitude)}
    elif area:
        tool, args = "weather_query_by_area", {"area": area}
    else:
        return {"ok": False,
                "error": "缺少查询参数: 请提供城市名 area 或经纬度 longitude/latitude"}

    if tool == "weather_query_by_area":
        # 依次尝试精简地名候选, 命中一次即返回
        for cand in _strip_admin_suffix((area or "").strip()):
            if not cand:
                continue
            try:
                res = _remote_call("weather_query_by_area", {"area": cand})
            except Exception as e:
                _log.warning("天气查询异常 area=%s: %s", cand, e)
                return {"ok": False, "tool": tool, "error": f"天气查询失败: {e}"}
            code = res.get("code")
            if code == 200 and res.get("data"):
                res["ok"] = True
                res["source"] = "XingYuWeather"
                return res
            # 未找到时继续尝试下一候选
        return {"ok": False, "tool": tool,
                "error": f"未找到地区'{(area or '').strip()}', 可尝试经纬度查询",
                "candidates": _strip_admin_suffix((area or "").strip())}

    try:
        res = _remote_call(tool, args)
    except Exception as e:
        _log.warning("天气查询异常(经纬度): %s", e)
        return {"ok": False, "tool": tool, "error": f"天气查询失败: {e}"}
    code = res.get("code")
    if code != 200:
        return {"ok": False, "tool": tool, "error": res.get("msg", "查询失败"), }
    res["ok"] = True
    res["source"] = "XingYuWeather"
    return res


__all__ = ["query_weather"]