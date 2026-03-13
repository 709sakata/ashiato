"""
あしあとプロジェクト - Stage1 切片化エージェント

EvidenceExtractor: 発言記録を観点別に分類して根拠発言を抽出する。
"""

import json
import logging

from ashiato.domain.viewpoints import VIEWPOINTS
from ashiato.infra.llm import call_ollama
from ashiato.prompts import load_prompt

logger = logging.getLogger(__name__)

EVIDENCE_SCHEMA = {
    "type": "object",
    "properties": {vp: {"type": "array", "items": {"type": "string"}} for vp in VIEWPOINTS},
    "required": VIEWPOINTS,
}


class EvidenceExtractor:
    """発言記録を3観点に分類し、根拠発言リストを返すエージェント。"""

    def run(
        self,
        child: str,
        transcript: str,
        session_info: dict | None = None,
    ) -> dict[str, list[str]]:
        """
        Args:
            child: 児童名
            transcript: build_transcript_per_child() が生成した発言テキスト
            session_info: セッション情報（school_type を含む）

        Returns:
            {"知識・技能": [...], "思考・判断・表現": [...], "主体的に学習に取り組む態度": [...]}
        """
        school_type = (session_info or {}).get("school_type", "小学校")

        system = (
            f"あなたは{school_type}指導要録の観点別学習評価を専門とする教育記録アナリストである。"
            f"この記録は{school_type}への出席扱い申請に使用される公的文書であり、"
            "発言の根拠が審査されるため正確性が厳格に求められる。"
            "発言テキストは一字一句変えずに記録し、推測・補完・解釈は絶対に行わない。"
        )

        prompt = load_prompt(
            "segment_evidence",
            child=child,
            school_type=school_type,
            transcript=transcript,
        )

        raw = call_ollama(
            prompt,
            system=system,
            num_predict=-1,
            format=EVIDENCE_SCHEMA,
            extra_options={"temperature": 0.1},
        )

        try:
            parsed = json.loads(raw)
            return {v: [u for u in parsed.get(v, []) if u] for v in VIEWPOINTS}
        except (json.JSONDecodeError, AttributeError) as e:
            logger.error("Stage1 JSONパース失敗（%s）: %s", child, e)
            logger.error("  Ollamaの出力（先頭200文字）: %r", raw[:200])
            logger.error("  対処方法: Ollamaのバージョンを確認してください（0.3.0以上必要）。")
            raise RuntimeError(f"Stage1 JSONパース失敗（{child}）") from e
