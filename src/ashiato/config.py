"""
あしあとプロジェクト - 共有設定

環境変数でデフォルト値を上書き可能:
  SUPABASE_DB_URL       Supabase PostgreSQL 接続文字列（必須）
                        例: postgresql://postgres:[PASSWORD]@db.[REF].supabase.co:5432/postgres
  ASHIATO_OLLAMA_URL    Ollama API エンドポイント（デフォルト: http://localhost:11434/api/generate）
  ASHIATO_MODEL         使用するモデル名（デフォルト: qwen2.5:7b）
  ASHIATO_MAX_SESSIONS  コンテキストに含める過去セッション数（デフォルト: 4）
  ASHIATO_OLLAMA_TIMEOUT  Ollama リクエストタイムアウト秒数（デフォルト: 120）
  ASHIATO_MAX_RETRIES   Ollama 接続失敗時のリトライ回数（デフォルト: 3）
  ASHIATO_MAC_MINI      文字起こしサーバーのホスト名（デフォルト: mac-mini-ollama）
"""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

# プロジェクトルートの .env を読み込む（src/ashiato/ から3階層上）
env_path = Path(__file__).parent.parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s: %(message)s",
)

# Supabase 接続設定（db.py でも参照される）
SUPABASE_DB_URL: str = os.environ.get("SUPABASE_DB_URL", "")

OLLAMA_URL: str = os.environ.get("ASHIATO_OLLAMA_URL", "http://localhost:11434/api/generate")
MODEL: str = os.environ.get("ASHIATO_MODEL", "qwen2.5:7b")

MAX_SESSIONS: int = int(os.environ.get("ASHIATO_MAX_SESSIONS", "4"))

OLLAMA_TIMEOUT: int = int(os.environ.get("ASHIATO_OLLAMA_TIMEOUT", "120"))
OLLAMA_MAX_RETRIES: int = int(os.environ.get("ASHIATO_MAX_RETRIES", "3"))
