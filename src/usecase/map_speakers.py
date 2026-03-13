#!/usr/bin/env python3
import csv
import json
import logging
import sys
import os
import urllib.error
from datetime import datetime

from config import OLLAMA_TIMEOUT
from infra.llm import call_ollama

logger = logging.getLogger(__name__)


def _sanitize_name(name: str, max_length: int = 50) -> str:
    """CSV破壊文字を除去し、長さを制限する。"""
    name = name.replace('"', '').replace('\n', '').replace('\r', '').strip()
    return name[:max_length]


def ollama_extract_activity(csv_text: str) -> str:
    """発言記録から実際に行われた活動内容を抽出する。"""
    system = (
        "あなたはNPO法人姫路YMCAが運営するフリースクール「あしあと」（太子遊び冒険の森ASOBO）の活動記録担当者であり、"
        "学校提出用の公式文書（出席扱い申請書）に記載する活動内容を"
        "発言記録から正確に抽出することを専門とする。"
        "根拠のない情報の追加・推測・創作は絶対に行わない。"
    )
    prompt = f"""以下はNPO法人姫路YMCAが運営するフリースクール「あしあと」の太子遊び冒険の森ASOBO（兵庫県揖保郡太子町の里山）での体験セッションの発言記録です。
この記録から、実際に行われた活動内容のみを抽出してください。

【判断ステップ（この順で思考すること）】
1. 発言記録を通読し、実際に行われた行動・体験を示す動詞句（「～した」「～採った」等）を探す
2. 「～したい」「～するかも」「～できればいいな」等の未遂・希望・予定を除外する
3. 残った活動を重複なく「・」区切り2〜6項目にまとめる

【出力例】
良い例: 自然観察・虫採り・火起こし体験・昼食調理
悪い例: 自然が好き・楽しそうだった・また来たい（活動ではなく感想・希望）

【制約】
- 発言記録に明確に存在する活動のみ記載すること（発言に根拠のない活動は絶対に含めない）
- 活動内容のみを1行で出力すること（説明・前置き・後書き不要）
- 出力形式: 「活動A・活動B・活動C」（「・」区切り、2〜6項目）

発言記録：
{csv_text}
"""
    return call_ollama(prompt, system=system, extra_options={"temperature": 0.1})


def map_speakers(input_csv: str, output_csv: str | None = None) -> tuple[str, str]:
    rows = []
    speakers = []
    with open(input_csv, encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
            if row['speaker'] not in speakers:
                speakers.append(row['speaker'])

    speakers.sort()
    print(f"\n📋 {len(speakers)} 名の話者を検出しました\n")

    for sp in speakers:
        samples = [r['text'] for r in rows if r['speaker'] == sp][:3]
        count = len([r for r in rows if r['speaker'] == sp])
        print(f"  {sp}（{count}発言）")
        for s in samples:
            print(f"    →「{s}」")
        print()

    print("─" * 50)
    print("各話者の名前を入力してください")
    print("（例：支援者：山田 / 児童：鈴木太郎）")
    print("─" * 50 + "\n")

    mapping = {}
    for sp in speakers:
        name = _sanitize_name(input(f"{sp} の名前: "))
        mapping[sp] = name if name else sp

    print("\n─" * 50)
    print("セッション情報")
    print("─" * 50)
    date     = input("活動日（例：2026年10月18日）: ").strip()
    location = input("活動場所（例：太子遊び冒険の森ASOBO・兵庫県揖保郡太子町）: ").strip()
    school_type_input = input("学校種別（小学校/中学校、Enterで小学校）: ").strip()
    school_type = school_type_input if school_type_input in ("小学校", "中学校") else "小学校"

    print("\n⏳ 活動内容をAIが抽出中...")
    csv_text = "\n".join([f"{r['speaker']}：{r['text']}" for r in rows])
    try:
        suggested = ollama_extract_activity(csv_text)
        print(f"\n💡 AI提案：{suggested}")
        activity = input(f"活動内容（Enterでそのまま使用、修正する場合は入力）: ").strip()
        if not activity:
            activity = suggested
    except (urllib.error.URLError, json.JSONDecodeError, RuntimeError) as e:
        logger.warning("AI抽出失敗（%s）。手動で入力してください。", e)
        activity = input("活動内容（例：自然観察・虫採り）: ").strip()

    if output_csv is None:
        base = os.path.splitext(input_csv)[0]
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        output_csv = f"{base}_mapped_{ts}.csv"

    print("\n─" * 50)
    anon_input = input("報告書用に児童名を匿名化しますか？（y/N）: ").strip().lower()
    anonymize = anon_input in ("y", "yes", "はい")
    anon_mapping: dict[str, str] = {}
    if anonymize:
        supporter_name = input("支援者の名前（匿名化から除外）: ").strip()
        code_index = 0
        code_labels = [chr(ord("A") + i) for i in range(26)]
        for name in mapping.values():
            if name and name != supporter_name and name not in anon_mapping:
                anon_mapping[name] = f"児童{code_labels[code_index]}"
                code_index += 1
        print("  匿名化マッピング:")
        for real, code in anon_mapping.items():
            print(f"    {real} → {code}")

    with open(output_csv, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['start','end','speaker','text'])
        writer.writeheader()
        for row in rows:
            name = mapping.get(row['speaker'], row['speaker'])
            row['speaker'] = anon_mapping.get(name, name) if anonymize else name
            writer.writerow(row)

    meta_path = output_csv.replace('.csv', '_meta.txt')
    with open(meta_path, 'w', encoding='utf-8') as f:
        f.write(f"活動日: {date}\n")
        f.write(f"場所: {location}\n")
        f.write(f"活動内容: {activity}\n")
        f.write(f"学校種別: {school_type}\n")
        f.write("話者マッピング:\n")
        for orig, name in mapping.items():
            f.write(f"  {orig} → {name}\n")
        if anonymize:
            f.write("匿名化マッピング:\n")
            for real, code in anon_mapping.items():
                f.write(f"  {real} → {code}\n")

    print(f"\n✅ 完了: {output_csv}")
    print(f"   メタ情報: {meta_path}")
    return output_csv, meta_path

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("使い方: python3 map_speakers.py <input.csv> [output.csv]")
        sys.exit(1)
    input_csv = sys.argv[1]
    output_csv = sys.argv[2] if len(sys.argv) > 2 else None
    map_speakers(input_csv, output_csv)
