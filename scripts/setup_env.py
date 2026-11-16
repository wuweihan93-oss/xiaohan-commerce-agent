"""生成独立配置，可选复制模型配置，不输出密钥、不复用数据库。"""

import argparse
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--copy-model-from", type=Path, help="可选：从现有 .env 复制模型配置"
    )
    args = parser.parse_args()
    target = ROOT / ".env"
    if target.exists():
        print("项目 .env 已存在，保留现有配置。")
        return
    text = (ROOT / ".env.example").read_text()
    values = {"SESSION_SECRET": secrets.token_hex(32)}
    if args.copy_model_from:
        sys.path.insert(0, str(ROOT / "backend"))
        from dotenv import dotenv_values

        source = dotenv_values(args.copy_model_from)
        mapping = {
            "CHAT_API_KEY": "DEEPSEEK_API_KEY",
            "CHAT_BASE_URL": "DEEPSEEK_BASE_URL",
            "CHAT_MODEL": "REVIEW_LLM_MODEL",
            "EMBEDDING_API_KEY": "EMBEDDING_API_KEY",
            "EMBEDDING_BASE_URL": "EMBEDDING_BASE_URL",
            "EMBEDDING_MODEL": "EMBEDDING_MODEL",
        }
        values.update(
            {new: source[old] for new, old in mapping.items() if source.get(old)}
        )
    lines = []
    for line in text.splitlines():
        key = line.split("=", 1)[0]
        if key in values:
            value = str(values[key]).replace("'", "\\'")
            line = f"{key}='{value}'"
        lines.append(line)
    with target.open("x") as stream:
        stream.write("\n".join(lines) + "\n")
    target.chmod(0o600)
    print("已生成独立 .env；请填写聊天模型 API Key 和兼容地址后启动。")


if __name__ == "__main__":
    main()
