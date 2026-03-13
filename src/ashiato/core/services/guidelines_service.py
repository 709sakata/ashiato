"""
あしあとプロジェクト - ガイドラインRAGサービス

GuidelinesRetriever: ベクトルストアから関連ガイドラインチャンクを取得し、
                     プロンプト挿入用のテキストに整形する。

使用方法:
    retriever = GuidelinesRetriever()
    if retriever.is_available():
        chunks = retriever.retrieve("中学校 観点別評価 知識・技能", "中学校")
        text = retriever.format_for_prompt(chunks)
"""

import logging
from functools import cached_property

from ashiato.config import GUIDELINES_ENABLED, GUIDELINES_INDEX_DIR, GUIDELINES_TOP_K
from ashiato.infra.embeddings import embed
from ashiato.infra.vector_store import VectorStore

logger = logging.getLogger(__name__)


class GuidelinesRetriever:
    """ガイドラインPDFのベクトルインデックスから関連チャンクを検索するRAGサービス。

    ASHIATO_GUIDELINES_ENABLED=false（デフォルト）の場合はすべての操作がno-opになる。
    インデックス未構築の場合も空リストを返すグレースフルデグラデーション動作。
    """

    def __init__(self, index_dir: str | None = None):
        self._index_dir = index_dir or GUIDELINES_INDEX_DIR
        self._enabled = GUIDELINES_ENABLED

    @cached_property
    def _store(self) -> VectorStore:
        return VectorStore(self._index_dir)

    def is_available(self) -> bool:
        """RAGが利用可能かどうかを返す（有効化済み + インデックス構築済み）。"""
        if not self._enabled:
            return False
        return self._store.is_built()

    def retrieve(
        self,
        query: str,
        school_type: str,
        top_k: int = GUIDELINES_TOP_K,
        source_type: str | None = None,
    ) -> list[dict]:
        """クエリに関連するガイドラインチャンクを返す。

        Args:
            query: 検索クエリ文字列
            school_type: 対象学校種別（"小学校" / "中学校"）。school_type="共通"のチャンクも含む。
            top_k: 返すチャンク数
            source_type: "current" / "future" / None（両方）で絞り込み

        Returns:
            スコア降順のチャンクリスト。利用不可・失敗時は []。
        """
        if not self.is_available():
            return []

        query_vec = embed(query)
        if query_vec is None:
            logger.warning("クエリのエンベディング取得失敗。ガイドラインRAGをスキップします。")
            return []

        return self._store.search(
            query_vec,
            top_k=top_k,
            filter_school_type=school_type,
            filter_source_type=source_type,
        )

    def format_for_prompt(self, chunks: list[dict]) -> str:
        """チャンクリストをプロンプト挿入用テキストに整形する。

        Args:
            chunks: retrieve() の戻り値

        Returns:
            プロンプトに挿入するための整形済みテキスト。チャンクが空の場合は ""。
        """
        if not chunks:
            return ""

        lines = []
        for chunk in chunks:
            source = chunk.get("source", "")
            subject = chunk.get("subject", "")
            page = chunk.get("page", "")
            header = f"【出典: {source}（{subject}、p.{page}）】"
            lines.append(header)
            lines.append(chunk.get("text", "").strip())
            lines.append("")

        return "\n".join(lines).strip()
