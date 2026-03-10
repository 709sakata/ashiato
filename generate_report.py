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

def generate_child_report(child: str, transcript: str, session_info: dict, child_count: int) -> str:
    """児童1人分の観点別記述を生成"""
    system = "あなたは小学校の教育記録を作成する専門家です。指導要録の観点別学習状況の記述を、担当教員が観察した文体で正確に作成します。"

    prompt = f"""以下は、不登校支援活動「ASOBO」での里山体験セッションにおける、{child}さんの発言記録です。

【セッション情報】
日付: {session_info['date']}
活動場所: {session_info['location']}
活動内容: {session_info['activity']}

【{child}さんの発言記録（支援者とのやりとり含む）】
{transcript}

【この児童の総発言数: {child_count}件】

【観点の定義（里山体験における解釈）】
- 知識・技能: 自然の生き物・植物・道具（火起こし等）に関する知識の獲得や技能の習得
- 思考・判断・表現: 自然現象への疑問・観察結果の言語化・仮説・比較・感想の表現
- 主体的に学習に取り組む態度: 自発的な挑戦・粘り強さ・次回への意欲・他者との協力意識

【指示】
上記の記録をもとに、小学校の「指導要録」に記載する観点別学習状況の記述を作成してください。

以下のルールを厳守してください：
- 出力は必ず下記のフォーマット通りに、見出しレベルも含めて正確に出力すること
- 各観点は2〜3文で、具体的なエピソードを根拠として記述すること
- 人称は「{child}は」で統一し、「彼は」「彼女は」は使わないこと
- 「AIが判断した」ではなく「担当教員が観察した」という文体で書くこと
- 体言止めや箇条書きは使わず、文章で書くこと
- 各観点の末尾に（根拠発言数: N件）を括弧書きで添えること。Nはその観点の記述で引用・参照した発言の件数のみを数えること（総発言数{child_count}件を超えないこと）
- 文字起こし誤りと思われる語（ひらがな・カタカナ誤変換など不自然な表現）は文脈から正しい語に解釈して記述し、誤変換をそのまま記述しないこと
- フォーマット以外の前置きや後書きは一切出力しないこと

出力フォーマット（この通りに出力すること）:

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
    representative = _pick_representative_utterances(rows, children, n=2)

    system = "あなたは小学校の教育記録を作成する専門家です。現職教員が観察した文体で、簡潔かつ教育的意義のある報告文を作成します。"

    prompt = f"""以下は、不登校支援活動「ASOBO」の里山体験セッションの記録です。

【セッション情報】
日付: {session_info['date']}
活動場所: {session_info['location']}
活動内容: {session_info['activity']}
参加児童: {', '.join(children)}（{len(children)}名）

【児童別発言件数】
{stats_text}

【各児童の代表的な発言】
{representative}

【指示】
校長先生への報告書の冒頭に載せる「セッション総括」を200字程度で書いてください。
・活動内容（{session_info['activity']}）の流れを簡潔に含めること
・参加した全児童（{', '.join(children)}）に均等に言及し、特定の2名だけを目立たせないこと
・「不登校」という言葉は使わず「学校外での学びの場」として表現
・現職教員が観察した文体で
・体験活動の教育的意義を含める
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
        print(f"✍️  [{i}/{len(children)}] {child} の記述を生成中...")
        transcript = build_transcript_per_child(rows, child)
        child_count = sum(1 for r in rows if r["speaker"] == child)

        if child_count == 0:
            lines.append(f"### {child}")
            lines.append(f"※ 本セッションでの発言記録なし")
            lines.append(f"")
            continue

        report = generate_child_report(child, transcript, session_info, child_count)
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
