"""
あしあとプロジェクト - 共有設定

環境変数でデフォルト値を上書き可能:
  ASHIATO_OLLAMA_URL    Ollama API エンドポイント（デフォルト: http://localhost:11434/api/generate）
  ASHIATO_MODEL         使用するモデル名（デフォルト: qwen2.5:7b）
  ASHIATO_MAX_SESSIONS  コンテキストに含める過去セッション数（デフォルト: 4）
  ASHIATO_DB            DBファイルパス（デフォルト: <スクリプトディレクトリ>/db/ashiato.db）
  ASHIATO_OLLAMA_TIMEOUT  Ollama リクエストタイムアウト秒数（デフォルト: 120）
  ASHIATO_MAX_RETRIES   Ollama 接続失敗時のリトライ回数（デフォルト: 3）
  ASHIATO_MAC_MINI      文字起こしサーバーのホスト名（デフォルト: mac-mini-ollama）
"""

import os
from pathlib import Path

OLLAMA_URL: str = os.environ.get("ASHIATO_OLLAMA_URL", "http://localhost:11434/api/generate")
MODEL: str = os.environ.get("ASHIATO_MODEL", "qwen2.5:7b")

VIEWPOINTS: list[str] = [
    "知識・技能",
    "思考・判断・表現",
    "主体的に学習に取り組む態度",
]

MAX_SESSIONS: int = int(os.environ.get("ASHIATO_MAX_SESSIONS", "4"))

DEFAULT_DB: Path = Path(
    os.environ.get("ASHIATO_DB", str(Path(__file__).parent / "db" / "ashiato.db"))
)

OLLAMA_TIMEOUT: int = int(os.environ.get("ASHIATO_OLLAMA_TIMEOUT", "120"))
OLLAMA_MAX_RETRIES: int = int(os.environ.get("ASHIATO_MAX_RETRIES", "3"))
