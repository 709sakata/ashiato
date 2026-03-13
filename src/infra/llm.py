"""
あしあとプロジェクト - Ollama API クライアント

call_ollama(): 指数バックオフ付きリトライ対応の Ollama HTTP クライアント
"""

import json
import logging
import sys
import time
import urllib.error
import urllib.request

from config import MODEL, OLLAMA_MAX_RETRIES, OLLAMA_TIMEOUT, OLLAMA_URL

logger = logging.getLogger(__name__)


def call_ollama(
    prompt: str,
    system: str = "",
    num_predict: int = -1,
    format: dict | None = None,
    extra_options: dict | None = None,
) -> str:
    """Ollama にリクエストを送信し、レスポンステキストを返す。

    接続失敗時は指数バックオフでリトライ（最大 OLLAMA_MAX_RETRIES 回）。
    全試行失敗時は標準エラーにメッセージを出力して sys.exit(1)。
    """
    body: dict = {
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": num_predict, **(extra_options or {})},
    }
    if system:
        body["system"] = system
    if format:
        body["format"] = format

    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    last_error: Exception | None = None
    for attempt in range(OLLAMA_MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8")).get("response", "").strip()
        except urllib.error.URLError as e:
            last_error = e
            if attempt < OLLAMA_MAX_RETRIES - 1:
                wait = 2**attempt  # 1s, 2s, 4s, ...
                logger.warning(
                    "Ollama接続失敗（試行%d/%d）。%d秒後にリトライ: %s",
                    attempt + 1, OLLAMA_MAX_RETRIES, wait, e,
                )
                time.sleep(wait)

    logger.error("Ollama接続失敗（%d回試行）: %s", OLLAMA_MAX_RETRIES, last_error)
    sys.exit(1)
