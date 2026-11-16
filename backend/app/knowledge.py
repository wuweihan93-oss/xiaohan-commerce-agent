"""Markdown → 递归切分 → Embedding → Milvus Lite → 带出处的检索结果。"""

import hashlib
import json
import math
import re
import threading

import httpx
from langchain_core.embeddings import Embeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pymilvus import MilvusClient

from app import config

_lock = threading.RLock()
_client = None
_collection = "commerce_knowledge"
_splitter = RecursiveCharacterTextSplitter(
    chunk_size=380, chunk_overlap=60, separators=["\n## ", "\n\n", "\n", "。", " ", ""]
)


class LocalEmbeddings(Embeddings):
    """可复现的字符 n-gram 哈希向量，只用于无需下载模型的检索演示。"""

    def embed_documents(self, texts):
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text):
        vector = [0.0] * 512
        text = re.sub(r"\s+", "", text.lower())
        for size in (1, 2, 3):
            for index in range(max(0, len(text) - size + 1)):
                token = text[index : index + size].encode()
                digest = hashlib.blake2b(token, digest_size=8).digest()
                vector[int.from_bytes(digest[:4], "big") % 512] += (
                    1 if digest[4] % 2 else -1
                )
        length = math.sqrt(sum(value * value for value in vector)) or 1
        return [value / length for value in vector]


class RemoteEmbeddings(Embeddings):
    def embed_documents(self, texts):
        vectors = []
        headers = {"Authorization": f"Bearer {config.EMBEDDING_API_KEY}"}
        with httpx.Client(timeout=60, headers=headers) as client:
            for start in range(0, len(texts), 8):
                batch = texts[start : start + 8]
                if config.EMBEDDING_MODE == "dashscope":
                    # 沿用学习项目的百炼多模态接口；这里的 Base URL 不是 compatible-mode 地址。
                    response = client.post(
                        config.EMBEDDING_BASE_URL.rstrip("/")
                        + "/services/embeddings/multimodal-embedding/multimodal-embedding",
                        json={
                            "model": config.EMBEDDING_MODEL,
                            "input": {"contents": [{"text": text} for text in batch]},
                        },
                    )
                    key = "embeddings"
                else:
                    response = client.post(
                        config.EMBEDDING_BASE_URL.rstrip("/") + "/embeddings",
                        json={"model": config.EMBEDDING_MODEL, "input": batch},
                    )
                    key = "data"
                if response.is_error:
                    raise RuntimeError(
                        f"向量服务返回 HTTP {response.status_code}，请检查 Embedding 配置及额度"
                    )
                payload = response.json()
                items = (
                    payload.get("output", {}).get(key, [])
                    if config.EMBEDDING_MODE == "dashscope"
                    else payload.get(key, [])
                )
                items = sorted(items, key=lambda item: item.get("index", 0))
                if len(items) != len(batch) or any(
                    not item.get("embedding") for item in items
                ):
                    raise RuntimeError("向量服务返回数量不正确")
                vectors.extend(item["embedding"] for item in items)
        return vectors

    def embed_query(self, text):
        return self.embed_documents([text])[0]


def embedder():
    if config.EMBEDDING_MODE == "local":
        return LocalEmbeddings()
    if config.EMBEDDING_MODE not in {"dashscope", "openai"}:
        raise ValueError("EMBEDDING_MODE 只支持 local、dashscope、openai")
    if not all(
        (config.EMBEDDING_API_KEY, config.EMBEDDING_BASE_URL, config.EMBEDDING_MODEL)
    ):
        raise ValueError("请完整填写 Embedding 密钥、地址和模型")
    return RemoteEmbeddings()


def initialize():
    global _client
    with _lock:
        files = sorted(config.KNOWLEDGE_DIR.glob("*.md"))
        content = [(path.name, path.read_text(encoding="utf-8")) for path in files]
        fingerprint = hashlib.sha256(
            json.dumps(
                [
                    config.EMBEDDING_MODE,
                    config.EMBEDDING_BASE_URL,
                    config.EMBEDDING_MODEL,
                    content,
                ],
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        manifest = config.DATA_DIR / "knowledge.json"
        previous = json.loads(manifest.read_text()) if manifest.exists() else {}
        if _client is None:
            _client = MilvusClient(uri=str(config.DATA_DIR / "knowledge.db"))
        if previous.get("fingerprint") == fingerprint and _client.has_collection(
            _collection
        ):
            # Lite 重启后集合可能处于 released 状态；文件存在不等于已加载。
            _client.load_collection(_collection)
            return previous
        documents = []
        for filename, text in content:
            for index, chunk in enumerate(_splitter.split_text(text)):
                documents.append(
                    {
                        "id": len(documents) + 1,
                        "source": filename,
                        "text": chunk,
                        "chunk": index + 1,
                    }
                )
        if not documents:
            raise ValueError("knowledge 目录至少需要一份非空 Markdown 文件")
        vectors = embedder().embed_documents([item["text"] for item in documents])
        # 独立 collection，仅重建此 demo 的文件库，不连接旧项目的 Milvus。
        if _client.has_collection(_collection):
            _client.drop_collection(_collection)
        _client.create_collection(
            collection_name=_collection,
            dimension=len(vectors[0]),
            metric_type="COSINE",
            auto_id=False,
        )
        _client.insert(
            _collection,
            [{**item, "vector": vector} for item, vector in zip(documents, vectors)],
        )
        _client.load_collection(_collection)
        result = {
            "fingerprint": fingerprint,
            "files": [name for name, _ in content],
            "chunks": len(documents),
            "embedding": config.EMBEDDING_MODE,
            "dimension": len(vectors[0]),
        }
        temporary = manifest.with_suffix(".tmp")
        temporary.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        temporary.replace(manifest)
        return result


def search(query):
    with _lock:
        initialize()
        results = _client.search(
            _collection,
            data=[embedder().embed_query(query)],
            limit=4,
            output_fields=["source", "text", "chunk"],
        )
        return [
            {**hit["entity"], "score": round(hit["distance"], 4)}
            for hit in results[0]
            if hit["distance"] > 0.03
        ]


def close():
    global _client
    with _lock:
        if _client is not None:
            _client.close()
            _client = None
