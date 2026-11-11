import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

_ROOT = Path(__file__).resolve().parents[1]
if load_dotenv:
    # 环境变量优先；项目内 .env.example 作为本地开发回退配置。
    load_dotenv(_ROOT / ".env", override=False)
    load_dotenv(_ROOT / ".env.example", override=False)


def data_dir() -> Path:
    root = Path(os.getenv("DATA_DIR", "data"))
    root.mkdir(parents=True, exist_ok=True)
    return root


HTTP_TIMEOUT_SECONDS = float(os.getenv("HTTP_TIMEOUT_SECONDS", "30"))
