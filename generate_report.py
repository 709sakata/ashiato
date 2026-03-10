#!/usr/bin/env python3
"""
あしあとプロジェクト - 報告書生成スクリプト
入力: CSVファイル（start, end, speaker, text）
出力: 校長向け報告書 Markdown
"""

import csv
import sys
import json
import argparse
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "qwen2.5:7b"

# ===== 観点別学習状況の観点（小学校学習指導要領準拠） =====
VIEWPOINTS = [
    "知識・技能",
    "思考・判断・表現",
    "主体的に学習に取り組む態度",
]

def load_meta_txt(path: str) -> dict:
    """map_speakers.py が生成する _meta.txt を読み込んで session_info dict に変換"""
    meta: dict = {}
    key_map = {"活動日": "date", "場所": "location", "活動内容": "activity"}
    with open(path, encoding="utf-8") as f:
        for line in f:
            for jp, en in key_map.items():
                if line.startswith(jp + ": "):
                    meta[en] = line[len(jp) + 2:].strip()
    return meta


def load_csv(path: str) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows

def get_children(rows: list[dict], supporter: str) -> list[str]:
    """支援者以外の話者を児童として返す"""
    speakers = set(r["speaker"] for r in rows)
    # 「全員」「不明」等を除外
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

def call_ollama(prompt: str, system: str = "", num_predict: int = -1) -> str:
    body: dict = {
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": num_predict}
    }
    if system:
        body["system"] = system
    payload = json.dumps(body).encode("utf-8")

    req = urllib.request.Request(
        OLLAMA_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result.get("response", "").strip()
    except urllib.error.URLError as e:
        print(f"[ERROR] Ollama接続失敗: {e}", file=sys.stderr)
        sys.exit(1)

def normalize_child_report(child: str, raw: str) -> str:
    """LLMの出力を強制的に ## 児童名 / ### 観点 形式に正規化する"""
    import re

    # 児童名見出しを ## に統一（#, ###, ####等すべて）
    raw = re.sub(r'^#{1,6}\s*' + re.escape(child) + r'\s*$', f'## {child}', raw, flags=re.MULTILINE)

    # 観点見出しを ### に統一
    for viewpoint in ["知識・技能", "思考・判断・表現", "主体的に学習に取り組む態度"]:
        raw = re.sub(r'^#{1,6}\s*' + re.escape(viewpoint) + r'\s*$', f'### {viewpoint}', raw, flags=re.MULTILINE)

    # 先頭に ## 児童名 がなければ追加
    if not re.search(r'^## ' + re.escape(child), raw, flags=re.MULTILINE):
        raw = f'## {child}\n\n' + raw

    # 「彼女」「彼」を児童名に置換（助詞の前のみ、他の語の一部は除外）
    raw = re.sub(r'彼女(?=[はがのをにへもと])', child, raw)
    raw = re.sub(r'彼(?=[はがのをにへもと])', child, raw)

    return raw.strip()

def _parse_evidence(raw: str) -> dict[str, list[str]]:
    """切片化LLM出力をパースして観点別発言リストに変換"""
    import re
    result: dict[str, list[str]] = {v: [] for v in VIEWPOINTS}
    current = None
    for line in raw.splitlines():
        stripped = line.strip()
        for v in VIEWPOINTS:
            if re.match(r'^#{1,4}\s*' + re.escape(v), stripped):
                current = v
                break
        else:
            if current and stripped.startswith("- "):
                text = stripped[2:].strip().strip("「」")
                if text and text not in ("なし", "（根拠発言なし）"):
                    result[current].append(text)
    return result


def extract_evidence_per_viewpoint(child: str, transcript: str) -> dict[str, list[str]]:
    """Stage 1 - 切片化: 発言記録を観点別に分類し、根拠発言をそのまま抜き出す"""
    system = (
        "あなたは優秀なリサーチャーである。"
        "与えられた発言記録から根拠発言を切り出し、指定された観点ごとに整理してほしい。"
        "発言録が入力されるまで待機してほしい。"
    )

    prompt = f"""# 前提条件
本コンテンツは、ASOBO里山体験セッションにおける{child}さんの発言記録をもとに、
小学校の指導要録（出席扱い申請用）に記載する観点別学習状況の根拠発言を特定することが目的である。

# 観点の定義（里山体験における解釈）
■ 知識・技能
→「この発言は、自然の生き物・植物・現象について新しい知識を得たことを示しているか？
   または、道具の使い方・技能（火起こし、調理、虫の捕まえ方等）を習得・実践したことを示しているか？」

■ 思考・判断・表現
→「この発言は、自然現象への疑問・気づきを言葉にしているか？
   観察した結果を仮説・比較・感想として表現しているか？自分の判断を述べているか？」

■ 主体的に学習に取り組む態度
→「この発言は、自ら進んで挑戦しようとする意欲を示しているか？
   困難に粘り強く取り組む姿・次回への意欲・他者との協力意識を示しているか？」

# {child}さんの発言記録（支援者とのやりとり含む）
{transcript}

# 目的を達成するためのステップ
1. 発言記録を1件ずつ順番に読む
2. 各発言を上記3観点の定義と照らし合わせる
3. 該当する観点に発言テキストをそのまま記載する
4. 1つの発言が複数の観点に該当する場合は、それぞれに記載する（複数所属OK）
5. どの観点にも該当しない発言は除外する
6. 全件の処理が完了したら成果物を出力する

# 実行プロセスのルール（すべて厳守する）
- 発言テキストは一字一句そのまま記録する（要約・編集・補完・解釈禁止）
- 発言の意図・理由が記録内で明言されていない場合は推測で補完しない
- 「わからない」「おそらく」などのニュアンスが含まれる発言はそのまま記録する
- 該当発言が0件の観点は「なし」と記載する
- 前置きや後書きは出力しない

# 成果物のフォーマット（この通りに出力すること）
### 知識・技能
- 「（発言テキスト）」

### 思考・判断・表現
- 「（発言テキスト）」

### 主体的に学習に取り組む態度
- 「（発言テキスト）」
"""
    raw = call_ollama(prompt, system=system, num_predict=-1)
    return _parse_evidence(raw)


def generate_child_report(child: str, evidence: dict[str, list[str]], session_info: dict) -> str:
    """Stage 2 - 記述生成: 切片化済みの根拠発言リストをもとに観点別記述を作成"""
    system = (
        "あなたは優秀なリサーチャーである。"
        "与えられた根拠発言リスト（切片化済み）をもとに、指導要録の観点別学習状況の記述を作成してほしい。"
        "根拠発言リストが入力されるまで待機してほしい。"
    )

    # 観点別根拠発言テキストを構築（件数を明示）
    evidence_parts = []
    for v in VIEWPOINTS:
        utterances = evidence[v]
        evidence_parts.append(f"### {v}（{len(utterances)}件）")
        if utterances:
            for u in utterances:
                evidence_parts.append(f"- 「{u}」")
        else:
            evidence_parts.append("- （根拠発言なし）")
    evidence_text = "\n".join(evidence_parts)

    prompt = f"""# 前提条件
本コンテンツは、ASOBO里山体験セッションにおける{child}さんの根拠発言リスト（Stage1切片化済み）をもとに、
学校への出席扱い申請用の指導要録「観点別学習状況」の記述を作成することが目的である。

# 変数の定義
- 根拠発言: Stage1で発言記録から切り出した、各観点に該当する発言テキスト（原文そのまま）
- 観点別記述: 根拠発言をもとに記述する、指導要録に載せる2〜3文の観察文

# セッション情報
日付: {session_info['date']}
活動場所: {session_info['location']}
活動内容: {session_info['activity']}

# 観点別根拠発言リスト（これのみを使用すること）
{evidence_text}

# 目的を達成するためのステップ
1. 各観点の根拠発言リストを読み込む
2. 根拠発言から読み取れる事実（何をしたか・何と発言したか）を整理する
3. 各観点について2〜3文の記述を作成する
   - 1文目: 根拠発言に基づく具体的な行動・発言の事実（「〜と発言した」「〜に取り組んだ」）
   - 2文目: その事実から観察できること（「〜する様子が見られた」「〜が認められた」）
   - 3文目（任意）: 追加の根拠発言がある場合、補足として記述
4. 根拠発言が「なし」の観点は「本セッションの記録からは確認できなかった」と記述する
5. 全観点の記述が完了したら成果物を出力する

# 実行プロセスのルール（すべて厳守する）
- 根拠発言リストに存在しない事実は絶対に記述しない（創作・推測・補完禁止）
- 人称は「{child}は」で統一し、「彼は」「彼女は」は使わない
- 支援者が実際に観察した事実として書く（「〜した」「〜と発言した」「〜と述べた」等）
- 体言止めや箇条書きは使わず、文章で書く
- 各観点の末尾に（根拠発言数: N件）を括弧書きで添える。Nは根拠発言リストの括弧内の件数をそのまま記載する
- 文字起こし誤りと思われる語は文脈から正しい語に解釈して記述し、誤変換をそのまま記述しない
- 前置きや後書きは出力しない

# 成果物のフォーマット（この通りに出力すること）

## {child}

### 知識・技能
（ここに記述）

### 思考・判断・表現
（ここに記述）

### 主体的に学習に取り組む態度
（ここに記述）
"""
    return normalize_child_report(child, call_ollama(prompt, system=system, num_predict=2000))

def _pick_representative_utterances(rows: list[dict], children: list[str], n: int = 2) -> str:
    """各児童の代表的な発言をn件ずつ抽出してテキスト化"""
    lines = []
    for child in children:
        utterances = [r["text"] for r in rows if r["speaker"] == child and r.get("text") and r["text"] != "[聞き取り不明]"]
        # 短すぎる発言を除き、中盤から代表発言を選ぶ
        candidates = [u for u in utterances if len(u) >= 8]
        if not candidates:
            candidates = utterances
        # 均等にn件取る（先頭・末尾に偏らないよう中央付近から）、重複除去
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

def generate_session_summary(
    children: list[str],
    session_info: dict,
    *,
    rows: list[dict] | None = None,
    child_counts: dict[str, int] | None = None,
    utterances_sample: str | None = None,
) -> str:
    """セッション全体サマリーを生成。rows か (child_counts + utterances_sample) のどちらかが必要"""
    if child_counts is None:
        child_counts = {child: sum(1 for r in rows if r["speaker"] == child) for child in children}
    if utterances_sample is None:
        utterances_sample = _pick_representative_utterances(rows, children, n=4)
    stats_text = "\n".join(f"  {k}: {v}件" for k, v in child_counts.items())
    representative = utterances_sample

    system = (
        "あなたは支援者の観察記録を整理する記録補助者です。"
        "与えられた【各児童の発言抜粋】に存在する事実のみを根拠に記述します。"
        "記録にない活動・様子・発言の創作・推測・補完は絶対に行いません。"
        "この記録は学校への出席扱い申請に使用される公式文書の下書きです。"
    )

    prompt = f"""以下は、不登校支援活動「ASOBO」の里山体験セッションの記録です。

【セッション情報】
日付: {session_info['date']}
活動場所: {session_info['location']}
活動内容: {session_info['activity']}
参加児童: {', '.join(children)}（{len(children)}名）

【児童別発言件数】
{stats_text}

【各児童の発言抜粋（これが根拠となる唯一の記録）】
{representative}

【指示】
校長先生への報告書の冒頭に載せる「セッション総括」を200字程度で書いてください。
・【各児童の発言抜粋】に記載された内容のみを根拠に活動を描写すること（記録にない活動・様子の創作禁止）
・活動内容（{session_info['activity']}）の範囲内で簡潔に述べること
・参加した全児童（{', '.join(children)}）に均等に言及し、特定の2名だけを目立たせないこと
・「不登校」という言葉は使わず「学校外での学びの場」として表現
・支援者が観察した事実として書くこと
・前置きや後書きは不要、総括の文章のみ出力すること
"""
    return call_ollama(prompt, system=system, num_predict=500)

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


def _run_stage1(rows: list[dict], children: list[str], session_info: dict, supporter: str, date_slug: str) -> tuple[str, dict]:
    """切片化を実行してevidence.jsonに保存し、パスとevidence dictを返す"""
    evidence_by_child: dict[str, dict[str, list[str]]] = {}

    for i, child in enumerate(children, 1):
        child_count = sum(1 for r in rows if r["speaker"] == child)
        if child_count == 0:
            evidence_by_child[child] = {v: [] for v in VIEWPOINTS}
            continue

        transcript = build_transcript_per_child(rows, child)
        print(f"\n📋 [{i}/{len(children)}] {child}（{child_count}発言）: Stage1 切片化中...")
        evidence = extract_evidence_per_viewpoint(child, transcript)
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
    print(f"\n💾 切片化結果を保存しました: {evidence_path}")
    print("   → 内容を確認・修正後、Stage2を実行してください:")
    print(f"   python3 generate_report.py --stage 2 --evidence {evidence_path}")
    return evidence_path, evidence_by_child


def _run_stage2(
    evidence_by_child: dict[str, dict[str, list[str]]],
    children: list[str],
    session_info: dict,
    supporter: str,
    output_path: str,
    *,
    rows: list[dict] | None = None,
    child_counts: dict[str, int] | None = None,
    utterances_sample: str | None = None,
) -> None:
    """evidence dictから報告書Markdownを生成して書き出す"""
    lines = []
    lines.append(f"# ASOBO 活動報告書（校長向け）")
    lines.append(f"")
    lines.append(f"**日付**: {session_info['date']}  ")
    lines.append(f"**場所**: {session_info['location']}  ")
    lines.append(f"**活動**: {session_info['activity']}  ")
    lines.append(f"**参加児童**: {', '.join(children)}（{len(children)}名）  ")
    lines.append(f"**支援者**: {supporter}  ")
    lines.append(f"**記録生成日**: {datetime.today().strftime('%Y年%m月%d日')}  ")
    lines.append(f"")
    lines.append(f"---")
    lines.append(f"")

    print("📝 セッション総括を生成中...")
    summary = generate_session_summary(
        children, session_info,
        rows=rows, child_counts=child_counts, utterances_sample=utterances_sample,
    )
    lines.append(f"## セッション総括")
    lines.append(f"")
    lines.append(summary)
    lines.append(f"")
    lines.append(f"---")
    lines.append(f"")

    lines.append(f"## 観点別学習状況（児童別）")
    lines.append(f"")

    for i, child in enumerate(children, 1):
        evidence = evidence_by_child.get(child, {v: [] for v in VIEWPOINTS})
        # evidence_count は切片化後の証拠件数。CSVの発言件数とは異なる点に注意
        evidence_count = sum(len(evidence[v]) for v in VIEWPOINTS)

        if evidence_count == 0:
            lines.append(f"### {child}")
            lines.append(f"※ 本セッションの記録から観点別の根拠発言を確認できなかった")
            lines.append(f"")
            continue

        print(f"✍️  [{i}/{len(children)}] {child}: Stage2 記述生成中...")
        report = generate_child_report(child, evidence, session_info)
        lines.append(report)
        lines.append(f"")
        lines.append(f"---")
        lines.append(f"")

    lines.append(f"## 備考")
    lines.append(f"")
    lines.append(f"本報告書は、音声記録をWhisper（自動文字起こし）およびAI言語モデル（Ollama）で補助処理したものです。")
    lines.append(f"記述内容は担当支援者（{supporter}）が確認・承認したものを正式記録とします。")
    lines.append(f"")

    content = "\n".join(lines)
    Path(output_path).write_text(content, encoding="utf-8")
    print(f"\n✅ 完了: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="あしあと 報告書生成",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
実行例:
  # Stage1（切片化）のみ実行 → evidence_YYYYMMDD.json を生成
  python3 generate_report.py mapped.csv --stage 1

  # Stage2（記述生成）のみ実行 → evidence.json を読み込んで報告書を生成
  python3 generate_report.py --stage 2 --evidence evidence_20261018.json

  # 両ステージ一括実行（デフォルト）
  python3 generate_report.py mapped.csv
""",
    )
    parser.add_argument("csv", nargs="?", default=None, help="入力CSVファイル（Stage2単独実行時は不要）")
    parser.add_argument("--stage", choices=["all", "1", "2"], default="all",
                        help="実行ステージ: 1=切片化のみ, 2=記述生成のみ, all=両方（デフォルト）")
    parser.add_argument("--evidence", default=None,
                        help="Stage2専用: 切片化結果JSONファイルのパス")
    parser.add_argument("--date", default=datetime.today().strftime("%Y年%m月%d日"), help="セッション日付")
    parser.add_argument("--location", default="里山フィールド", help="活動場所")
    parser.add_argument("--activity", default="自然探索・昼食調理・火起こし体験", help="活動内容")
    parser.add_argument("--supporter", default="山田", help="支援者名（除外用）")
    parser.add_argument("--output", default=None, help="出力ファイルパス（Stage2/all用）")
    args = parser.parse_args()

    date_slug = datetime.today().strftime("%Y%m%d")

    # Stage2単独: evidence.jsonから読み込んで報告書生成
    if args.stage == "2":
        if args.csv:
            print("[WARNING] --stage 2 では --csv は無視されます。", file=sys.stderr)
        if not args.evidence:
            parser.error("--stage 2 には --evidence <JSONファイル> が必要です")
        session_info, supporter, children, child_counts, utterances_sample, evidence_by_child = load_evidence_json(args.evidence)
        output_path = args.output or f"report_校長向け_{date_slug}.md"
        print(f"📂 切片化結果を読み込み中: {args.evidence}")
        _run_stage2(
            evidence_by_child, children, session_info, supporter, output_path,
            child_counts=child_counts, utterances_sample=utterances_sample,
        )
        return

    # Stage1 / all: CSVが必要
    if not args.csv:
        parser.error("CSVファイルを指定してください（例: python3 generate_report.py mapped.csv）")

    # _meta.txt を自動検出してセッション情報を補完（CLI引数で上書き可）
    meta_path = str(Path(args.csv).with_suffix("")) + "_meta.txt"
    meta_from_file: dict = {}
    if Path(meta_path).exists():
        meta_from_file = load_meta_txt(meta_path)
        print(f"📋 メタ情報を読み込みました: {meta_path}")
    else:
        print(f"ℹ️  _meta.txt が見つかりません。CLI引数のセッション情報を使用します。", file=sys.stderr)

    default_date     = datetime.today().strftime("%Y年%m月%d日")
    default_location = "里山フィールド"
    default_activity = "自然探索・昼食調理・火起こし体験"

    session_info = {
        "date":     args.date     if args.date     != default_date     else meta_from_file.get("date",     default_date),
        "location": args.location if args.location != default_location else meta_from_file.get("location", default_location),
        "activity": args.activity if args.activity != default_activity else meta_from_file.get("activity", default_activity),
    }

    print(f"📂 読み込み中: {args.csv}")
    rows = load_csv(args.csv)
    children = get_children(rows, args.supporter)
    print(f"👶 検出された児童: {', '.join(children)}")
    print(f"📊 総発言数: {len(rows)}件\n")

    # Stage1: 切片化のみ
    if args.stage == "1":
        _run_stage1(rows, children, session_info, args.supporter, date_slug)
        return

    # all: 切片化→報告書生成を一括実行
    _, evidence_by_child = _run_stage1(rows, children, session_info, args.supporter, date_slug)
    output_path = args.output or f"report_校長向け_{date_slug}.md"
    _run_stage2(
        evidence_by_child, children, session_info, args.supporter, output_path,
        rows=rows,
    )


if __name__ == "__main__":
    main()
