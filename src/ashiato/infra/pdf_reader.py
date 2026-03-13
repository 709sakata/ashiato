"""
あしあとプロジェクト - PDFテキスト抽出 + チャンキング

extract_chunks(): ガイドラインPDFからテキストを抽出し、メタデータ付きのチャンクリストを返す。
"""

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# 1チャンクあたりの目安文字数（日本語は文字数ベース）
CHUNK_SIZE = 400


def _infer_metadata(pdf_path: Path, guidelines_root: Path) -> dict:
    """ファイルパスからschool_type / source_type / subjectを推定する。"""
    try:
        rel = pdf_path.relative_to(guidelines_root)
    except ValueError:
        rel = pdf_path

    parts = rel.parts  # 例: ("current", "JuniorHigh", "中学校学習指導要領...国語編.pdf")

    # source_type
    if parts[0] == "current":
        source_type = "current"
    elif parts[0] == "future":
        source_type = "future"
    else:
        source_type = "current"

    # school_type
    if source_type == "future":
        school_type = "共通"
    elif len(parts) > 1 and parts[1] == "elementary":
        school_type = "小学校"
    elif len(parts) > 1 and parts[1] == "JuniorHigh":
        school_type = "中学校"
    else:
        school_type = "共通"

    # subject（ファイル名から推定）
    name = pdf_path.stem
    subject_patterns = [
        ("国語", "国語"),
        ("数学", "数学"),
        ("算数", "算数"),
        ("理科", "理科"),
        ("社会", "社会"),
        ("英語", "外国語"),
        ("外国語", "外国語"),
        ("体育", "体育"),
        ("保健体育", "保健体育"),
        ("音楽", "音楽"),
        ("図画工作", "図画工作"),
        ("美術", "美術"),
        ("家庭", "家庭"),
        ("技術", "技術"),
        ("道徳", "道徳"),
        ("特別活動", "特別活動"),
        ("総合的な学習", "総合的な学習"),
        ("総則", "総則"),
        ("情報", "情報"),
    ]
    subject = "全般"
    for pattern, label in subject_patterns:
        if pattern in name:
            subject = label
            break

    return {
        "source": str(rel),
        "school_type": school_type,
        "source_type": source_type,
        "subject": subject,
    }


def _split_text(text: str, chunk_size: int = CHUNK_SIZE) -> list[str]:
    """テキストを句点・改行を優先してchunk_size文字前後に分割する。"""
    # 余分な空白・改行を正規化
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text).strip()

    if not text:
        return []

    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        if end >= len(text):
            chunk = text[start:].strip()
            if chunk:
                chunks.append(chunk)
            break

        # 句点「。」や改行で区切れる位置を探す（chunk_sizeを超えない範囲）
        best_cut = -1
        for cut_char in ("。\n", "。", "\n\n", "\n"):
            idx = text.rfind(cut_char[0], start + chunk_size // 2, end)
            if idx != -1:
                best_cut = idx + 1
                break

        if best_cut == -1:
            best_cut = end

        chunk = text[start:best_cut].strip()
        if chunk:
            chunks.append(chunk)
        start = best_cut

    return chunks


def extract_chunks(pdf_path: Path, guidelines_root: Path) -> list[dict]:
    """PDFからテキストを抽出し、メタデータ付きのチャンクリストを返す。

    Args:
        pdf_path: 対象のPDFファイルパス
        guidelines_root: guidelinesディレクトリのルートパス（メタデータ推定に使用）

    Returns:
        [{"text": str, "source": str, "school_type": str,
          "source_type": str, "subject": str, "page": int}, ...]
        抽出失敗・テキストなしの場合は []
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        logger.error("pypdf がインストールされていません。pip install pypdf を実行してください。")
        return []

    metadata = _infer_metadata(pdf_path, guidelines_root)
    chunks = []

    try:
        reader = PdfReader(str(pdf_path))
        for page_num, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception as e:
                logger.debug("ページ %d のテキスト抽出失敗（%s）: %s", page_num, pdf_path.name, e)
                continue

            for chunk_text in _split_text(text):
                if len(chunk_text) < 20:
                    continue
                chunks.append({
                    **metadata,
                    "text": chunk_text,
                    "page": page_num,
                })
    except Exception as e:
        logger.warning("PDF読み込み失敗（%s）: %s", pdf_path.name, e)
        return []

    return chunks


def collect_pdfs(guidelines_root: Path) -> list[Path]:
    """guidelinesルートから current/ と future/ 以下のPDF全件を収集する。"""
    pdfs = []
    for subdir in ("current", "future"):
        target = guidelines_root / subdir
        if target.exists():
            pdfs.extend(sorted(target.rglob("*.pdf")))
    return pdfs
