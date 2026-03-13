"""
usecase/map_speakers.py の純粋関数のユニットテスト
"""
import pytest

from usecase.map_speakers import _sanitize_name


class TestSanitizeName:
    def test_removes_double_quote(self):
        assert '"太郎"' not in _sanitize_name('"太郎"')
        assert "太郎" == _sanitize_name('"太郎"')

    def test_removes_newline(self):
        result = _sanitize_name("太\n郎")
        assert "\n" not in result
        assert "太郎" == result

    def test_removes_carriage_return(self):
        result = _sanitize_name("太\r郎")
        assert "\r" not in result

    def test_strips_whitespace(self):
        assert _sanitize_name("  太郎  ") == "太郎"

    def test_truncates_at_max_length(self):
        long_name = "あ" * 100
        result = _sanitize_name(long_name, max_length=50)
        assert len(result) == 50

    def test_default_max_length_50(self):
        long_name = "あ" * 60
        result = _sanitize_name(long_name)
        assert len(result) == 50

    def test_normal_name_unchanged(self):
        assert _sanitize_name("山田太郎") == "山田太郎"

    def test_empty_string(self):
        assert _sanitize_name("") == ""

    def test_only_spaces(self):
        assert _sanitize_name("   ") == ""
