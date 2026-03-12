"""
utils.py の純粋関数のユニットテスト
"""
import csv
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import load_csv


class TestLoadCsv:
    def _write_csv(self, content: str, encoding: str = "utf-8") -> str:
        """一時CSVファイルを書き込んでパスを返す"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", encoding=encoding, delete=False, newline=""
        ) as f:
            f.write(content)
            return f.name

    def test_reads_basic_csv(self):
        path = self._write_csv("speaker,text\n山田,こんにちは\n太郎,やった\n")
        try:
            rows = load_csv(path)
            assert len(rows) == 2
            assert rows[0]["speaker"] == "山田"
            assert rows[1]["text"] == "やった"
        finally:
            os.unlink(path)

    def test_reads_utf8_bom(self):
        path = self._write_csv(
            "speaker,text\n山田,はじめまして\n", encoding="utf-8-sig"
        )
        try:
            rows = load_csv(path)
            # BOM があっても正しくヘッダを認識できること
            assert "speaker" in rows[0]
            assert rows[0]["speaker"] == "山田"
        finally:
            os.unlink(path)

    def test_returns_list_of_dicts(self):
        path = self._write_csv("a,b\n1,2\n")
        try:
            rows = load_csv(path)
            assert isinstance(rows, list)
            assert isinstance(rows[0], dict)
        finally:
            os.unlink(path)

    def test_empty_csv_returns_empty_list(self):
        path = self._write_csv("speaker,text\n")
        try:
            rows = load_csv(path)
            assert rows == []
        finally:
            os.unlink(path)
