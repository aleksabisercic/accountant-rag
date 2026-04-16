import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

def load_config(path: Path | None = None) -> dict:
    path = path or BASE_DIR / "config.yaml"
    with open(path) as f:
        return yaml.safe_load(f)

CONFIG = load_config()

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
COHERE_API_KEY = os.environ["COHERE_API_KEY"]
QDRANT_URL = os.environ["QDRANT_URL"]
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
