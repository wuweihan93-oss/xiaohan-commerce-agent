"""启动集成测试服务器，并在应用加载前注入测试模型。"""

import os

import app.models
from tests.fake_customer_model import FakeCustomerModel

app.models.create_chat_model = FakeCustomerModel

from app.main import app

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=int(os.environ["TEST_PORT"]),
        proxy_headers=False,
    )
