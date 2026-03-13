"""
あしあとプロジェクト - Stage2 報告書生成エージェント

ReportGenerator: 切片化済み根拠発言リストから観点別記述とセッションサマリーを生成する。
"""

import logging

from ashiato.domain.viewpoints import VIEWPOINTS
from ashiato.infra.llm import call_ollama
from ashiato.prompts import load_prompt

logger = logging.getLogger(__name__)

# オーディエンス別システムプロンプト（child レポート用）
_CHILD_SYSTEM: dict[str, str] = {
    "principal": (
        "あなたは{school_type}指導要録の観点別学習状況記述を専門とする教育記録作成者である。"
        "提供された根拠発言リストのみを根拠に、支援者が観察した事実として記述する。"
        "リストに存在しない事実の創作・推測・補完は絶対に行わない。"
        "この記録は学校への出席扱い申請に使用される公式文書の下書きであり、"
        "教員が内容を確認・承認した上で正式記録となる。"
    ),
    "parent": (
        "あなたはフリースクール「あしあと」の保護者向け活動報告書を作成する教育アドバイザーです。"
        "子どもの発言・行動の事実を温かく読みやすい文体で保護者に伝えることを専門とします。"
        "提供された根拠発言リストのみを根拠に記述します。"
        "子どもの発言は「」で直接引用し、リストに存在しない感情・印象の推測は行いません。"
    ),
}

# オーディエンス別システムプロンプト（summary 用）
_SUMMARY_SYSTEM: dict[str, str] = {
    "principal": (
        "あなたはNPO法人姫路YMCAが運営するフリースクール「あしあと」（太子遊び冒険の森ASOBO）の公式記録補助者である。"
        "校長先生への活動報告書（出席扱い申請書添付用）の冒頭総括文を作成することを専門とする。"
        "与えられた【各児童の発言抜粋】に存在する事実のみを根拠に記述する。"
        "記録にない活動・様子・発言の創作・推測・補完は絶対に行わない。"
    ),
    "parent": (
        "あなたはフリースクール「あしあと」の活動便りを作成する教育アドバイザーです。"
        "保護者の方がセッションの様子を親しみやすく理解できるよう、活動の概況を温かく伝えます。"
        "提供された発言抜粋に存在する事実のみを根拠に記述します。"
    ),
}


class ReportGenerator:
    """根拠発言リストから指導要録記述を生成するエージェント。"""

    def generate_child_report(
        self,
        child: str,
        evidence: dict[str, list[str]],
        session_info: dict,
        *,
        audience: str = "principal",
        db_context: dict | None = None,
        build_context_section_fn=None,
        guidelines_retriever=None,
    ) -> str:
        """
        観点別根拠発言リストから児童ごとの記述を生成する。

        Args:
            child: 児童名
            evidence: {"観点": [発言, ...]} の切片化済みデータ
            session_info: セッション情報
            audience: 報告書の読者 ("principal" | "parent")
            db_context: load_context_for_report() の戻り値（省略可）
            build_context_section_fn: コンテキストセクションを構築する関数（省略可）
            guidelines_retriever: GuidelinesRetriever | None（省略可）

        Returns:
            Markdown形式の記述文字列
        """
        school_type = session_info.get("school_type", "小学校")

        # principal のみ school_type を埋め込む（parent プロンプトにはプレースホルダーなし）
        if audience == "principal":
            system = _CHILD_SYSTEM["principal"].format(school_type=school_type)
        else:
            system = _CHILD_SYSTEM[audience]

        # RAG は principal のみ使用
        if audience != "principal":
            guidelines_retriever = None

        evidence_counts = {v: len(evidence[v]) for v in VIEWPOINTS}
        evidence_parts = []
        for v in VIEWPOINTS:
            utterances = evidence[v]
            evidence_parts.append(f"### {v}（{evidence_counts[v]}件）")
            if utterances:
                for u in utterances:
                    evidence_parts.append(f"- 「{u}」")
            else:
                evidence_parts.append("- （根拠発言なし）")
        evidence_text = "\n".join(evidence_parts)

        context_section = ""
        continuity_instruction = ""
        if db_context and (db_context.get("plan_goals") or db_context.get("history")):
            if build_context_section_fn:
                context_section = "# 参考情報（記述の文脈として使用）\n" + build_context_section_fn(child, db_context)
            continuity_instruction = (
                "8. 参考情報（過去履歴・支援計画）は記述の「文脈」として活用し、"
                "今回の根拠発言と自然につながる流れで書くこと。"
                "ただし参考情報の内容を根拠として使ってはならない（今回の根拠発言が唯一の根拠）。"
                "成長の継続が今回の発言からも確認できる場合は「引き続き」「継続して」等の表現を使ってよい。"
            )

        # ガイドラインRAGによる参照箇所の取得
        guidelines_context = ""
        future_guidelines_context = ""
        if guidelines_retriever is not None and guidelines_retriever.is_available():
            # current/: 観点別学習状況の記述スタイル根拠
            curr_query = f"{school_type} 観点別学習状況 指導要録 記述"
            curr_chunks = guidelines_retriever.retrieve(curr_query, school_type, source_type="current")
            guidelines_context = guidelines_retriever.format_for_prompt(curr_chunks)
            # future/: 不登校支援・フリースクールの制度的背景
            fut_query = "不登校 フリースクール 出席扱い 体験活動 教育的意義"
            fut_chunks = guidelines_retriever.retrieve(fut_query, school_type, source_type="future")
            future_guidelines_context = guidelines_retriever.format_for_prompt(fut_chunks)
            if guidelines_context or future_guidelines_context:
                logger.debug(
                    "ガイドラインRAG: current %d チャンク, future %d チャンク取得（Stage2）",
                    len(curr_chunks), len(fut_chunks),
                )

        template_name = "generate_report_child" if audience == "principal" else f"generate_report_child_{audience}"
        prompt = load_prompt(
            template_name,
            child=child,
            school_type=school_type,
            session_date=session_info["date"],
            session_location=session_info["location"],
            session_activity=session_info["activity"],
            context_section=context_section,
            evidence_text=evidence_text,
            continuity_instruction=continuity_instruction,
            guidelines_context=guidelines_context,
            future_guidelines_context=future_guidelines_context,
            count_kn=evidence_counts["知識・技能"],
            count_th=evidence_counts["思考・判断・表現"],
            count_at=evidence_counts["主体的に学習に取り組む態度"],
        )

        return call_ollama(
            prompt,
            system=system,
            num_predict=2000,
            extra_options={"repeat_penalty": 1.1, "top_k": 20},
        )

    def generate_session_summary(
        self,
        children: list[str],
        session_info: dict,
        *,
        audience: str = "principal",
        child_counts: dict[str, int] | None = None,
        utterances_sample: str | None = None,
    ) -> str:
        """
        セッション全体のサマリー段落を生成する。

        Args:
            children: 参加児童名リスト
            session_info: セッション情報
            audience: 報告書の読者 ("principal" | "parent")
            child_counts: 児童別発言件数
            utterances_sample: 児童別代表発言テキスト（evidence.json から）

        Returns:
            サマリー文字列
        """
        stats_text = "\n".join(f"  {k}: {v}件" for k, v in (child_counts or {}).items())
        representative = utterances_sample or ""

        system = _SUMMARY_SYSTEM[audience]

        if audience == "parent":
            child_slot_lines = "\n".join(
                f"{c}さんは「（{c}さんの発言抜粋から1つ引用）」と話してくれるなど、（観察した様子を1文で）"
                for c in children
            )
        else:
            child_slot_lines = "\n".join(
                f"{c}は「（{c}の発言抜粋から1つ引用）」と発言するなど、（観察した事実を1文で）"
                for c in children
            )

        template_name = "generate_report_summary" if audience == "principal" else f"generate_report_summary_{audience}"
        prompt = load_prompt(
            template_name,
            session_date=session_info["date"],
            session_location=session_info["location"],
            session_activity=session_info["activity"],
            children_str=", ".join(children),
            children_count=len(children),
            stats_text=stats_text,
            representative=representative,
            child_slot_lines=child_slot_lines,
        )

        return call_ollama(prompt, system=system, num_predict=700)
