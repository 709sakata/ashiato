"""
プロンプトテンプレートローダー

使い方:
  from ashiato.prompts import load_prompt
  prompt = load_prompt("segment_evidence", child="太郎", school_type="小学校", transcript="...")
"""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

_env = Environment(
    loader=FileSystemLoader(Path(__file__).parent),
    keep_trailing_newline=True,
    undefined=StrictUndefined,
)


def load_prompt(name: str, **kwargs: object) -> str:
    """指定した名前の .j2 テンプレートを変数で展開して返す。"""
    return _env.get_template(f"{name}.j2").render(**kwargs)
