"""
あしあとプロジェクト - 共有ユーティリティ

- call_ollama(): 指数バックオフ付きリトライ対応の Ollama API クライアント
- load_csv(): UTF-8-BOM 対応 CSV 読み込み
"""

import csv
import json
import sys
import time
import urllib.error
import urllib.request

from config import MODEL, OLLAMA_MAX_RETRIES, OLLAMA_TIMEOUT, OLLAMA_URL


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
                print(
                    f"[WARNING] Ollama接続失敗（試行{attempt + 1}/{OLLAMA_MAX_RETRIES}）。"
                    f"{wait}秒後にリトライ: {e}",
                    file=sys.stderr,
                )
                time.sleep(wait)

    print(
        f"[ERROR] Ollama接続失敗（{OLLAMA_MAX_RETRIES}回試行）: {last_error}",
        file=sys.stderr,
    )
    sys.exit(1)


def load_csv(path: str) -> list[dict]:
    """UTF-8-BOM 対応で CSV ファイルを読み込み、行の辞書リストを返す。"""
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))
