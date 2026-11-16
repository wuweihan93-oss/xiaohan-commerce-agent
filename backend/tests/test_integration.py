"""完整 HTTP → LangChain → stdio MCP → Milvus/订单 → SSE 测试，无外部模型调用。"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

import httpx

BACKEND = Path(__file__).resolve().parents[1]


class FullStackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            cls.port = listener.getsockname()[1]
        cls.url = f"http://127.0.0.1:{cls.port}"
        cls.env = {
            **os.environ,
            "COMMERCE_DATA_DIR": cls.temporary.name,
            "COMMERCE_INTERNAL_URL": cls.url,
            "DEMO_MODE": "true",
            "EMBEDDING_MODE": "local",
            "SESSION_SECRET": "isolated-test-secret-" + "a" * 40,
            "LANGSMITH_TRACING": "false",
        }
        cls.log = open(Path(cls.temporary.name) / "server.log", "w+")
        cls.start_server()

    @classmethod
    def start_server(cls):
        cls.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(cls.port),
                "--no-proxy-headers",
            ],
            cwd=BACKEND,
            env=cls.env,
            stdout=cls.log,
            stderr=cls.log,
        )
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            try:
                if httpx.get(cls.url + "/api/health", timeout=1).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            if cls.process.poll() is not None:
                break
            time.sleep(0.15)
        cls.log.seek(0)
        raise RuntimeError("隔离测试后端未启动：" + cls.log.read()[-3000:])

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        cls.process.wait(timeout=20)
        cls.log.close()
        cls.temporary.cleanup()

    def setUp(self):
        self.client = httpx.Client(base_url=self.url, timeout=90)
        self.addCleanup(self.client.close)
        self.client.get("/api/session")

    def chat(self, question, conversation_id=None):
        response = self.client.post(
            "/api/chat/stream",
            json={
                "question": question,
                "conversation_id": conversation_id or str(uuid.uuid4()),
            },
            headers={"X-Client-Request": "1"},
        )
        self.assertEqual(response.status_code, 200, response.text[:500])
        events = []
        for block in response.text.split("\n\n"):
            if not block.startswith("event: "):
                continue
            name, body = block.split("\ndata: ", 1)
            events.append((name[7:], json.loads(body)))
        errors = [data for event, data in events if event == "error"]
        self.assertFalse(errors, errors)
        self.assertTrue(any(event == "token" for event, data in events))
        self.assertEqual(events[-1][0], "done")
        return events[-1][1]

    def test_01_real_mcp_product_order_and_rag_tools(self):
        product = self.chat("降噪耳机多少钱，还有库存吗？")
        self.assertIn("399.00", product["answer"])
        self.assertTrue(
            any(item["tool"] == "search_products" for item in product["trace"])
        )
        order = self.chat("查一下 SG20260002 的物流")
        self.assertIn("运输中", order["answer"])
        knowledge = self.chat("七天无理由退货的运费谁承担？")
        self.assertTrue(knowledge["sources"])
        self.assertTrue(
            any("退换货规则" in item["source"] for item in knowledge["sources"])
        )
        self.assertIn("search_knowledge", [item["tool"] for item in knowledge["trace"]])

    def test_02_after_sales_has_explicit_confirmation_and_no_real_refund(self):
        self.chat("SG20260001 可以退货吗？先只解释规则，不申请")
        self.assertEqual(self.client.get("/api/actions").json()["actions"], [])
        result = self.chat("为 SG20260001 申请退货，原因是不合适")
        self.assertIn("草稿", result["answer"])
        actions = self.client.get("/api/actions").json()["actions"]
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["status"], "pending")
        action_id = actions[0]["id"]
        with httpx.Client(base_url=self.url) as stranger:
            denied = stranger.post(
                f"/api/actions/{action_id}/confirm",
                json={"confirm": True},
                headers={"X-Client-Request": "1"},
            )
            self.assertEqual(denied.status_code, 404)
        missing = self.client.post(
            f"/api/actions/{action_id}/confirm", json={"confirm": True}
        )
        self.assertEqual(missing.status_code, 403)
        payload = {"json": {"confirm": True}, "headers": {"X-Client-Request": "1"}}
        first = self.client.post(f"/api/actions/{action_id}/confirm", **payload)
        again = self.client.post(f"/api/actions/{action_id}/confirm", **payload)
        self.assertEqual(
            first.json()["action"]["ticket_id"], again.json()["action"]["ticket_id"]
        )
        self.assertEqual(first.json()["action"]["status"], "submitted")
        self.assertIn("未发生退款", first.json()["notice"])
        order = next(
            item
            for item in self.client.get("/api/orders").json()["orders"]
            if item["id"] == "SG20260001"
        )
        self.assertEqual(order["status"], "已签收")

    def test_03_cross_customer_and_cross_browser_isolation(self):
        conversation_id = str(uuid.uuid4())
        self.chat("查一下 SG20260001", conversation_id)
        with httpx.Client(base_url=self.url) as stranger:
            self.assertEqual(
                stranger.get(f"/api/conversations/{conversation_id}").status_code, 404
            )
            self.assertEqual(
                stranger.get("/api/conversations").json()["conversations"], []
            )
            stolen = stranger.post(
                "/api/chat/stream",
                json={"question": "读取其他人历史", "conversation_id": conversation_id},
                headers={"X-Client-Request": "1"},
            )
            self.assertEqual(stolen.status_code, 404)
        self.client.post(
            "/api/session/customer",
            json={"customer_id": "demo-b"},
            headers={"X-Client-Request": "1"},
        )
        self.assertEqual(
            self.client.get(f"/api/conversations/{conversation_id}").status_code, 404
        )
        self.assertEqual(
            [item["id"] for item in self.client.get("/api/orders").json()["orders"]],
            ["SG20260003"],
        )
        hidden = self.chat("查一下 SG20260001")
        self.assertIn("未找到", hidden["answer"])
        self.assertNotIn("399.00", hidden["answer"])

    def test_04_internal_search_and_csrf_are_protected(self):
        self.assertEqual(
            self.client.post("/internal/search", json={"query": "退款"}).status_code,
            403,
        )
        cross = self.client.post(
            "/api/session/customer",
            json={"customer_id": "demo-b"},
            headers={"X-Client-Request": "1", "Origin": "https://evil.example"},
        )
        self.assertEqual(cross.status_code, 403)
        empty = self.client.post(
            "/api/chat/stream",
            json={"question": " "},
            headers={"X-Client-Request": "1"},
        )
        self.assertEqual(empty.status_code, 422)

    def test_05_restart_restores_chat_history_and_checkpoint(self):
        conversation_id = str(uuid.uuid4())
        self.chat("查一下 SG20260002", conversation_id)
        self.process.terminate()
        self.process.wait(timeout=20)
        type(self).start_server()
        restored = self.client.get(f"/api/conversations/{conversation_id}").json()[
            "conversation"
        ]
        self.assertEqual(len(restored["messages"]), 2)
        second = self.chat("保温杯多少钱？", conversation_id)
        self.assertTrue(second["sources"], "重启后向量集合也必须能检索，不能只恢复消息")
        saved = self.client.get(f"/api/conversations/{conversation_id}").json()[
            "conversation"
        ]
        self.assertEqual(
            [item["role"] for item in saved["messages"]],
            ["user", "assistant", "user", "assistant"],
        )
