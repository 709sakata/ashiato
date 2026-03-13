"""
あしあとプロジェクト - Ollamaエンベディングクライアント

embed(): テキストをOllamaのエンベディングモデルでベクトル化する。
"""

import json
import logging
import time
import urllib.error
import urllib.request

from ashiato.config import EMBED_MODEL, OLLAMA_MAX_RETRIES, OLLAMA_TIMEOUT, OLLAMA_URL

logger = logging.getLogger(__name__)

# エンベディングエンドポイントはgenerate用URLから派生させる
# 例: http://localhost:11434/api/generate → http://localhost:11434/api/embeddings
_EMBED_URL = OLLAMA_URL.replace("/api/generate", "/api/embeddings")


def embed(text: str, model: str = EMBED_MODEL) -> list[float] | None:
    """Ollamaのエンベディングモデルでテキストをベクトル化する。

    Args:
        text: エンベディング対象のテキスト
        model: 使用するOllamaモデル名（デフォルト: EMBED_MODEL設定値）

    Returns:
        浮動小数点数のリスト（ベクトル）。失敗時は None。
    """
    payload = json.dumps({"model": model, "prompt": text}).encode("utf-8")
    req = urllib.request.Request(
        _EMBED_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    last_error: Exception | None = None
    for attempt in range(OLLAMA_MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                embedding = data.get("embedding")
                if embedding:
                    return embedding
                logger.warning("Ollamaからembeddingが返されませんでした（model: %s）", model)
                return None
        except urllib.error.URLError as e:
            last_error = e
            if attempt < OLLAMA_MAX_RETRIES - 1:
                wait = 2**attempt
                logger.warning(
                    "エンベディング接続失敗（試行%d/%d）。%d秒後にリトライ: %s",
                    attempt + 1, OLLAMA_MAX_RETRIES, wait, e,
                )
                time.sleep(wait)

    logger.error("エンベディング接続失敗（%d回試行）: %s", OLLAMA_MAX_RETRIES, last_error)
    return None
