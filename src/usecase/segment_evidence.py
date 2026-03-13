#!/usr/bin/env python3
"""
あしあとプロジェクト - Stage1 切片化スクリプト
入力: mapped.csv（話者マッピング済みCSV）
出力: evidence_YYYYMMDD.json
"""

import json
import logging
import argparse
import sys
from datetime import datetime
from pathlib import Path

from config import MAX_SESSIONS
from domain.viewpoints import VIEWPOINTS
from infra.llm import call_ollama
from infra.csv_reader import load_csv

logger = logging.getLogger(__name__)

EVIDENCE_SCHEMA = {
    "type": "object",
    "properties": {vp: {"type": "array", "items": {"type": "string"}} for vp in VIEWPOINTS},
    "required": VIEWPOINTS,
}


def load_meta_txt(path: str) -> dict:
    """map_speakers.py が生成する _meta.txt を読み込んで session_info dict に変換"""
    meta: dict = {}
    key_map = {"活動日": "date", "場所": "location", "活動内容": "activity", "学校種別": "school_type"}
    with open(path, encoding="utf-8") as f:
        for line in f:
            for jp, en in key_map.items():
                if line.startswith(jp + ": "):
                    meta[en] = line[len(jp) + 2:].strip()
    return meta


def get_children(rows: list[dict], supporter: str) -> list[str]:
    """支援者以外の話者を児童として返す"""
    speakers = set(r["speaker"] for r in rows)
    excludes = {supporter, "全員", "不明", "[不明]", ""}
    return sorted(s for s in speakers if s not in excludes and not s.startswith("["))


def build_transcript_per_child(rows: list[dict], child: str) -> str:
    """児童の発言と直前の支援者発言をセットで抽出（文脈つき）"""
    lines = []
    prev_supporter_line = ""
    for row in rows:
        if row["speaker"] not in ("全員", "") and not row["speaker"].startswith("["):
            if row["speaker"] != child:
                prev_supporter_line = f"  支援者: {row['text']}"
            else:
                if prev_supporter_line:
                    lines.append(prev_supporter_line)
                    prev_supporter_line = ""
                lines.append(f"  {child}: {row['text']}")
    return "\n".join(lines)


def build_full_transcript(rows: list[dict]) -> str:
    lines = []
    for row in rows:
        if row["text"] and row["text"] != "[聞き取り不明]":
            lines.append(f"[{row['start']}] {row['speaker']}: {row['text']}")
    return "\n".join(lines)


def extract_evidence_per_viewpoint(child: str, transcript: str, session_info: dict | None = None) -> dict[str, list[str]]:
    """Stage 1 - 切片化: 発言記録を観点別に分類し、根拠発言をそのまま抜き出す"""
    school_type = (session_info or {}).get("school_type", "小学校")

    system = (
        f"あなたは{school_type}指導要録の観点別学習評価を専門とする教育記録アナリストである。"
        f"この記録は{school_type}への出席扱い申請に使用される公的文書であり、"
        "発言の根拠が審査されるため正確性が厳格に求められる。"
        "発言テキストは一字一句変えずに記録し、推測・補完・解釈は絶対に行わない。"
    )

    prompt = f"""# 前提条件（Context）
本作業の目的: NPO法人姫路YMCAが運営するフリースクール「あしあと」の太子遊び冒険の森ASOBO（兵庫県揖保郡太子町の里山）での体験セッションにおける{child}さんの発言記録から、
{school_type}の指導要録（出席扱い申請用）に記載する観点別学習状況の根拠発言を特定・分類すること。

# 観点の定義（Task: 里山体験における解釈）
■ 知識・技能
→ 自然の生き物・植物・現象についての新しい知識、または道具・技能の習得・実践を示す発言
【該当例】「カブトムシはクヌギの木の汁を吸うんだって！」「火起こし、できた！」
【非該当例】「楽しかった」「やってみたい」（感想・意欲のみで知識・技能の内容がない）

■ 思考・判断・表現
→ 自然現象への疑問・気づき・仮説・比較・判断を言語化した発言
【該当例】「なんでこっちの葉っぱだけ虫に食われてるんだろう」「こっちの方が火がつきやすいと思う」
【非該当例】「すごい！」（驚きのみで考察・判断の内容がない）

■ 主体的に学習に取り組む態度
→ 自発的挑戦・継続意欲・困難への粘り強さ・他者との協力意識を示す発言
【該当例】「もう1回やってみる」「次は自分でやりたい」「〇〇くんも一緒にやろう」
【非該当例】支援者に促されて行動した旨のみが記録されている発言

# 境界例と判断推論（グレーゾーン対応）
複数の観点に当てはまりそうな発言は、以下の思考チェーンで分類する。

【境界例1】「やった！火起こしできた！これ難しかったんだよな」
→ 感想だけか？ → No（「できた」という技能達成を含む）
→ 知識・技能か？ → Yes（火起こし技能の習得・成功を示す）
→ 主体的に学習か？ → 意欲も感じるが技能習得が主軸
→ 結論: 知識・技能 に分類

【境界例2】「もう1回やってみる！絶対できるようになりたい」
→ 技能の内容か？ → No（具体的な技能内容の言及なし）
→ 思考・判断か？ → No（疑問・考察でなく意欲の表明）
→ 主体的に学習か？ → Yes（継続意欲・再挑戦の意志を明言）
→ 結論: 主体的に学習に取り組む態度 に分類

【迷った場合の判断優先順位（tiebreaker）】
具体的な技能・知識の内容を示す → 知識・技能
疑問・気づき・判断を言語化している → 思考・判断・表現
継続意欲・挑戦・協力の意志を示す → 主体的に学習に取り組む態度

# {child}さんの発言記録（支援者とのやりとり含む）
{transcript}

# 思考ステップ（この順で実行すること）
1. 発言記録をすべて通読する
2. 各発言を上記3観点の定義・具体例と照らし合わせて評価する
3. 最も当てはまる観点に1つのみ分類する（どれにも該当しない場合は除外）
4. 下記の制約ルールに違反していないか確認する
5. JSON形式で出力する

# 制約ルール（すべて厳守する）
1. 発言テキストは一字一句そのまま記録する（要約・編集・補完・解釈禁止）
2. 発言の意図・理由が記録内で明言されていない場合は推測で補完しない
3. 1つの発言は最も当てはまる観点1つにのみ分類する（重複分類禁止）
4. 4語以下の短い相槌・返事（「うん」「そう」「え」「はい」「わかった」等）は除外する
5. どの観点にも該当しない発言は除外する
6. 該当発言が0件の観点は空配列にする

# 出力形式（このJSONのみを出力すること。前置き・後書き不要）
{{
  "知識・技能": ["該当する発言をそのまま記載", "..."],
  "思考・判断・表現": ["該当する発言をそのまま記載", "..."],
  "主体的に学習に取り組む態度": ["該当する発言をそのまま記載", "..."]
}}
"""
    raw = call_ollama(prompt, system=system, num_predict=-1, format=EVIDENCE_SCHEMA,
                      extra_options={"temperature": 0.1})
    try:
        parsed = json.loads(raw)
        return {v: [u for u in parsed.get(v, []) if u] for v in VIEWPOINTS}
    except (json.JSONDecodeError, AttributeError) as e:
        logger.error("Stage1 JSONパース失敗（%s）: %s", child, e)
        logger.error("  Ollamaの出力（先頭200文字）: %r", raw[:200])
        logger.error("  対処方法: Ollamaのバージョンを確認してください（0.3.0以上必要）。")
        raise RuntimeError(f"Stage1 JSONパース失敗（{child}）") from e


def _pick_representative_utterances(rows: list[dict], children: list[str], n: int = 2) -> str:
    """各児童の代表的な発言をn件ずつ抽出してテキスト化"""
    lines = []
    for child in children:
        utterances = [r["text"] for r in rows if r["speaker"] == child and r.get("text") and r["text"] != "[聞き取り不明]"]
        candidates = [u for u in utterances if len(u) >= 8]
        if not candidates:
            candidates = utterances
        step = max(1, len(candidates) // (n + 1))
        seen: set[str] = set()
        picks: list[str] = []
        for i in range(1, n + 1):
            u = candidates[min(i * step, len(candidates) - 1)]
            if u not in seen:
                seen.add(u)
                picks.append(u)
        for p in picks:
            lines.append(f"  {child}:「{p}」")
    return "\n".join(lines)


def save_evidence_json(
    evidence_by_child: dict[str, dict[str, list[str]]],
    session_info: dict,
    supporter: str,
    children: list[str],
    rows: list[dict],
    path: str,
) -> None:
    child_counts = {child: sum(1 for r in rows if r["speaker"] == child) for child in children}
    utterances_sample = _pick_representative_utterances(rows, children, n=4)
    data = {
        "schema_version": 1,
        "session_info": session_info,
        "supporter": supporter,
        "children": children,
        "child_counts": child_counts,
        "utterances_sample": utterances_sample,
        "evidence": evidence_by_child,
    }
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_evidence_json(path: str) -> tuple[dict, str, list[str], dict, str, dict[str, dict[str, list[str]]]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return (
        data["session_info"],
        data["supporter"],
        data["children"],
        data.get("child_counts", {}),
        data.get("utterances_sample", ""),
        data["evidence"],
    )


def _run_stage1(rows: list[dict], children: list[str], session_info: dict, supporter: str, date_slug: str) -> tuple[str, dict[str, dict[str, list[str]]]]:
    """切片化を実行してevidence.jsonに保存し、パスとevidence dictを返す"""
    evidence_by_child: dict[str, dict[str, list[str]]] = {}

    for i, child in enumerate(children, 1):
        child_count = sum(1 for r in rows if r["speaker"] == child)
        if child_count == 0:
            evidence_by_child[child] = {v: [] for v in VIEWPOINTS}
            continue

        transcript = build_transcript_per_child(rows, child)
        print(f"\n📋 [{i}/{len(children)}] {child}（{child_count}発言）: Stage1 切片化中...")
        evidence = extract_evidence_per_viewpoint(child, transcript, session_info)
        evidence_by_child[child] = evidence

        print(f"  ┌─ 切片化結果（確認してください）")
        for v in VIEWPOINTS:
            utterances = evidence[v]
            print(f"  │ 【{v}】{len(utterances)}件")
            for u in utterances:
                print(f"  │   ・{u}")
        print(f"  └─────────────────")

    evidence_path = f"evidence_{date_slug}.json"
    save_evidence_json(evidence_by_child, session_info, supporter, children, rows, evidence_path)
    print(f"\n💾 切片化結果を保存しました: {evidence_path}", file=sys.stderr)
    print(f"   → 内容を確認・修正後、Stage2を実行してください:", file=sys.stderr)
    print(f"   PYTHONPATH=src python3 src/usecase/generate_report.py --evidence {evidence_path}", file=sys.stderr)
    return evidence_path, evidence_by_child


def main() -> None:
    parser = argparse.ArgumentParser(
        description="あしあと Stage1 切片化",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
実行例:
  PYTHONPATH=src python3 src/usecase/segment_evidence.py mapped.csv
  PYTHONPATH=src python3 src/usecase/segment_evidence.py mapped.csv --supporter 山田
""",
    )
    parser.add_argument("csv", help="入力CSVファイル（mapped_*.csv）")
    parser.add_argument("--date", default=datetime.today().strftime("%Y年%m月%d日"), help="セッション日付")
    parser.add_argument("--location", default="太子遊び冒険の森ASOBO（兵庫県揖保郡太子町）", help="活動場所")
    parser.add_argument("--activity", default="自然観察・昼食調理・火起こし体験", help="活動内容")
    parser.add_argument("--supporter", default="山田", help="支援者名（除外用）")
    parser.add_argument("--school-type", choices=["小学校", "中学校"], default="小学校", help="学校種別")
    args = parser.parse_args()

    date_slug = datetime.today().strftime("%Y%m%d")

    meta_path = str(Path(args.csv).with_suffix("")) + "_meta.txt"
    meta_from_file: dict = {}
    if Path(meta_path).exists():
        meta_from_file = load_meta_txt(meta_path)
        print(f"📋 メタ情報を読み込みました: {meta_path}")

    default_date        = datetime.today().strftime("%Y年%m月%d日")
    default_location    = "太子遊び冒険の森ASOBO（兵庫県揖保郡太子町）"
    default_activity    = "自然観察・昼食調理・火起こし体験"
    default_school_type = "小学校"

    session_info = {
        "date":        args.date        if args.date        != default_date        else meta_from_file.get("date",        default_date),
        "location":    args.location    if args.location    != default_location    else meta_from_file.get("location",    default_location),
        "activity":    args.activity    if args.activity    != default_activity    else meta_from_file.get("activity",    default_activity),
        "school_type": args.school_type if args.school_type != default_school_type else meta_from_file.get("school_type", default_school_type),
    }

    print(f"📂 読み込み中: {args.csv}")
    rows = load_csv(args.csv)
    children = get_children(rows, args.supporter)
    print(f"👶 検出された児童: {', '.join(children)}")
    print(f"📊 総発言数: {len(rows)}件\n")

    evidence_path, _ = _run_stage1(rows, children, session_info, args.supporter, date_slug)
    # 機械可読なパスを stdout に出力（シェルスクリプトが $(…) でキャプチャできる）
    print(evidence_path)


if __name__ == "__main__":
    main()
