"""
あしあとプロジェクト - 個別支援計画エージェント

PlanAgent: 保護者面談記録や蓄積セッションデータから個別支援計画を生成・更新する。
"""

import logging

from ashiato.infra.llm import call_ollama
from ashiato.prompts import load_prompt

logger = logging.getLogger(__name__)


class PlanAgent:
    """個別支援計画の初回作成・四半期更新を行うエージェント。"""

    def generate_init_plan(
        self,
        child: str,
        school_type: str,
        period_start: str,
        period_end: str,
        info_section: str,
    ) -> str:
        """
        初回の個別支援計画を生成する。

        Args:
            child: 児童名
            school_type: 学校種別（小学校/中学校）
            period_start: 計画期間開始
            period_end: 計画期間終了
            info_section: 面談記録または対話入力テキスト

        Returns:
            Markdown形式の計画書文字列
        """
        system = (
            f"あなたは{school_type}の特別支援教育コーディネーターであり、"
            "フリースクール「あしあと」（太子遊び冒険の森ASOBO）での体験学習を通じた"
            "個別支援計画の作成専門家である。"
            "提供された情報のみに基づき、具体的・行動観察可能な計画書を作成する。"
        )

        prompt = load_prompt(
            "support_plan_init",
            child=child,
            school_type=school_type,
            period_start=period_start,
            period_end=period_end,
            info_section=info_section,
        )

        return call_ollama(
            prompt,
            system=system,
            num_predict=3000,
            extra_options={"repeat_penalty": 1.1},
        )

    def generate_update_plan(
        self,
        child: str,
        school_type: str,
        date_range: str,
        session_count: int,
        new_period_start: str,
        new_period_end: str,
        current_plan_version: int,
        current_plan_content: str,
        history_text: str,
        trend_text: str,
    ) -> str:
        """
        四半期更新版の個別支援計画を生成する。

        Args:
            child: 児童名
            school_type: 学校種別
            date_range: 評価期間（例: "2026-01-10 ～ 2026-03-15"）
            session_count: 評価期間のセッション数
            new_period_start: 新計画期間開始
            new_period_end: 新計画期間終了
            current_plan_version: 現行計画バージョン
            current_plan_content: 現行計画の本文
            history_text: セッション記録テキスト
            trend_text: 観点別数値推移テキスト

        Returns:
            Markdown形式の改定計画書文字列
        """
        system = (
            f"あなたは{school_type}の特別支援教育コーディネーターであり、"
            "フリースクール「あしあと」の体験学習記録をもとに"
            "個別支援計画を定期的に改定する専門家である。"
            "記録にある発言のみを根拠に成長を評価し、次期目標を設定する。"
        )

        prompt = load_prompt(
            "support_plan_update",
            child=child,
            school_type=school_type,
            date_range=date_range,
            session_count=session_count,
            new_period_start=new_period_start,
            new_period_end=new_period_end,
            current_plan_version=current_plan_version,
            current_plan_content=current_plan_content,
            history_text=history_text,
            trend_text=trend_text,
        )

        return call_ollama(
            prompt,
            system=system,
            num_predict=3000,
            extra_options={"repeat_penalty": 1.1},
        )
