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

# ===== 観点別学習状況の観点（小学校・中学校学習指導要領準拠） =====
VIEWPOINTS = [
    "知識・技能",
    "思考・判断・表現",
    "主体的に学習に取り組む態度",
]

EVIDENCE_SCHEMA = {
    "type": "object",
    "properties": {
        "知識・技能": {"type": "array", "items": {"type": "string"}},
        "思考・判断・表現": {"type": "array", "items": {"type": "string"}},
        "主体的に学習に取り組む態度": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["知識・技能", "思考・判断・表現", "主体的に学習に取り組む態度"],
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

def call_ollama(prompt: str, system: str = "", num_predict: int = -1, format: dict | None = None, extra_options: dict | None = None) -> str:
    body: dict = {
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": num_predict, **(extra_options or {})}
    }
    if system:
        body["system"] = system
    if format:
        body["format"] = format
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


def extract_evidence_per_viewpoint(child: str, transcript: str, session_info: dict | None = None) -> dict[str, list[str]]:
    """Stage 1 - 切片化: 発言記録を観点別に分類し、根拠発言をそのまま抜き出す"""
    school_type = (session_info or {}).get("school_type", "小学校")

    # [Persona] 指導要録の公的性と審査リスクを明示して正確性を担保
    # [Context] 出席扱い申請書の根拠として審査される文書であることを強調
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
    raw = call_ollama(prompt, system=system, num_predict=-1, format=EVIDENCE_SCHEMA)
    try:
        parsed = json.loads(raw)
        return {v: [u for u in parsed.get(v, []) if u] for v in VIEWPOINTS}
    except (json.JSONDecodeError, AttributeError):
        # JSON パース失敗時は空の結果を返す（呼び出し元で確認できるよう警告）
        print(f"[WARNING] Stage1 JSON パース失敗（{child}）。Ollama のバージョンを確認してください（0.3.0 以上必要）。", file=sys.stderr)
        return {v: [] for v in VIEWPOINTS}


def generate_child_report(child: str, evidence: dict[str, list[str]], session_info: dict) -> str:
    """Stage 2 - 記述生成: 切片化済みの根拠発言リストをもとに観点別記述を作成"""
    school_type = session_info.get("school_type", "小学校")

    # [Persona] 指導要録専門の記録作成者、公文書としての責任を明示
    # [Context] 出席扱い申請書の下書きとして教員が確認・承認する文書
    system = (
        f"あなたは{school_type}指導要録の観点別学習状況記述を専門とする教育記録作成者である。"
        "提供された根拠発言リストのみを根拠に、支援者が観察した事実として記述する。"
        "リストに存在しない事実の創作・推測・補完は絶対に行わない。"
        "この記録は学校への出席扱い申請に使用される公式文書の下書きであり、"
        "教員が内容を確認・承認した上で正式記録となる。"
    )

    # 観点別根拠発言テキストを構築（件数はPythonで確定）
    evidence_counts = {v: len(evidence[v]) for v in VIEWPOINTS}
    evidence_parts = []
    for v in VIEWPOINTS:
        utterances = evidence[v]
        evidence_parts.append(f"### {v}（{evidence_counts[v]}件）")
        if utterances:
            for u in utterances:
                evidence_parts.append(f"- 「{u}」")
        else:
            evidence_parts.append("- （根拠発言なし）")
    evidence_text = "\n".join(evidence_parts)

    prompt = f"""# 前提条件（Context）
目的: NPO法人姫路YMCAが運営するフリースクール「あしあと」の太子遊び冒険の森ASOBO（兵庫県揖保郡太子町の里山）での体験セッションにおける{child}さんの根拠発言リスト（Stage1切片化済み）をもとに、
{school_type}への出席扱い申請用の指導要録「観点別学習状況」の記述を作成する。

# セッション情報
日付: {session_info['date']}
活動場所: {session_info['location']}
活動内容: {session_info['activity']}

# 観点別根拠発言リスト（これのみを使用すること）
{evidence_text}

# 記述例（Output: このスタイルで書くこと）
### 知識・技能（記述例）
{child}は「カブトムシはクヌギの木の汁を吸うんだって！」と発言し、
里山生物の生態について新たな知識を得た様子が見られた。
また「火起こし、できた！」と述べ、道具の扱いを習得する場面も確認された。
（根拠発言数: 2件）

# 思考ステップ（この順で実行すること）
1. 各観点の根拠発言リストを読み込む
2. 根拠発言から読み取れる事実（何をしたか・何と発言したか）を整理する
3. 各観点について2〜3文の記述を作成する
   - 1文目: 根拠発言に基づく具体的な行動・発言の事実（「〜と発言した」「〜に取り組んだ」）
   - 2文目: その事実から観察できること（「〜する様子が見られた」「〜が認められた」）
   - 3文目（任意）: 追加の根拠発言がある場合、補足として記述
4. 根拠発言が「なし」の観点は「本セッションの記録からは確認できなかった」と記述する
5. 全観点の記述が完了したら成果物を出力する

# 制約ルール（すべて厳守する）
1. 根拠発言リストに存在しない事実は絶対に記述しない（創作・推測・補完禁止）
   NG例: 「{child}は楽しんでいた」（感情の推測であり、根拠発言なし）
2. 人称は「{child}は」で統一し、「彼は」「彼女は」は使わない
3. 支援者が実際に観察した事実として書く（「〜した」「〜と発言した」「〜と述べた」等）
4. 体言止めや箇条書きは使わず、文章で書く
5. 各観点の末尾に（根拠発言数: N件）を括弧書きで添える。Nは以下の確定値を使うこと:
   知識・技能: {evidence_counts['知識・技能']}件 / 思考・判断・表現: {evidence_counts['思考・判断・表現']}件 / 主体的に学習に取り組む態度: {evidence_counts['主体的に学習に取り組む態度']}件
6. 明らかな音声認識の変換誤り（例：固有名詞の当て字）は自然な語に修正してよいが、発言の意図・内容・ニュアンスを変える解釈は行わないこと
7. 前置きや後書きは出力しない

# 成果物のフォーマット（この通りに出力すること）

## {child}

### 知識・技能
（ここに記述）（根拠発言数: {evidence_counts['知識・技能']}件）

### 思考・判断・表現
（ここに記述）（根拠発言数: {evidence_counts['思考・判断・表現']}件）

### 主体的に学習に取り組む態度
（ここに記述）（根拠発言数: {evidence_counts['主体的に学習に取り組む態度']}件）
"""
    return normalize_child_report(child, call_ollama(
        prompt, system=system, num_predict=2000,
        extra_options={"repeat_penalty": 1.1, "top_k": 20},
    ))

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

    # [Persona] 学校外支援センターの公式記録補助者として役割を明確化
    # [Context] 校長向け報告書の冒頭文、出席扱い申請の公的文書
    system = (
        "あなたはNPO法人姫路YMCAが運営するフリースクール「あしあと」（太子遊び冒険の森ASOBO）の公式記録補助者である。"
        "校長先生への活動報告書（出席扱い申請書添付用）の冒頭総括文を作成することを専門とする。"
        "与えられた【各児童の発言抜粋】に存在する事実のみを根拠に記述する。"
        "記録にない活動・様子・発言の創作・推測・補完は絶対に行わない。"
    )

    prompt = f"""以下は、NPO法人姫路YMCAが運営するフリースクール「あしあと」（太子遊び冒険の森ASOBO・兵庫県揖保郡太子町の里山）での体験セッションの記録です。

【セッション情報】
日付: {session_info['date']}
活動場所: {session_info['location']}
活動内容: {session_info['activity']}
参加児童: {', '.join(children)}（{len(children)}名）

【児童別発言件数】
{stats_text}

【各児童の発言抜粋（これが根拠となる唯一の記録）】
{representative}

【総括例（このスタイルで書くこと）】
{session_info['date']}、{session_info['location']}にて{session_info['activity']}を実施した。
{children[0]}は「〜」と発言するなど、積極的に活動に参加する様子が見られた。
（各児童1文ずつ続く）
活動全体を通じ、学習指導要領に沿った体験学習の機会となった。

【指示】
校長先生への報告書の冒頭に載せる「セッション総括」を作成してください。

【構成（この順で書くこと）】
1. 書き出し文: セッション全体の活動概況を1文
2. 各児童の観察文: 以下の児童それぞれについて1文ずつ（省略・まとめ禁止）
{chr(10).join(f"   - {c}について発言抜粋に基づく観察1文" for c in children)}
3. まとめ文: 活動全体の意義・様子を1文

【制約ルール】
1. 各児童（{', '.join(children)}）に必ず1文ずつ言及すること（「〜と〜は」でまとめる省略禁止）
2. 【各児童の発言抜粋】に記載された内容のみを根拠に記述すること（創作・推測禁止）
3. 活動内容（{session_info['activity']}）の範囲内で述べること
4. 「不登校」という言葉は使わず「学校外での学びの場」として表現
5. 支援者が観察した事実として書くこと（「〜する様子が見られた」「〜と発言した」等）
6. 前置き・後書きは不要、総括の文章のみ出力すること
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
    lines.append(f"# あしあと（太子遊び冒険の森ASOBO）活動報告書（校長向け）")
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
    parser.add_argument("--location", default="太子遊び冒険の森ASOBO（兵庫県揖保郡太子町）", help="活動場所")
    parser.add_argument("--activity", default="自然観察・昼食調理・火起こし体験", help="活動内容")
    parser.add_argument("--supporter", default="山田", help="支援者名（除外用）")
    parser.add_argument("--school-type", choices=["小学校", "中学校"], default="小学校", help="学校種別（デフォルト: 小学校）")
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
