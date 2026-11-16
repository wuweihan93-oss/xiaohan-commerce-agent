"""SQLite 保存独立的演示业务数据；所有订单、会话和售后操作强制检查归属。"""

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from app.config import DATABASE


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect():
    connection = sqlite3.connect(DATABASE, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize():
    with connect() as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS products (id TEXT PRIMARY KEY, name TEXT, price INTEGER, stock INTEGER, category TEXT, description TEXT, specs TEXT);
            CREATE TABLE IF NOT EXISTS orders (id TEXT PRIMARY KEY, customer_id TEXT, product_id TEXT REFERENCES products(id), status TEXT, amount INTEGER, created_at TEXT, delivered_at TEXT, logistics TEXT);
            CREATE TABLE IF NOT EXISTS visitors (id TEXT PRIMARY KEY, customer_id TEXT NOT NULL DEFAULT 'demo-a');
            CREATE TABLE IF NOT EXISTS conversations (id TEXT PRIMARY KEY, visitor_id TEXT, customer_id TEXT, title TEXT, updated_at TEXT);
            CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, conversation_id TEXT REFERENCES conversations(id), role TEXT, content TEXT, sources TEXT DEFAULT '[]', trace TEXT DEFAULT '[]', created_at TEXT);
            CREATE TABLE IF NOT EXISTS actions (id TEXT PRIMARY KEY, visitor_id TEXT, customer_id TEXT, conversation_id TEXT, order_id TEXT, reason TEXT, status TEXT, created_at TEXT, ticket_id TEXT);
            CREATE UNIQUE INDEX IF NOT EXISTS action_order_active ON actions(visitor_id, customer_id, order_id) WHERE status IN ('pending', 'submitted');
            CREATE INDEX IF NOT EXISTS conversation_owner ON conversations(visitor_id, customer_id, updated_at);
            CREATE INDEX IF NOT EXISTS message_conversation ON messages(conversation_id, created_at);
        """)
        products = [
            (
                "P1001",
                "拾光降噪耳机",
                39900,
                28,
                "数码音频",
                "主动降噪 / 约 30 小时续航 / 云白与岩灰",
                {"蓝牙": "5.3", "颜色": ["云白", "岩灰"], "保修": "12 个月"},
            ),
            (
                "P1002",
                "轻量保温杯",
                8900,
                63,
                "生活好物",
                "500mL / 316L 不锈钢 / 奶白与浅咖",
                {"容量": "500mL", "材质": "316L 不锈钢", "颜色": ["奶白", "浅咖"]},
            ),
            (
                "P1003",
                "折叠阅读灯",
                15900,
                16,
                "桌面好物",
                "三档色温 / 无级调光 / USB-C 接电",
                {"供电": "USB-C，无内置电池", "颜色": ["雾白"], "保修": "12 个月"},
            ),
        ]
        for item in products:
            db.execute(
                "INSERT OR IGNORE INTO products VALUES (?,?,?,?,?,?,?)",
                (*item[:6], json.dumps(item[6], ensure_ascii=False)),
            )
        current = datetime.now(timezone.utc)
        orders = [
            (
                "SG20260001",
                "demo-a",
                "P1001",
                "已签收",
                39900,
                (current - timedelta(days=3)).isoformat(),
                (current - timedelta(days=1)).isoformat(),
                ["商家已发货", "快件到达杭州分拨中心", "快件已签收"],
            ),
            (
                "SG20260002",
                "demo-a",
                "P1002",
                "运输中",
                8900,
                (current - timedelta(days=1)).isoformat(),
                None,
                ["商家已发货", "快件到达上海转运中心", "正在发往杭州"],
            ),
            (
                "SG20260003",
                "demo-b",
                "P1003",
                "待发货",
                15900,
                current.isoformat(),
                None,
                ["订单已支付", "仓库备货中"],
            ),
        ]
        for item in orders:
            db.execute(
                "INSERT OR IGNORE INTO orders VALUES (?,?,?,?,?,?,?,?)",
                (*item[:7], json.dumps(item[7], ensure_ascii=False)),
            )


def visitor(visitor_id):
    with connect() as db:
        db.execute("INSERT OR IGNORE INTO visitors(id) VALUES (?)", (visitor_id,))
        return dict(
            db.execute("SELECT * FROM visitors WHERE id=?", (visitor_id,)).fetchone()
        )


def switch_customer(visitor_id, customer_id):
    if customer_id not in {"demo-a", "demo-b"}:
        raise ValueError("只能切换到预设演示身份")
    with connect() as db:
        db.execute(
            "UPDATE visitors SET customer_id=? WHERE id=?", (customer_id, visitor_id)
        )


def products(query=""):
    with connect() as db:
        items = [dict(row) for row in db.execute("SELECT * FROM products ORDER BY id")]
    for item in items:
        item["specs"] = json.loads(item["specs"])
    if query:
        matched = [
            item
            for item in items
            if query.lower() in json.dumps(item, ensure_ascii=False).lower()
            or any(
                word in query for word in (item["name"], item["id"], item["name"][-2:])
            )
        ]
        return matched or items
    return items


def orders(customer_id, order_id=None):
    with connect() as db:
        sql = "SELECT o.*, p.name AS product_name FROM orders o JOIN products p ON p.id=o.product_id WHERE customer_id=?"
        rows = db.execute(
            sql + (" AND o.id=?" if order_id else "") + " ORDER BY o.id",
            (customer_id, order_id) if order_id else (customer_id,),
        )
        items = [dict(row) for row in rows]
    for item in items:
        item["logistics"] = json.loads(item["logistics"])
    return items


def conversation(visitor_id, customer_id, conversation_id):
    with connect() as db:
        row = db.execute(
            "SELECT * FROM conversations WHERE id=? AND visitor_id=? AND customer_id=?",
            (conversation_id, visitor_id, customer_id),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["messages"] = [
            dict(item)
            for item in db.execute(
                "SELECT * FROM messages WHERE conversation_id=? ORDER BY rowid",
                (conversation_id,),
            )
        ]
        for item in result["messages"]:
            item["sources"], item["trace"] = (
                json.loads(item["sources"]),
                json.loads(item["trace"]),
            )
        return result


def conversation_list(visitor_id, customer_id):
    with connect() as db:
        return [
            dict(row)
            for row in db.execute(
                "SELECT id,title,updated_at FROM conversations WHERE visitor_id=? AND customer_id=? ORDER BY updated_at DESC LIMIT 50",
                (visitor_id, customer_id),
            )
        ]


def begin_turn(visitor_id, customer_id, conversation_id, question):
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT visitor_id,customer_id FROM conversations WHERE id=?",
            (conversation_id,),
        ).fetchone()
        if row and (row["visitor_id"], row["customer_id"]) != (visitor_id, customer_id):
            raise PermissionError("找不到这段客服会话")
        db.execute(
            "INSERT OR IGNORE INTO conversations VALUES (?,?,?,?,?)",
            (conversation_id, visitor_id, customer_id, question[:36], now()),
        )
        db.execute(
            "INSERT INTO messages(id,conversation_id,role,content,created_at) VALUES (?,?,?,?,?)",
            (uuid.uuid4().hex, conversation_id, "user", question, now()),
        )
        db.execute(
            "UPDATE conversations SET updated_at=? WHERE id=?", (now(), conversation_id)
        )


def finish_turn(conversation_id, answer, sources, trace):
    with connect() as db:
        db.execute(
            "INSERT INTO messages VALUES (?,?,?,?,?,?,?)",
            (
                uuid.uuid4().hex,
                conversation_id,
                "assistant",
                answer,
                json.dumps(sources, ensure_ascii=False),
                json.dumps(trace, ensure_ascii=False),
                now(),
            ),
        )
        db.execute(
            "UPDATE conversations SET updated_at=? WHERE id=?", (now(), conversation_id)
        )


def eligibility(order):
    if order["status"] == "待发货":
        return "取消订单"
    if (
        order["status"] == "已签收"
        and order["delivered_at"]
        and datetime.now(timezone.utc)
        <= datetime.fromisoformat(order["delivered_at"]) + timedelta(days=7)
    ):
        return "退货退款"
    raise ValueError(
        "当前订单不符合自动申请条件，请联系人工客服；运输中或签收超过 7 天不能自动提交"
    )


def prepare_action(visitor_id, customer_id, conversation_id, order_id, reason):
    items = orders(customer_id, order_id)
    if not items:
        raise ValueError("找不到该订单，或订单不属于当前演示用户")
    kind = eligibility(items[0])
    if not reason.strip() or len(reason) > 500:
        raise ValueError("请提供 1 至 500 个字符的售后原因")
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        # 同一浏览器和订单重复调用工具不会重复创建申请。
        existing = db.execute(
            "SELECT * FROM actions WHERE visitor_id=? AND customer_id=? AND order_id=? AND status IN ('pending','submitted')",
            (visitor_id, customer_id, order_id),
        ).fetchone()
        if existing:
            return {
                **dict(existing),
                "kind": kind,
                "notice": "售后草稿需要用户在页面点击确认；提交后仍待人工审核，不代表退款。",
            }
        action_id = uuid.uuid4().hex
        db.execute(
            "INSERT INTO actions VALUES (?,?,?,?,?,?,?,?,?)",
            (
                action_id,
                visitor_id,
                customer_id,
                conversation_id,
                order_id,
                reason.strip(),
                "pending",
                now(),
                None,
            ),
        )
        return {
            "id": action_id,
            "order_id": order_id,
            "reason": reason.strip(),
            "status": "pending",
            "kind": kind,
            "notice": "这是售后草稿，尚未提交，请用户在页面点击确认。",
        }


def actions(visitor_id, customer_id):
    with connect() as db:
        return [
            dict(row)
            for row in db.execute(
                "SELECT id,conversation_id,order_id,reason,status,created_at,ticket_id FROM actions WHERE visitor_id=? AND customer_id=? ORDER BY created_at DESC LIMIT 30",
                (visitor_id, customer_id),
            )
        ]


def confirm_action(visitor_id, customer_id, action_id, confirm):
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT * FROM actions WHERE id=? AND visitor_id=? AND customer_id=?",
            (action_id, visitor_id, customer_id),
        ).fetchone()
        if row is None:
            raise PermissionError("找不到这项售后申请")
        result = dict(row)
        if result["status"] != "pending":
            return result
        if confirm:
            order = db.execute(
                "SELECT * FROM orders WHERE id=? AND customer_id=?",
                (row["order_id"], customer_id),
            ).fetchone()
            if not order:
                raise ValueError("订单不存在或不属于当前用户")
            eligibility(dict(order))
        status = "submitted" if confirm else "cancelled"
        ticket_id = "AS" + uuid.uuid4().hex[:12].upper() if confirm else None
        db.execute(
            "UPDATE actions SET status=?,ticket_id=? WHERE id=?",
            (status, ticket_id, action_id),
        )
        return {**result, "status": status, "ticket_id": ticket_id}
