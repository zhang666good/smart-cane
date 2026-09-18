"""SQLite 数据访问层(DAO).

提供用户画像 / 会话记忆 / 传感快照 / 告警记录的读写，
供 FastAPI 网关与 MCP 工具共用。
"""
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterable

from .config import get_settings

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def _path() -> str:
    return str(get_settings().data_dir / "cane.db")


def get_conn() -> sqlite3.Connection:
    """进程级单例连接(SQLite 线程安全由外部锁保证)."""
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(_path(), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
    return _conn


@contextmanager
def _cursor():
    with _lock:
        conn = get_conn()
        cur = conn.cursor()
        try:
            yield cur
            conn.commit()
        finally:
            cur.close()


def init_db() -> None:
    with _cursor() as cur:
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                device_id   TEXT PRIMARY KEY,
                nickname    TEXT DEFAULT '老伙伴',
                profile     TEXT DEFAULT '{}',   -- JSON 用户画像(病史/偏好/称谓)
                created_at  REAL DEFAULT (strftime('%s','now'))
            );
            CREATE TABLE IF NOT EXISTS sensor_records (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id   TEXT NOT NULL,
                data        TEXT NOT NULL,        -- JSON 传感(三轴/光敏/温湿/定位)
                ts          REAL DEFAULT (strftime('%s','now'))
            );
            CREATE TABLE IF NOT EXISTS conversations (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id   TEXT NOT NULL,
                role        TEXT NOT NULL,        -- 'user' | 'assistant'
                content     TEXT NOT NULL,
                ts          REAL DEFAULT (strftime('%s','now'))
            );
            CREATE TABLE IF NOT EXISTS alerts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id   TEXT NOT NULL,
                type        TEXT NOT NULL,        -- 'fall' | 'sos' | 'lowlight' | ...
                message     TEXT NOT NULL,
                handled     INTEGER DEFAULT 0,
                ts          REAL DEFAULT (strftime('%s','now'))
            );
            CREATE INDEX IF NOT EXISTS idx_sensor_device_ts
                ON sensor_records(device_id, ts);
            
            CREATE TABLE IF NOT EXISTS accounts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT NOT NULL UNIQUE,   -- 老人=设备id / 家人=/^family:/ / 管理员=/^admin:/
                role        TEXT NOT NULL,          -- elder | family | admin
                name        TEXT DEFAULT '',
                password    TEXT DEFAULT '',        -- demo 明文(正式用 hash)
                device_id   TEXT,                   -- 绑定设备(elder 必填, 家人可空, admin 空)
                active      INTEGER DEFAULT 1,
                created_at  REAL DEFAULT (strftime('%s','now'))
            );
            CREATE TABLE IF NOT EXISTS device_bindings (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT NOT NULL,          -- family 账号 user_id
                device_id   TEXT NOT NULL,          -- 被观测设备
                relation    TEXT DEFAULT 'family',  -- family | guardian
                created_at  REAL DEFAULT (strftime('%s','now'))
            );
            CREATE INDEX IF NOT EXISTS idx_bind_user ON device_bindings(user_id);
            CREATE INDEX IF NOT EXISTS idx_bind_device ON device_bindings(device_id);
            CREATE TABLE IF NOT EXISTS conversation_mode (
                device_id   TEXT PRIMARY KEY,
                curr_mode   TEXT NOT NULL DEFAULT 'ai',  -- ai | family | admin
                queue       TEXT DEFAULT '[]',          -- JSON 插播队列: [{"type","sender","audio_url","text"}...]
                updated_by  TEXT NOT NULL,             -- 最后修改者 user_id
                updated_at  REAL DEFAULT (strftime('%s','now'))
            );
            CREATE TABLE IF NOT EXISTS voice_messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id   TEXT NOT NULL,  -- 目标老人设备
                sender_id   TEXT NOT NULL,  -- 发送者 user_id
                sender_role TEXT NOT NULL,  -- family | admin
                audio_path  TEXT NOT NULL,  -- 存储路径
                audio_url   TEXT NOT NULL,  -- 可访问 URL
                direction  TEXT NOT NULL,  -- to-device | to-sender
                is_reply   INTEGER DEFAULT 0,  -- 是否老人回复
                read       INTEGER DEFAULT 0,  -- 是否已读
                ts          REAL DEFAULT (strftime('%s','now'))
            );
            CREATE INDEX IF NOT EXISTS vm_idx_device_ts ON voice_messages(device_id, ts);
            CREATE INDEX IF NOT EXISTS vm_idx_sender ON voice_messages(sender_id, ts);
            CREATE TABLE IF NOT EXISTS operation_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                operator    TEXT NOT NULL,          -- 管理员 user_id
                action      TEXT NOT NULL,          -- 操作类型(如 device_control)
                target      TEXT,                   -- 目标(如 device_id)
                detail      TEXT DEFAULT '{}',      -- JSON 细节
                ts          REAL DEFAULT (strftime('%s','now'))
            );
            CREATE TABLE IF NOT EXISTS events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id   TEXT NOT NULL,
                type        TEXT NOT NULL,        -- obstacle | water | position | ...
                message     TEXT NOT NULL,
                ts          REAL DEFAULT (strftime('%s','now'))
            );
            CREATE INDEX IF NOT EXISTS idx_events_device_ts ON events(device_id, ts);
            """
        )
        # 增量迁移: alerts 表补充 remark 列(复用同一 cursor, 避免重入 _lock 死锁)
        try:
            cur.execute("ALTER TABLE alerts ADD COLUMN remark TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass  # 列已存在


# ---------------- users ----------------
def upsert_user(device_id: str, nickname: str | None = None,
                profile: dict | None = None) -> dict:
    with _cursor() as cur:
        cur.execute("SELECT * FROM users WHERE device_id=?", (device_id,))
        row = cur.fetchone()
        if row is None:
            cur.execute(
                "INSERT INTO users(device_id, nickname, profile) VALUES(?,?,?)",
                (device_id, nickname or "老伙伴",
                 json.dumps(profile or {}, ensure_ascii=False)),
            )
            return {"device_id": device_id, "nickname": nickname or "老伙伴",
                    "profile": profile or {}}
        # 更新非空字段
        new_nick = nickname if nickname is not None else row["nickname"]
        cur.execute(
            "UPDATE users SET nickname=? WHERE device_id=?",
            (new_nick, device_id),
        )
        if profile is not None:
            merged = {**json.loads(row["profile"]), **profile}
            cur.execute(
                "UPDATE users SET profile=? WHERE device_id=?",
                (json.dumps(merged, ensure_ascii=False), device_id),
            )
            row = {**row, "profile": merged, "nickname": new_nick}
        else:
            row = {**row, "nickname": new_nick}
    return dict(row) | {"profile": json.loads(row["profile"]) if isinstance(
        row["profile"], str) else row["profile"]}


def get_user(device_id: str) -> dict | None:
    with _cursor() as cur:
        cur.execute("SELECT * FROM users WHERE device_id=?", (device_id,))
        row = cur.fetchone()
        if row is None:
            return None
        d = dict(row)
        d["profile"] = json.loads(d["profile"])
        return d


# ---------------- sensor_records ----------------
def add_sensor_record(device_id: str, data: dict) -> int:
    with _cursor() as cur:
        cur.execute(
            "INSERT INTO sensor_records(device_id, data) VALUES(?,?)",
            (device_id, json.dumps(data, ensure_ascii=False)),
        )
        return cur.lastrowid


def latest_sensors(device_id: str) -> dict | None:
    with _cursor() as cur:
        cur.execute(
            "SELECT data, ts FROM sensor_records WHERE device_id=? ORDER BY ts DESC LIMIT 1",
            (device_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return {"data": json.loads(row["data"]), "ts": row["ts"]}


def sensor_history(device_id: str, limit: int = 50,
                   start_ts: float | None = None,
                   end_ts: float | None = None) -> list[dict]:
    """按时间范围(可选)查询传感历史, 时间升序返回(便于画趋势/轨迹)."""
    sql = "SELECT data, ts FROM sensor_records WHERE device_id=?"
    args: list = [device_id]
    if start_ts is not None:
        sql += " AND ts>=?"
        args.append(start_ts)
    if end_ts is not None:
        sql += " AND ts<=?"
        args.append(end_ts)
    sql += " ORDER BY ts ASC LIMIT ?"
    args.append(limit)
    with _cursor() as cur:
        cur.execute(sql, args)
        return [{"data": json.loads(r["data"]), "ts": r["ts"]} for r in cur.fetchall()]


# ---------------- conversations ----------------
def add_message(device_id: str, role: str, content: str) -> int:
    with _cursor() as cur:
        cur.execute(
            "INSERT INTO conversations(device_id, role, content) VALUES(?,?,?)",
            (device_id, role, content),
        )
        return cur.lastrowid


def recent_messages(device_id: str, limit: int = 20) -> list[dict]:
    with _cursor() as cur:
        cur.execute(
            "SELECT role, content, ts FROM conversations WHERE device_id=? "
            "ORDER BY ts DESC LIMIT ?",
            (device_id, limit),
        )
        return [dict(r) for r in cur.fetchall()][::-1]


# ---------------- alerts ----------------
def add_alert(device_id: str, type_: str, message: str) -> int:
    with _cursor() as cur:
        cur.execute(
            "INSERT INTO alerts(device_id, type, message) VALUES(?,?,?)",
            (device_id, type_, message),
        )
        return cur.lastrowid


def list_alerts(device_id: str | None = None, limit: int = 50) -> list[dict]:
    with _cursor() as cur:
        if device_id:
            cur.execute(
                "SELECT * FROM alerts WHERE device_id=? ORDER BY ts DESC LIMIT ?",
                (device_id, limit),
            )
        else:
            cur.execute("SELECT * FROM alerts ORDER BY ts DESC LIMIT ?", (limit,))
        return [dict(r) for r in cur.fetchall()]


def mark_alert_handled(alert_id: int, remark: str = "") -> dict | None:
    """标记告警已处理(可附备注); 返回更新后的告警, 不存在返回 None."""
    with _cursor() as cur:
        cur.execute(
            "UPDATE alerts SET handled=1, remark=? WHERE id=?", (remark, alert_id))
        if cur.rowcount == 0:
            return None
        cur.execute("SELECT * FROM alerts WHERE id=?", (alert_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def get_alert(alert_id: int) -> dict | None:
    with _cursor() as cur:
        cur.execute("SELECT * FROM alerts WHERE id=?", (alert_id,))
        row = cur.fetchone()
        return dict(row) if row else None


# ---------------- events(障碍物/积水/位置等业务事件) ----------------
def add_event(device_id: str, type_: str, message: str) -> int:
    with _cursor() as cur:
        cur.execute(
            "INSERT INTO events(device_id, type, message) VALUES(?,?,?)",
            (device_id, type_, message),
        )
        return cur.lastrowid


def list_events(device_id: str, limit: int = 50,
                start_ts: float | None = None) -> list[dict]:
    sql = "SELECT * FROM events WHERE device_id=?"
    args: list = [device_id]
    if start_ts is not None:
        sql += " AND ts>=?"
        args.append(start_ts)
    sql += " ORDER BY ts DESC LIMIT ?"
    args.append(limit)
    with _cursor() as cur:
        cur.execute(sql, args)
        return [dict(r) for r in cur.fetchall()]

# ---------------- accounts / bindings / operation_log ----------------
def upsert_account(user_id: str, role: str, name: str = "",
                   password: str = "", device_id: str | None = None) -> dict:
    with _cursor() as cur:
        cur.execute("SELECT * FROM accounts WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        if row is None:
            cur.execute(
                "INSERT INTO accounts(user_id, role, name, password, device_id) "
                "VALUES(?,?,?,?,?)",
                (user_id, role, name, password, device_id),
            )
        else:
            cur.execute(
                "UPDATE accounts SET role=?, name=?, device_id=? WHERE user_id=?",
                (role, name, device_id, user_id),
            )
        cur.execute("SELECT * FROM accounts WHERE user_id=?", (user_id,))
        return dict(cur.fetchone())


def get_account(user_id: str) -> dict | None:
    with _cursor() as cur:
        cur.execute("SELECT * FROM accounts WHERE user_id=? AND active=1", (user_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def get_account_by_name(name: str) -> dict | None:
    with _cursor() as cur:
        cur.execute("SELECT * FROM accounts WHERE name=? AND active=1", (name,))
        row = cur.fetchone()
        return dict(row) if row else None


def bind_device(user_id: str, device_id: str, relation: str = "family") -> int:
    with _cursor() as cur:
        cur.execute("SELECT id FROM device_bindings WHERE user_id=? AND device_id=?",
                    (user_id, device_id))
        if cur.fetchone():
            return 0
        cur.execute(
            "INSERT INTO device_bindings(user_id, device_id, relation) VALUES(?,?,?)",
            (user_id, device_id, relation),
        )
        return cur.lastrowid


def bound_devices(user_id: str) -> list[str]:
    with _cursor() as cur:
        cur.execute("SELECT device_id FROM device_bindings WHERE user_id=?",
                    (user_id,))
        return [r["device_id"] for r in cur.fetchall()]


def is_bound(user_id: str, device_id: str) -> bool:
    with _cursor() as cur:
        cur.execute("SELECT 1 FROM device_bindings WHERE user_id=? AND device_id=?",
                    (user_id, device_id))
        return cur.fetchone() is not None


def log_operation(operator: str, action: str, target: str | None = None,
                  detail: dict | None = None) -> int:
    import json as _json
    with _cursor() as cur:
        cur.execute(
            "INSERT INTO operation_log(operator, action, target, detail) VALUES(?,?,?,?)",
            (operator, action, target,
             _json.dumps(detail or {}, ensure_ascii=False)),
        )
        return cur.lastrowid


def list_operations(limit: int = 50) -> list[dict]:
    with _cursor() as cur:
        cur.execute("SELECT * FROM operation_log ORDER BY ts DESC LIMIT ?", (limit,))
        return [dict(r) for r in cur.fetchall()]


# ---------------- conversation_mode (当前对话模式 + 插播队列) ----------------
def get_conversation_mode(device_id: str) -> dict | None:
    with _cursor() as cur:
        cur.execute("SELECT * FROM conversation_mode WHERE device_id=?", (device_id,))
        row = cur.fetchone()
        if row is None:
            # 默认模式: ai, 更新者系统
            return {"device_id": device_id, "curr_mode": "ai", "queue": [],
                    "updated_by": "system", "updated_at": None}
        d = dict(row)
        d["queue"] = json.loads(d["queue"]) if isinstance(d["queue"], str) else d["queue"]
        return d


def set_conversation_mode(device_id: str, new_mode: str, updated_by: str,
                           append_queue: dict | None = None) -> dict:
    """设置当前模式; 如果 append_queue 非空,追加到队列尾部。"""
    import json
    curr = get_conversation_mode(device_id)
    q = (curr["queue"] or [])[:]
    if append_queue is not None:
        q.append(append_queue)
    with _cursor() as cur:
        cur.execute("""
            REPLACE INTO conversation_mode(device_id, curr_mode, queue, updated_by)
            VALUES(?,?,?,?)
        """, (device_id, new_mode, json.dumps(q, ensure_ascii=False), updated_by))
    return get_conversation_mode(device_id)


def pop_next_queue(device_id: str) -> dict | None:
    """弹出队列头部项; 返回 None 则队列空。"""
    import json
    curr = get_conversation_mode(device_id)
    if not curr["queue"]:
        return None
    q = curr["queue"][:]
    head = q.pop(0)
    with _cursor() as cur:
        cur.execute("UPDATE conversation_mode SET queue=? WHERE device_id=?",
                    (json.dumps(q), device_id))
    return head


def clear_queue(device_id: str) -> None:
    with _cursor() as cur:
        cur.execute("UPDATE conversation_mode SET queue='[]' WHERE device_id=?", (device_id,))


# ---------------- voice_messages (异步语音留言) ----------------
def add_voice_message(device_id: str, sender_id: str, sender_role: str,
                      audio_path: str, audio_url: str, direction: str,
                      is_reply: bool = False) -> int:
    with _cursor() as cur:
        cur.execute("""
            INSERT INTO voice_messages(device_id, sender_id, sender_role,
                audio_path, audio_url, direction, is_reply)
            VALUES(?,?,?,?,?,?,?)
        """, (device_id, sender_id, sender_role, audio_path, audio_url, direction, 1 if is_reply else 0))
        return cur.lastrowid


def list_voice_messages(device_id: str, limit: int = 50,
                       unread_only: bool = False) -> list[dict]:
    with _cursor() as cur:
        w = ""
        args = [device_id]
        if unread_only:
            w += " AND read=0"
        cur.execute("""SELECT * FROM voice_messages WHERE device_id=? {}
                    ORDER BY ts DESC LIMIT ?""".format(w), (*args, limit))
        return [dict(r) for r in cur.fetchall()]


def mark_voice_read(msg_id: int) -> None:
    with _cursor() as cur:
        cur.execute("UPDATE voice_messages SET read=1 WHERE id=?", (msg_id,))