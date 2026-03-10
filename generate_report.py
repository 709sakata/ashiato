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

def call_ollama(prompt: str, system: str = "") -> str:
    body: dict = {
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": 1500}
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

    # 「彼は」「彼女は」を児童名に置換
    raw = raw.replace('彼女は', f'{child}は').replace('彼は', f'{child}は')
    raw = raw.replace('彼女が', f'{child}が').replace('彼が', f'{child}が')
    raw = raw.replace('彼女の', f'{child}の').replace('彼の', f'{child}の')

    return raw.strip()

def _parse_evidence(raw: str) -> dict[str, list[str]]:
    """切片化LLM出力をパースして観点別発言リストに変換"""
    import re
    result: dict[str, list[str]] = {v: [] for v in VIEWPOINTS}
    current = None
    for line in raw.splitlines():
        stripped = line.strip()
        for v in VIEWPOINTS:
            if re.match(r'^#{0,4}\s*' + re.escape(v), stripped):
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
    raw = call_ollama(prompt, system=system)
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
    return normalize_child_report(child, call_ollama(prompt, system=system))

def _pick_representative_utterances(rows: list[dict], children: list[str], n: int = 2) -> str:
    """各児童の代表的な発言をn件ずつ抽出してテキスト化"""
    lines = []
    for child in children:
        utterances = [r["text"] for r in rows if r["speaker"] == child and r.get("text") and r["text"] != "[聞き取り不明]"]
        # 短すぎる発言を除き、中盤から代表発言を選ぶ
        candidates = [u for u in utterances if len(u) >= 8]
        if not candidates:
            candidates = utterances
        # 均等にn件取る（先頭・末尾に偏らないよう中央付近から）
        step = max(1, len(candidates) // (n + 1))
        picks = [candidates[min(i * step, len(candidates) - 1)] for i in range(1, n + 1)]
        for p in picks:
            lines.append(f"  {child}:「{p}」")
    return "\n".join(lines)

def generate_session_summary(rows: list[dict], children: list[str], session_info: dict) -> str:
    """セッション全体サマリーを生成"""
    stats = {child: sum(1 for r in rows if r["speaker"] == child) for child in children}
    stats_text = "\n".join(f"  {k}: {v}件" for k, v in stats.items())
    representative = _pick_representative_utterances(rows, children, n=4)

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
    return call_ollama(prompt, system=system)

def main():
    parser = argparse.ArgumentParser(description="あしあと 報告書生成")
    parser.add_argument("csv", help="入力CSVファイル")
    parser.add_argument("--date", default=datetime.today().strftime("%Y年%m月%d日"), help="セッション日付")
    parser.add_argument("--location", default="里山フィールド", help="活動場所")
    parser.add_argument("--activity", default="自然探索・昼食調理・火起こし体験", help="活動内容")
    parser.add_argument("--supporter", default="山田", help="支援者名（除外用）")
    parser.add_argument("--output", default=None, help="出力ファイルパス")
    args = parser.parse_args()

    session_info = {
        "date": args.date,
        "location": args.location,
        "activity": args.activity,
    }

    print(f"📂 読み込み中: {args.csv}")
    rows = load_csv(args.csv)
    children = get_children(rows, args.supporter)
    print(f"👶 検出された児童: {', '.join(children)}")
    print(f"📊 総発言数: {len(rows)}件\n")

    # 出力ファイル名
    date_slug = datetime.today().strftime("%Y%m%d")
    output_path = args.output or f"report_校長向け_{date_slug}.md"

    lines = []
    lines.append(f"# ASOBO 活動報告書（校長向け）")
    lines.append(f"")
    lines.append(f"**日付**: {session_info['date']}  ")
    lines.append(f"**場所**: {session_info['location']}  ")
    lines.append(f"**活動**: {session_info['activity']}  ")
    lines.append(f"**参加児童**: {', '.join(children)}（{len(children)}名）  ")
    lines.append(f"**支援者**: {args.supporter}  ")
    lines.append(f"**記録生成日**: {datetime.today().strftime('%Y年%m月%d日')}  ")
    lines.append(f"")
    lines.append(f"---")
    lines.append(f"")

    # セッション総括
    print("📝 セッション総括を生成中...")
    summary = generate_session_summary(rows, children, session_info)
    lines.append(f"## セッション総括")
    lines.append(f"")
    lines.append(summary)
    lines.append(f"")
    lines.append(f"---")
    lines.append(f"")

    # 観点別記述（児童ごと）
    lines.append(f"## 観点別学習状況（児童別）")
    lines.append(f"")

    for i, child in enumerate(children, 1):
        child_count = sum(1 for r in rows if r["speaker"] == child)

        if child_count == 0:
            lines.append(f"### {child}")
            lines.append(f"※ 本セッションでの発言記録なし")
            lines.append(f"")
            continue

        transcript = build_transcript_per_child(rows, child)

        # Stage 1: 切片化
        print(f"\n📋 [{i}/{len(children)}] {child}（{child_count}発言）: Stage1 切片化中...")
        evidence = extract_evidence_per_viewpoint(child, transcript)

        # 中間確認: 抽出された根拠発言を表示（ハルシネーション確認用）
        print(f"  ┌─ 切片化結果（確認してください）")
        for v in VIEWPOINTS:
            utterances = evidence[v]
            print(f"  │ 【{v}】{len(utterances)}件")
            for u in utterances:
                print(f"  │   ・{u}")
        print(f"  └─────────────────")

        # Stage 2: 記述生成
        print(f"✍️  [{i}/{len(children)}] {child}: Stage2 記述生成中...")
        report = generate_child_report(child, evidence, session_info)
        lines.append(report)
        lines.append(f"")
        lines.append(f"---")
        lines.append(f"")

    # フッター
    lines.append(f"## 備考")
    lines.append(f"")
    lines.append(f"本報告書は、音声記録をWhisper（自動文字起こし）およびAI言語モデル（Ollama）で補助処理したものです。")
    lines.append(f"記述内容は担当支援者（{args.supporter}）が確認・承認したものを正式記録とします。")
    lines.append(f"")

    # 書き出し
    content = "\n".join(lines)
    Path(output_path).write_text(content, encoding="utf-8")
    print(f"\n✅ 完了: {output_path}")

if __name__ == "__main__":
    main()
