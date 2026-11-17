"""业务权限与售后确认测试，仅使用临时 SQLite 数据。"""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app import store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        patcher = patch.object(
            store, "DATABASE", Path(self.temporary.name) / "business.sqlite3"
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        store.initialize()
        store.visitor("visitor-a")
        store.visitor("visitor-b")

    def test_orders_enforce_customer_scope(self):
        self.assertEqual(len(store.orders("demo-a")), 2)
        self.assertEqual(len(store.orders("demo-b")), 1)
        self.assertEqual(store.orders("demo-b", "SG20260001"), [])
        with self.assertRaises(ValueError):
            store.prepare_action("visitor-b", "demo-b", "chat", "SG20260001", "不合适")

    def test_product_recommendations_apply_budget_category_and_use_case(self):
        results = store.recommend_products(
            "办公桌阅读灯", max_price_yuan=200, category="桌面", limit=3
        )
        self.assertEqual(results[0]["id"], "P1003")
        self.assertTrue(all(item["price"] <= 20000 for item in results))
        self.assertEqual(
            store.recommend_products("耳机", max_price_yuan=100), []
        )
        with store.connect() as db:
            db.execute("UPDATE products SET stock=0 WHERE id='P1003'")
        self.assertNotIn(
            "P1003", [item["id"] for item in store.recommend_products("阅读灯")]
        )

    def test_product_catalog_has_more_categories_and_recommendation_aliases(self):
        self.assertEqual(len(store.products()), 15)
        commute_audio = store.recommend_products("通勤运动耳机", max_price_yuan=300)
        self.assertIn("P1009", [item["id"] for item in commute_audio])
        desk_setup = store.recommend_products("笔记本电脑办公桌面")
        self.assertTrue({"P1004", "P1005", "P1006"}.issubset(
            {item["id"] for item in desk_setup}
        ))

    def test_existing_database_adds_recommendation_history_column(self):
        with store.connect() as db:
            db.execute("ALTER TABLE messages DROP COLUMN recommendations")
        store.initialize()
        with store.connect() as db:
            columns = {row["name"] for row in db.execute("PRAGMA table_info(messages)")}
        self.assertIn("recommendations", columns)

    def test_conversation_cannot_be_claimed_by_another_browser_or_customer(self):
        store.begin_turn("visitor-a", "demo-a", "chat", "你好")
        for visitor, customer in [("visitor-b", "demo-a"), ("visitor-a", "demo-b")]:
            with self.subTest(visitor=visitor, customer=customer):
                self.assertIsNone(store.conversation(visitor, customer, "chat"))
                with self.assertRaises(PermissionError):
                    store.begin_turn(visitor, customer, "chat", "越权")
        self.assertEqual(
            len(store.conversation("visitor-a", "demo-a", "chat")["messages"]), 1
        )

    def test_draft_requires_browser_confirmation_and_confirmation_is_idempotent(self):
        draft = store.prepare_action(
            "visitor-a", "demo-a", "chat", "SG20260001", "不合适"
        )
        repeated = store.prepare_action(
            "visitor-a", "demo-a", "chat", "SG20260001", "重复申请"
        )
        self.assertEqual(draft["id"], repeated["id"])
        self.assertEqual(draft["status"], "pending")
        with self.assertRaises(PermissionError):
            store.confirm_action("visitor-b", "demo-a", draft["id"], True)
        result = store.confirm_action("visitor-a", "demo-a", draft["id"], True)
        again = store.confirm_action("visitor-a", "demo-a", draft["id"], True)
        self.assertEqual(result["status"], "submitted")
        self.assertEqual(result["ticket_id"], again["ticket_id"])
        self.assertEqual(store.orders("demo-a", "SG20260001")[0]["status"], "已签收")

    def test_transit_and_old_orders_cannot_be_auto_submitted(self):
        with self.assertRaisesRegex(ValueError, "当前订单不符合"):
            store.prepare_action("visitor-a", "demo-a", "chat", "SG20260002", "不需要")
        draft = store.prepare_action(
            "visitor-a", "demo-a", "chat", "SG20260001", "不合适"
        )
        with store.connect() as db:
            db.execute(
                "UPDATE orders SET delivered_at=? WHERE id=?",
                (
                    (datetime.now(timezone.utc) - timedelta(days=8)).isoformat(),
                    "SG20260001",
                ),
            )
        with self.assertRaises(ValueError):
            store.confirm_action("visitor-a", "demo-a", draft["id"], True)
        self.assertEqual(store.actions("visitor-a", "demo-a")[0]["status"], "pending")

    def test_cancel_draft_allows_a_new_application_and_seeds_do_not_overwrite(self):
        draft = store.prepare_action(
            "visitor-a", "demo-a", "chat", "SG20260001", "不合适"
        )
        store.confirm_action("visitor-a", "demo-a", draft["id"], False)
        next_draft = store.prepare_action(
            "visitor-a", "demo-a", "chat", "SG20260001", "重新申请"
        )
        self.assertNotEqual(draft["id"], next_draft["id"])
        with store.connect() as db:
            db.execute("UPDATE products SET stock=0, price=12345 WHERE id='P1001'")
        store.initialize()
        product = store.products("P1001")[0]
        self.assertEqual(product["stock"], 0)
        self.assertEqual(product["price"], 12345)
