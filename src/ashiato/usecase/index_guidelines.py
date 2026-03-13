"""
あしあとプロジェクト - ガイドラインインデックス構築CLI

使い方:
    python -m ashiato.usecase.index_guidelines
    python -m ashiato.usecase.index_guidelines --guidelines-dir guidelines --index-dir guidelines_index

処理フロー:
    1. guidelines/current/ と guidelines/future/ 以下のPDF全件を走査
    2. extract_chunks() でページ分割 + チャンキング（source_typeメタデータ付き）
    3. Ollamaの nomic-embed-text でベクトル化
    4. VectorStore.build() でインデックスを guidelines_index/ に保存

前提条件:
    - pip install pypdf numpy
    - ollama pull nomic-embed-text
    - Ollamaが起動していること
"""

import argparse
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def _find_project_root() -> Path:
    """config.py からプロジェクトルートを特定する。"""
    try:
        from ashiato.config import GUIDELINES_DIR, GUIDELINES_INDEX_DIR
        # config.py は src/ashiato/ にある → プロジェクトルートは3階層上
        config_path = Path(__file__).parent.parent.parent.parent
        return config_path.resolve()
    except ImportError:
        return Path.cwd()


def run(guidelines_dir: Path, index_dir: Path) -> int:
    """インデックスを構築する。成功時は0、失敗時は1を返す。"""
    from ashiato.infra.embeddings import embed
    from ashiato.infra.pdf_reader import collect_pdfs, extract_chunks
    from ashiato.infra.vector_store import VectorStore

    # PDF収集
    pdfs = collect_pdfs(guidelines_dir)
    if not pdfs:
        logger.error("PDFが見つかりません: %s", guidelines_dir)
        return 1

    logger.info("ガイドラインPDF: %d件 を処理します", len(pdfs))

    # チャンク抽出
    all_chunks: list[dict] = []
    for pdf_path in pdfs:
        chunks = extract_chunks(pdf_path, guidelines_dir)
        if chunks:
            logger.info("  %s: %d チャンク抽出", pdf_path.name, len(chunks))
            all_chunks.extend(chunks)
        else:
            logger.warning("  %s: チャンク抽出なし（スキップ）", pdf_path.name)

    if not all_chunks:
        logger.error("有効なチャンクが1件も抽出できませんでした")
        return 1

    logger.info("合計 %d チャンクをベクトル化します...", len(all_chunks))

    # エンベディング
    vectors: list[list[float]] = []
    failed = 0
    valid_chunks: list[dict] = []

    for i, chunk in enumerate(all_chunks, start=1):
        if i % 50 == 0 or i == len(all_chunks):
            logger.info("  エンベディング: %d / %d", i, len(all_chunks))
        vec = embed(chunk["text"])
        if vec is None:
            logger.debug("  エンベディング失敗（スキップ）: %s p.%s", chunk["source"], chunk["page"])
            failed += 1
            continue
        vectors.append(vec)
        valid_chunks.append(chunk)

    if not valid_chunks:
        logger.error("エンベディング成功チャンクが0件です。Ollamaとモデルを確認してください。")
        logger.error("  確認コマンド: ollama list  / ollama pull nomic-embed-text")
        return 1

    if failed > 0:
        logger.warning("エンベディング失敗: %d チャンク（成功: %d チャンク）", failed, len(valid_chunks))

    # インデックス保存
    store = VectorStore(index_dir)
    store.build(valid_chunks, vectors)

    # 統計サマリー
    current_count = sum(1 for c in valid_chunks if c.get("source_type") == "current")
    future_count = sum(1 for c in valid_chunks if c.get("source_type") == "future")
    logger.info("インデックス構築完了:")
    logger.info("  current（学習指導要領）: %d チャンク", current_count)
    logger.info("  future（教育改革資料）:  %d チャンク", future_count)
    logger.info("  合計: %d チャンク → %s", len(valid_chunks), index_dir)
    logger.info("")
    logger.info("有効化するには .env に以下を追記してください:")
    logger.info("  ASHIATO_GUIDELINES_ENABLED=true")

    return 0


def main() -> None:
    project_root = _find_project_root()

    from ashiato.config import GUIDELINES_DIR, GUIDELINES_INDEX_DIR
    default_guidelines_dir = project_root / GUIDELINES_DIR
    default_index_dir = project_root / GUIDELINES_INDEX_DIR

    parser = argparse.ArgumentParser(
        description="ガイドラインPDFをベクトル化してインデックスを構築する",
    )
    parser.add_argument(
        "--guidelines-dir",
        type=Path,
        default=default_guidelines_dir,
        help=f"ガイドラインPDFのルートディレクトリ（デフォルト: {default_guidelines_dir}）",
    )
    parser.add_argument(
        "--index-dir",
        type=Path,
        default=default_index_dir,
        help=f"インデックス保存先ディレクトリ（デフォルト: {default_index_dir}）",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    sys.exit(run(args.guidelines_dir, args.index_dir))


if __name__ == "__main__":
    main()
