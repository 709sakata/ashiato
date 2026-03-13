"""
あしあとプロジェクト - CSV I/O ユーティリティ

load_csv(): UTF-8-BOM 対応 CSV 読み込み
"""

import csv


def load_csv(path: str) -> list[dict]:
    """UTF-8-BOM 対応で CSV ファイルを読み込み、行の辞書リストを返す。"""
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))
