"""
usecase/segment_evidence.py の純粋関数（LLM不要）のユニットテスト
"""
import os
import tempfile

import pytest

from ashiato.usecase.segment_evidence import (
    build_full_transcript,
    build_transcript_per_child,
    get_children,
    load_meta_txt,
)


# ---------------------------------------------------------------------------
# get_children
# ---------------------------------------------------------------------------

class TestGetChildren:
    def test_excludes_supporter(self):
        rows = [
            {"speaker": "山田", "text": "今日は虫採りだよ"},
            {"speaker": "太郎", "text": "やった！"},
            {"speaker": "花子", "text": "たのしい"},
        ]
        assert get_children(rows, "山田") == ["太郎", "花子"]

    def test_excludes_special_speakers(self):
        rows = [
            {"speaker": "山田", "text": "説明します"},
            {"speaker": "全員", "text": "はーい"},
            {"speaker": "不明", "text": "..."},
            {"speaker": "[不明]", "text": "（聞き取り不明）"},
            {"speaker": "", "text": ""},
            {"speaker": "太郎", "text": "面白い"},
        ]
        result = get_children(rows, "山田")
        assert result == ["太郎"]

    def test_returns_sorted(self):
        rows = [
            {"speaker": "山田", "text": ""},
            {"speaker": "花子", "text": ""},
            {"speaker": "太郎", "text": ""},
        ]
        result = get_children(rows, "山田")
        assert result == sorted(result)

    def test_empty_rows(self):
        assert get_children([], "山田") == []


# ---------------------------------------------------------------------------
# build_transcript_per_child
# ---------------------------------------------------------------------------

class TestBuildTranscriptPerChild:
    def _rows(self):
        return [
            {"speaker": "山田", "text": "カブトムシを探してみよう"},
            {"speaker": "太郎", "text": "どこにいるの？"},
            {"speaker": "山田", "text": "クヌギの木の近くかな"},
            {"speaker": "太郎", "text": "あ、いた！"},
            {"speaker": "花子", "text": "私も見せて"},
        ]

    def test_includes_child_utterances(self):
        result = build_transcript_per_child(self._rows(), "太郎")
        assert "太郎: どこにいるの？" in result
        assert "太郎: あ、いた！" in result

    def test_includes_preceding_supporter_line(self):
        result = build_transcript_per_child(self._rows(), "太郎")
        assert "支援者: カブトムシを探してみよう" in result

    def test_excludes_other_child(self):
        result = build_transcript_per_child(self._rows(), "太郎")
        assert "花子" not in result

    def test_no_utterances_returns_empty(self):
        rows = [{"speaker": "山田", "text": "説明"}]
        result = build_transcript_per_child(rows, "太郎")
        assert result == ""


# ---------------------------------------------------------------------------
# build_full_transcript
# ---------------------------------------------------------------------------

class TestBuildFullTranscript:
    def test_includes_normal_utterances(self):
        rows = [
            {"start": "00:01", "speaker": "山田", "text": "始めます"},
            {"start": "00:02", "speaker": "太郎", "text": "はい"},
        ]
        result = build_full_transcript(rows)
        assert "[00:01] 山田: 始めます" in result
        assert "[00:02] 太郎: はい" in result

    def test_excludes_unclear_utterances(self):
        rows = [
            {"start": "00:01", "speaker": "山田", "text": "[聞き取り不明]"},
            {"start": "00:02", "speaker": "太郎", "text": "面白い"},
        ]
        result = build_full_transcript(rows)
        assert "聞き取り不明" not in result
        assert "面白い" in result

    def test_excludes_empty_text(self):
        rows = [
            {"start": "00:01", "speaker": "山田", "text": ""},
            {"start": "00:02", "speaker": "太郎", "text": "やった"},
        ]
        result = build_full_transcript(rows)
        assert "山田" not in result


# ---------------------------------------------------------------------------
# load_meta_txt
# ---------------------------------------------------------------------------

class TestLoadMetaTxt:
    def test_reads_all_fields(self):
        content = (
            "活動日: 2026年10月18日\n"
            "場所: 太子遊び冒険の森ASOBO\n"
            "活動内容: 虫採り・火起こし\n"
            "学校種別: 小学校\n"
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", encoding="utf-8", delete=False) as f:
            f.write(content)
            path = f.name

        try:
            result = load_meta_txt(path)
            assert result["date"] == "2026年10月18日"
            assert result["location"] == "太子遊び冒険の森ASOBO"
            assert result["activity"] == "虫採り・火起こし"
            assert result["school_type"] == "小学校"
        finally:
            os.unlink(path)

    def test_missing_fields_not_in_result(self):
        content = "活動日: 2026年10月18日\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", encoding="utf-8", delete=False) as f:
            f.write(content)
            path = f.name

        try:
            result = load_meta_txt(path)
            assert "date" in result
            assert "location" not in result
        finally:
            os.unlink(path)
