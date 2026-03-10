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

def load_csv(path: str) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows

def get_children(rows: list[dict], supporter: str) -> list[str]:
    speakers = set(r["speaker"] for r in rows)
    excludes = {supporter, "全員", "不明", "[不明]", ""}
    return sorted(s for s in speakers if s not in excludes and not s.startswith("["))

def build_transcript_per_child(rows: list[dict], child: str) -> str:
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

def call_ollama(prompt: str) -> str:
    payload = json.dumps({
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": 1500}
    }).encode("utf-8")
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

def generate_child_report(child: str, transcript: str, session_info: dict) -> str:
    prompt = f"""あなたは小学校の教育記録を作成する専門家です。
以下は、不登校支援活動「ASOBO」での里山体験セッションにおける、{child}さんの発言記録です。

【セッション情報】
日付: {session_info['date']}
活動場所: {session_info['location']}
活動内容: {session_info['activity']}

【{child}さんの発言記録（支援者とのやりとり含む）】
{transcript}

【指示】
上記の記録をもとに、小学校の「指導要録」に記載する観点別学習状況の記述を作成してください。
以下の3観点それぞれについて、2〜3文で具体的なエピソードを根拠として記述してください。
「AIが判断した」ではなく「担当教員が観察した」という文体で書いてください。
体言止めや箇条書きではなく、文章で書いてください。

# {child}

## 知識・技能
（自然・生き物・調理・火起こし等への知識習得や技能の様子）

## 思考・判断・表現
（観察・発見・発言・質問の質や、自分の言葉で表現する様子）

## 主体的に学習に取り組む態度
（挑戦・持続・意欲・仲間との関わりの様子）

記述の末尾に、記録された発言数を（根拠発言数: N件）と括弧書きで添えてください。
"""
    return call_ollama(prompt)

def generate_session_summary(rows: list[dict], children: list[str], session_info: dict) -> str:
    stats = {}
    for child in children:
        count = sum(1 for r in rows if r["speaker"] == child)
        stats[child] = count
    stats_text = "\n".join(f"  {k}: {v}件" for k, v in stats.items())
    prompt = f"""以下は、不登校支援活動「ASOBO」の里山体験セッションの記録です。

【セッション情報】
日付: {session_info['date']}
活動場所: {session_info['location']}
活動内容: {session_info['activity']}
参加児童: {', '.join(children)}

【児童別発言件数】
{stats_text}

【指示】
校長先生への報告書の冒頭に載せる「セッション総括」を200字程度で書いてください。
・活動の流れと児童全体の様子を簡潔に
・「不登校」という言葉は使わず「学校外での学びの場」として表現
・現職教員が観察した文体で
・体験活動の教育的意義を含める
"""
    return call_ollama(prompt)

def main():
    parser = argparse.ArgumentParser(description="あしあと 報告書生成")
    parser.add_argument("csv", help="入力CSVファイル")
    parser.add_argument("--date", default=datetime.today().strftime("%Y年%m月%d日"))
    parser.add_argument("--location", default="里山フィールド")
    parser.add_argument("--activity", default="自然探索・昼食調理・火起こし体験")
    parser.add_argument("--supporter", default="塚原")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    session_info = {"date": args.date, "location": args.location, "activity": args.activity}

    print(f"📂 読み込み中: {args.csv}")
    rows = load_csv(args.csv)
    children = get_children(rows, args.supporter)
    print(f"👶 検出された児童: {', '.join(children)}")
    print(f"📊 総発言数: {len(rows)}件\n")

    date_slug = datetime.today().strftime("%Y%m%d")
    output_path = args.output or f"report_校長向け_{date_slug}.md"

    lines = []
    lines.append("# ASOBO 活動報告書（校長向け）")
    lines.append("")
    lines.append(f"**日付**: {session_info['date']}  ")
    lines.append(f"**場所**: {session_info['location']}  ")
    lines.append(f"**活動**: {session_info['activity']}  ")
    lines.append(f"**参加児童**: {', '.join(children)}（{len(children)}名）  ")
    lines.append(f"**支援者**: {args.supporter}  ")
    lines.append(f"**記録生成日**: {datetime.today().strftime('%Y年%m月%d日')}  ")
    lines.append("")
    lines.append("---")
    lines.append("")

    print("📝 セッション総括を生成中...")
    summary = generate_session_summary(rows, children, session_info)
    lines.append("## セッション総括")
    lines.append("")
    lines.append(summary)
    lines.append("")
    lines.append("---")
    lines.append("")

    lines.append("## 観点別学習状況（児童別）")
    lines.append("")

    for i, child in enumerate(children, 1):
        print(f"✍️  [{i}/{len(children)}] {child} の記述を生成中...")
        transcript = build_transcript_per_child(rows, child)
        child_count = sum(1 for r in rows if r["speaker"] == child)
        if child_count == 0:
            lines.append(f"### {child}")
            lines.append("※ 本セッションでの発言記録なし")
            lines.append("")
            continue
        report = generate_child_report(child, transcript, session_info)
        lines.append(report)
        lines.append("")
        lines.append("---")
        lines.append("")

    lines.append("## 備考")
    lines.append("")
    lines.append("本報告書は、音声記録をWhisper（自動文字起こし）およびAI言語モデル（Ollama）で補助処理したものです。")
    lines.append(f"記述内容は担当支援者（{args.supporter}）が確認・承認したものを正式記録とします。")
    lines.append("")

    content = "\n".join(lines)
    Path(output_path).write_text(content, encoding="utf-8")
    print(f"\n✅ 完了: {output_path}")

if __name__ == "__main__":
    main()
