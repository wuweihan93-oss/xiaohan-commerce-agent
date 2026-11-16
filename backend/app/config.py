"""项目配置独立加载，不依赖或修改小晗复习项目。"""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env", override=False)
DATA_DIR = Path(os.getenv("COMMERCE_DATA_DIR", str(ROOT / "backend/data"))).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
KNOWLEDGE_DIR = Path(
    os.getenv("COMMERCE_KNOWLEDGE_DIR", str(ROOT / "knowledge"))
).resolve()
DATABASE = DATA_DIR / "commerce.sqlite3"
CHECKPOINT_DB = DATA_DIR / "checkpoints.sqlite3"
CHAT_MODEL = os.getenv("CHAT_MODEL", "deepseek-chat")
CHAT_API_KEY = os.getenv("CHAT_API_KEY", "")
CHAT_BASE_URL = os.getenv("CHAT_BASE_URL", "")
EMBEDDING_MODE = os.getenv("EMBEDDING_MODE", "local")
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY", "")
EMBEDDING_BASE_URL = os.getenv("EMBEDDING_BASE_URL", "")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "")
SESSION_SECRET = os.getenv("SESSION_SECRET", "")
