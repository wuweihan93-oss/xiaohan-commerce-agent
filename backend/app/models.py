"""初始化客服 Agent 使用的聊天模型。"""

from langchain.chat_models import init_chat_model

from app import config


def create_chat_model():
    """按项目配置创建 OpenAI 兼容聊天模型。"""
    if not config.CHAT_API_KEY or not config.CHAT_BASE_URL:
        raise ValueError("请先填写 CHAT_API_KEY 和 CHAT_BASE_URL")

    # init_chat_model 与教程中的模型初始化一致；OpenAI provider 可接 DeepSeek 等兼容地址。
    return init_chat_model(
        model=config.CHAT_MODEL,
        model_provider="openai",
        api_key=config.CHAT_API_KEY,
        base_url=config.CHAT_BASE_URL,
        temperature=0.2,
        timeout=60,
        max_retries=1,
        max_tokens=1000,
    )
