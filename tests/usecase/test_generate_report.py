"""
usecase/generate_report.py の純粋関数（LLM・DB不要）のユニットテスト
"""
import pytest

from ashiato.usecase.generate_report import (
    build_context_section,
    normalize_child_report,
    normalize_child_report_parent,
)


# ---------------------------------------------------------------------------
# normalize_child_report
# ---------------------------------------------------------------------------

class TestNormalizeChildReport:
    def test_normalizes_child_heading(self):
        raw = "#### 太郎\n\n### 知識・技能\nテキスト"
        result = normalize_child_report("太郎", raw)
        assert result.startswith("## 太郎")

    def test_adds_missing_child_heading(self):
        raw = "### 知識・技能\n内容"
        result = normalize_child_report("花子", raw)
        assert "## 花子" in result

    def test_normalizes_viewpoint_headings(self):
        raw = "## 太郎\n\n#### 知識・技能\n内容"
        result = normalize_child_report("太郎", raw)
        assert "### 知識・技能" in result

    def test_replaces_pronoun_kare(self):
        raw = "## 太郎\n\n### 知識・技能\n彼は虫を見つけた。"
        result = normalize_child_report("太郎", raw)
        assert "彼は" not in result
        assert "太郎は" in result

    def test_replaces_pronoun_kanojo(self):
        raw = "## 花子\n\n### 思考・判断・表現\n彼女は考えた。"
        result = normalize_child_report("花子", raw)
        assert "彼女は" not in result
        assert "花子は" in result

    def test_no_false_pronoun_replacement(self):
        raw = "## 太郎\n\n### 知識・技能\n彼岸花を発見した。"
        result = normalize_child_report("太郎", raw)
        assert "彼岸花" in result


# ---------------------------------------------------------------------------
# normalize_child_report_parent
# ---------------------------------------------------------------------------

class TestNormalizeChildReportParent:
    def test_replaces_pronoun_kare(self):
        raw = "## 太郎さんの活動のようす\n\n### 今日の発見・できたこと\n彼は虫を見つけました。"
        result = normalize_child_report_parent("太郎", raw)
        assert "彼は" not in result
        assert "太郎さんは" in result

    def test_replaces_pronoun_kanojo(self):
        raw = "## 花子さんの活動のようす\n\n### 考えたこと\n彼女が話してくれました。"
        result = normalize_child_report_parent("花子", raw)
        assert "彼女が" not in result
        assert "花子さんが" in result

    def test_no_false_pronoun_replacement(self):
        raw = "## 太郎さんの活動のようす\n\n### 今日の発見・できたこと\n彼岸花を見つけました。"
        result = normalize_child_report_parent("太郎", raw)
        assert "彼岸花" in result

    def test_strips_whitespace(self):
        raw = "\n\n## 花子さんの活動のようす\n\n内容\n\n"
        result = normalize_child_report_parent("花子", raw)
        assert not result.startswith("\n")
        assert not result.endswith("\n")

    def test_does_not_normalize_viewpoint_headings(self):
        """保護者向けは観点名の見出し正規化を行わない"""
        raw = "## 太郎さんの活動のようす\n\n### 今日の発見・できたこと\n内容"
        result = normalize_child_report_parent("太郎", raw)
        assert "### 今日の発見・できたこと" in result


# ---------------------------------------------------------------------------
# build_context_section
# ---------------------------------------------------------------------------

class TestBuildContextSection:
    def test_empty_context(self):
        result = build_context_section("太郎", {"plan_goals": None, "history": []})
        assert result == ""

    def test_with_plan_goals(self):
        context = {
            "plan_goals": {
                "goals": {"知識・技能": "自然の生き物を知る"},
                "period": "2026年1月 ～ 2026年3月",
            },
            "history": [],
        }
        result = build_context_section("太郎", context)
        assert "個別支援計画" in result
        assert "知識・技能" in result
        assert "自然の生き物を知る" in result

    def test_with_history(self):
        context = {
            "plan_goals": None,
            "history": [
                {
                    "date": "2026-01-10",
                    "activity": "虫採り",
                    "counts": {"知識・技能": 2, "思考・判断・表現": 1, "主体的に学習に取り組む態度": 0},
                    "samples": {"知識・技能": "カブトムシを見つけた"},
                }
            ],
        }
        result = build_context_section("太郎", context)
        assert "2026-01-10" in result
        assert "虫採り" in result
