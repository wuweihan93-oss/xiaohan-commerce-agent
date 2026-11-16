"""打包代码与锁文件，排除密钥、虚拟环境和本机业务数据。"""

import argparse
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IGNORED = {
    ".env",
    ".venv",
    ".git",
    "node_modules",
    "dist",
    "__pycache__",
    ".ruff_cache",
    ".pytest_cache",
    ".DS_Store",
    "runtime",
    "data",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, "w:gz") as archive:
        for path in sorted(ROOT.rglob("*")):
            relative = path.relative_to(ROOT)
            if (
                not path.is_file()
                or path.is_symlink()
                or any(part in IGNORED for part in relative.parts)
                or path == output
                or path.suffix == ".pyc"
                or path.name.endswith(".tar.gz")
            ):
                continue
            archive.add(path, arcname=str(Path(ROOT.name) / relative), recursive=False)
    print(f"部署代码包已生成：{output}")
    print("未包含 .env 或数据；服务器首次运行 setup_env.py 创建独立配置。")


if __name__ == "__main__":
    main()
