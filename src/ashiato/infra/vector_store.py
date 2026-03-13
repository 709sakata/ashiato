"""
あしあとプロジェクト - ファイルベースのベクトルストア

VectorStore: ガイドラインチャンクのベクトルをnumpy + JSONファイルに永続化し、
             コサイン類似度でtop-k検索を提供する。

保存形式:
  <index_dir>/chunks.json  チャンクテキスト + メタデータのリスト
  <index_dir>/vectors.npy  numpy配列 (N, D) - chunks.jsonと同インデックス対応
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# future/ チャンクに適用するスコア重み（currentを優先）
FUTURE_SCORE_WEIGHT = 0.85

# future/ チャンクに適用するボーナスキーワード（フリースクール文脈）
FUTURE_BONUS_KEYWORDS = frozenset(["不登校", "フリースクール", "学びの多様化", "出席扱い", "体験活動"])


class VectorStore:
    """ガイドラインチャンクのベクトルストア。"""

    def __init__(self, index_dir: str | Path):
        self.index_dir = Path(index_dir)
        self._chunks: list[dict] | None = None
        self._vectors = None  # numpy ndarray | None

    def _chunks_path(self) -> Path:
        return self.index_dir / "chunks.json"

    def _vectors_path(self) -> Path:
        return self.index_dir / "vectors.npy"

    def is_built(self) -> bool:
        """インデックスが構築済みかどうかを返す。"""
        return self._chunks_path().exists() and self._vectors_path().exists()

    def build(self, chunks: list[dict], vectors: list[list[float]]) -> None:
        """チャンクリストとベクトルリストからインデックスを構築して保存する。

        Args:
            chunks: [{"text", "source", "school_type", "source_type", "subject", "page"}, ...]
            vectors: 各チャンクに対応するエンベディングベクトルのリスト
        """
        import numpy as np

        assert len(chunks) == len(vectors), "チャンク数とベクトル数が一致しません"

        self.index_dir.mkdir(parents=True, exist_ok=True)

        # JSONでチャンクを保存
        with open(self._chunks_path(), "w", encoding="utf-8") as f:
            json.dump(chunks, f, ensure_ascii=False, indent=2)

        # numpyでベクトルを保存
        arr = np.array(vectors, dtype=np.float32)
        np.save(self._vectors_path(), arr)

        self._chunks = chunks
        self._vectors = arr

        logger.info(
            "ベクトルインデックスを保存: %d チャンク, ベクトル次元 %d → %s",
            len(chunks), arr.shape[1] if arr.ndim > 1 else 0, self.index_dir,
        )

    def _load(self) -> bool:
        """インデックスをメモリにロードする。既にロード済みなら何もしない。"""
        if self._chunks is not None and self._vectors is not None:
            return True

        if not self.is_built():
            return False

        import numpy as np
        try:
            with open(self._chunks_path(), encoding="utf-8") as f:
                self._chunks = json.load(f)
            self._vectors = np.load(self._vectors_path())
            return True
        except Exception as e:
            logger.warning("ベクトルインデックスのロード失敗: %s", e)
            return False

    def search(
        self,
        query_vector: list[float],
        top_k: int = 3,
        filter_school_type: str | None = None,
        filter_source_type: str | None = None,
    ) -> list[dict]:
        """コサイン類似度でtop_k件のチャンクを返す。

        Args:
            query_vector: クエリのエンベディングベクトル
            top_k: 返すチャンク数
            filter_school_type: 指定した場合、そのschool_typeまたは'共通'のみ検索
            filter_source_type: 指定した場合、そのsource_typeのみ検索（"current" or "future"）

        Returns:
            スコア降順のチャンクリスト。各チャンクに "score" キーが追加される。
            インデックス未構築の場合は []。
        """
        import numpy as np

        if not self._load():
            return []

        # フィルタリング
        indices = []
        for i, chunk in enumerate(self._chunks):
            if filter_school_type and chunk.get("school_type") not in (filter_school_type, "共通"):
                continue
            if filter_source_type and chunk.get("source_type") != filter_source_type:
                continue
            indices.append(i)

        if not indices:
            return []

        # コサイン類似度計算
        qv = np.array(query_vector, dtype=np.float32)
        qv_norm = np.linalg.norm(qv)
        if qv_norm == 0:
            return []
        qv = qv / qv_norm

        filtered_vecs = self._vectors[indices]  # (M, D)
        norms = np.linalg.norm(filtered_vecs, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1.0, norms)
        normed = filtered_vecs / norms
        scores = normed @ qv  # (M,)

        # source_type による重みとキーワードボーナスの適用
        for j, idx in enumerate(indices):
            chunk = self._chunks[idx]
            if chunk.get("source_type") == "future":
                bonus = any(kw in chunk.get("text", "") for kw in FUTURE_BONUS_KEYWORDS)
                weight = 1.0 if bonus else FUTURE_SCORE_WEIGHT
                scores[j] *= weight

        # top_k を取得
        k = min(top_k, len(indices))
        top_j = int(np.argpartition(scores, -k)[-k:][0]) if k == 1 else None
        top_js = np.argpartition(scores, -k)[-k:]
        top_js_sorted = top_js[np.argsort(scores[top_js])[::-1]]

        results = []
        for j in top_js_sorted:
            chunk = dict(self._chunks[indices[j]])
            chunk["score"] = float(scores[j])
            results.append(chunk)

        return results
