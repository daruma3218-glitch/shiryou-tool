#!/usr/bin/env python3
"""Claude API（Web検索付き）を使ったリサーチ・分析・統合エージェント"""

import json
import os
import sys
import time
from pathlib import Path

import anthropic


def get_client() -> anthropic.Anthropic:
    """Anthropicクライアントを取得"""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key or api_key == "your_api_key_here":
        raise RuntimeError("ANTHROPIC_API_KEY が設定されていません。.env を確認してください。")
    return anthropic.Anthropic(api_key=api_key)


def claude_research(client: anthropic.Anthropic, query: str, system: str, max_tokens: int = 4096) -> str:
    """Claude API + Web検索でリサーチを実行"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=max_tokens,
                system=system,
                tools=[{
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": 10,
                }],
                messages=[{"role": "user", "content": query}],
            )

            # テキストブロックを抽出
            text_parts = []
            for block in response.content:
                if hasattr(block, "text"):
                    text_parts.append(block.text)

            return "\n".join(text_parts)

        except anthropic.RateLimitError:
            wait = 15 * (attempt + 1)
            print(f"  [RATE LIMIT] Waiting {wait}s...")
            time.sleep(wait)
        except Exception as e:
            print(f"  [ERROR] Claude API: {e}")
            if attempt == max_retries - 1:
                return ""
            time.sleep(3)

    return ""


def claude_query(client: anthropic.Anthropic, query: str, system: str, max_tokens: int = 4096) -> str:
    """Claude API（Web検索なし）でクエリを実行"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": query}],
            )

            text_parts = []
            for block in response.content:
                if hasattr(block, "text"):
                    text_parts.append(block.text)

            return "\n".join(text_parts)

        except anthropic.RateLimitError:
            wait = 15 * (attempt + 1)
            print(f"  [RATE LIMIT] Waiting {wait}s...")
            time.sleep(wait)
        except Exception as e:
            print(f"  [ERROR] Claude API: {e}")
            if attempt == max_retries - 1:
                return ""
            time.sleep(3)

    return ""


def analyze_manuscript(client: anthropic.Anthropic, manuscript_text: str) -> dict:
    """原稿を分析してキーワード・セクション等を抽出"""
    system = (
        "あなたは編集者です。原稿を分析してください。"
        "結果はJSON形式のみで返してください。他のテキストは不要です。"
    )
    query = f"""以下の原稿を分析してください。

{manuscript_text[:4000]}

以下のJSON形式で返してください（JSONのみ返すこと）:
{{
  "title": "原稿のメインテーマ/タイトル",
  "keywords": ["キーワード1", "キーワード2", ...（10-15個）],
  "sections": ["セクション1のタイトル", "セクション2のタイトル", ...],
  "summary": "原稿全体の要約（200文字以内）",
  "key_data_points": ["重要なデータ/数値1", "重要なデータ/数値2", ...]
}}"""

    result = claude_query(client, query, system)
    data = parse_json_object(result)

    if not data:
        lines = manuscript_text.split("\n")
        title = lines[0][:50] if lines else "無題"
        return {
            "title": title,
            "keywords": [],
            "sections": [title],
            "summary": manuscript_text[:200],
            "key_data_points": [],
        }

    return data


def research_twitter(client: anthropic.Anthropic, keywords: list, manuscript_summary: str) -> list:
    """X/Twitter の関連投稿をClaude Web検索で収集"""
    keyword_str = ", ".join(keywords[:8])
    system = (
        "あなたはSNSリサーチの専門家です。Web検索を使ってX/Twitterの投稿を調査してください。"
        "必ずWeb検索を実行して最新の情報を取得してください。"
        "結果はJSON配列形式で返してください。他のテキストは不要です。"
    )
    query = f"""以下のテーマに関連するX/Twitterの投稿をWeb検索で15-20件探してください。

テーマ: {manuscript_summary[:500]}
キーワード: {keyword_str}

実際にWeb検索を行い、X/Twitter上の投稿を見つけてください。

以下のJSON配列形式で返してください（JSONのみ返すこと）:
[
  {{
    "author": "投稿者名 (@ハンドル名)",
    "content": "投稿内容の要約（100文字以内）",
    "engagement": "いいね数やRT数（わかる範囲で）",
    "url": "投稿のURL（実在するURLのみ。不明なら空文字）",
    "relevance": "原稿のどの部分に関連するか"
  }}
]"""

    result = claude_research(client, query, system)
    return parse_json_array(result)


def research_youtube(client: anthropic.Anthropic, keywords: list, manuscript_summary: str) -> list:
    """YouTube の関連動画をClaude Web検索で収集"""
    keyword_str = ", ".join(keywords[:8])
    system = (
        "あなたはYouTubeリサーチの専門家です。Web検索を使ってYouTube動画を調査してください。"
        "必ずWeb検索を実行して最新の情報を取得してください。"
        "結果はJSON配列形式で返してください。他のテキストは不要です。"
    )
    query = f"""以下のテーマに関連するYouTube動画をWeb検索で10-15件探してください。

テーマ: {manuscript_summary[:500]}
キーワード: {keyword_str}

実際にWeb検索を行い、YouTube上の動画を見つけてください。

以下のJSON配列形式で返してください（JSONのみ返すこと）:
[
  {{
    "title": "動画タイトル",
    "channel": "チャンネル名",
    "views": "再生回数（わかる範囲で）",
    "url": "動画のURL (https://www.youtube.com/watch?v=...)",
    "description": "動画の概要（100文字以内）",
    "thumbnail": "",
    "relevance": "原稿のどの部分に関連するか"
  }}
]"""

    result = claude_research(client, query, system)
    return parse_json_array(result)


def research_web_data(client: anthropic.Anthropic, keywords: list, manuscript_summary: str, sections: list) -> list:
    """Web画像・データを40件収集（Claude Web検索）"""
    keyword_str = ", ".join(keywords[:10])
    sections_str = "\n".join([f"- {s}" for s in sections[:10]])
    system = (
        "あなたはWebリサーチの専門家です。Web検索を使って画像・データ・統計情報を調査してください。"
        "必ずWeb検索を実行して実在するURLを含めてください。"
        "公的機関・研究機関のデータを優先してください。"
        "結果はJSON配列形式で返してください。他のテキストは不要です。"
    )

    all_results = []

    # 2回に分けて検索（各20件）
    for batch in range(2):
        query = f"""以下のテーマに関連する画像・データ・図解・統計情報をWeb検索で20件探してください。
{"（前回とは異なるデータを探すこと）" if batch > 0 else ""}

テーマ: {manuscript_summary[:400]}
キーワード: {keyword_str}
原稿セクション:
{sections_str}

探すべきもの: 統計データ、グラフ、図解、インフォグラフィック、研究データ、公式資料、画像

実際にWeb検索を行い、実在するURLを含めてください。

以下のJSON配列形式で返してください（JSONのみ返すこと）:
[
  {{
    "index": {batch * 20 + 1},
    "description": "画像/データの説明（何の情報か）",
    "url": "ソースURL（必須、実在するURL）",
    "section": "対応する原稿セクション名",
    "type": "image|chart|data|infographic のいずれか"
  }}
]"""

        result = claude_research(client, query, system, max_tokens=8000)
        batch_results = parse_json_array(result)
        for i, item in enumerate(batch_results):
            item["index"] = len(all_results) + i + 1
        all_results.extend(batch_results)
        if batch < 1:
            time.sleep(2)

    return all_results[:40]


def generate_image_prompts(
    client: anthropic.Anthropic,
    manuscript_text: str,
    keywords: list,
    sections: list,
    mode: str,
    count: int,
    existing_materials_summary: str = "",
) -> list:
    """画像生成用の英文プロンプトをClaudeで作成"""
    sections_str = "\n".join([f"- {s}" for s in sections[:15]])

    if mode == "diagrams":
        system = (
            "あなたは情報デザインの専門家です。"
            "動画で使う図解画像のプロンプトを英語で作成してください。"
            "結果はJSON配列のみで返してください。"
        )
        extra_info = ""
        # Web検索で補足データを収集
        if keywords:
            research_system = "Webで情報を検索し、原稿を補足するデータや数値を収集してください。"
            research_query = f"以下のキーワードに関する最新のデータ・統計・数値を調査してください: {', '.join(keywords[:5])}"
            extra_info = claude_research(client, research_query, research_system, max_tokens=2000)

        query = f"""以下の原稿から、動画で使う図解画像のプロンプトを{count}個作成してください。

原稿（冒頭2000文字）:
{manuscript_text[:2000]}

セクション構成:
{sections_str}

補足データ:
{extra_info[:1000] if extra_info else "なし"}

条件:
- 英語のプロンプトにすること
- シンプルな図解（棒グラフ、円グラフ、フローチャート、比較図、タイムライン等）
- 色は統一感を持って
- 象徴的なデータは必ず図解にする
- 16:9の横長レイアウト
- 各プロンプトに対応するセクション名を記載

以下のJSON配列形式で返してください（JSONのみ返すこと）:
[
  {{"prompt": "英文プロンプト", "section": "セクション名"}}
]"""
    else:  # realistic
        system = (
            "あなたは映像プロデューサーです。"
            "動画で使うリアルな画像のプロンプトを英語で作成してください。"
            "結果はJSON配列のみで返してください。"
        )
        query = f"""以下の原稿から、動画で使うリアルな画像のプロンプトを{count}個作成してください。

原稿（冒頭2000文字）:
{manuscript_text[:2000]}

セクション構成:
{sections_str}

既存素材の概要:
{existing_materials_summary[:500]}

条件:
- 英語のプロンプトにすること
- 既存素材で足りない部分を補う画像
- 原稿の内容に合致するリアルな写真風画像
- 象徴的なシーンを切り取った画像
- 16:9の横長、映画的な構図
- 各プロンプトに対応するセクション名を記載

以下のJSON配列形式で返してください（JSONのみ返すこと）:
[
  {{"prompt": "英文プロンプト", "section": "セクション名"}}
]"""

    result = claude_query(client, query, system, max_tokens=8000)
    prompts = parse_json_array(result)
    return prompts[:count]


def generate_direction_data(
    client: anthropic.Anthropic,
    manuscript_text: str,
    sections: list,
    twitter_count: int,
    youtube_count: int,
    web_count: int,
    diagram_count: int,
    realistic_count: int,
    twitter_data: list,
    youtube_data: list,
    web_data: list,
    diagram_manifest: list,
    realistic_manifest: list,
) -> dict:
    """演出AIエージェント（Claude）: 全素材を統合してdata.jsonを生成"""
    sections_str = "\n".join([f"- {s}" for s in sections[:15]])

    twitter_summary = json.dumps(twitter_data[:5], ensure_ascii=False)[:800] if twitter_data else "なし"
    youtube_summary = json.dumps(youtube_data[:5], ensure_ascii=False)[:800] if youtube_data else "なし"
    web_summary = json.dumps(web_data[:5], ensure_ascii=False)[:800] if web_data else "なし"
    diagram_list = [d.get("section", "") for d in diagram_manifest if d.get("success")][:10]
    realistic_list = [d.get("section", "") for d in realistic_manifest if d.get("success")][:10]

    system = (
        "あなたは動画演出のプロフェッショナルです。"
        "収集した全素材を評価し、30分動画の構成を設計してください。"
        "動画編集者が使いやすい資料を作ることが目的です。"
        "結果はJSON形式のみで返してください。"
    )

    query = f"""以下の原稿と収集素材から、動画編集者向けの構成データを作成してください。

## 原稿（冒頭2000文字）
{manuscript_text[:2000]}

## セクション構成
{sections_str}

## 収集素材の概要
- X/Twitter投稿: {twitter_count}件
  サンプル: {twitter_summary}
- YouTube動画: {youtube_count}件
  サンプル: {youtube_summary}
- Web素材: {web_count}件
  サンプル: {web_summary}
- 図解画像: {diagram_count}枚 (セクション: {', '.join(diagram_list)})
- リアル画像: {realistic_count}枚 (セクション: {', '.join(realistic_list)})

## 指示
以下のJSON形式で返してください（JSONのみ返すこと）:

{{
  "title": "動画タイトル",
  "overview_html": "<p>原稿の概要（HTMLタグ使用可、200文字程度）</p>",
  "timeline": [
    {{"time": "00:00", "section": "セクション名", "duration": "X分", "description": "内容と使用素材の概要"}}
  ],
  "sections": [
    {{
      "id": "section-1",
      "title": "セクション名",
      "manuscript_text": "原稿の該当部分の要約（200文字以内）",
      "materials": [
        {{
          "type": "twitter",
          "type_label": "X投稿",
          "title": "素材タイトル",
          "thumbnail": "",
          "url": "ソースURL",
          "note": "演出メモ（表示タイミング、秒数等の具体的指示）"
        }}
      ]
    }}
  ],
  "direction_notes_html": "<h3>全体方針</h3><p>演出の全体方針...</p><h3>BGM提案</h3><p>...</p><h3>テロップ</h3><p>...</p><h3>不足素材</h3><p>...</p>"
}}

注意:
- 30分動画を想定してタイムラインを割り当てること
- 各セクションに最適な素材を選んで割り当て、具体的な演出指示をnoteに記載
- materials の type は twitter/youtube/web/diagram/realistic のいずれか
- diagram の thumbnail は "images/diagrams/diagrams_XXX.png" 形式
- realistic の thumbnail は "images/realistic/realistic_XXX.png" 形式
- Web素材には必ず元URLを記載
- 動画編集者の手間を減らす具体的な演出メモを書くこと"""

    result = claude_query(client, query, system, max_tokens=8000)

    try:
        data = parse_json_object(result)
        if data and "sections" in data:
            return data
    except Exception:
        pass

    return {
        "title": "動画素材集",
        "overview_html": f"<p>{manuscript_text[:200]}...</p>",
        "timeline": [],
        "sections": [{"id": f"section-{i+1}", "title": s, "manuscript_text": "", "materials": []}
                      for i, s in enumerate(sections)],
        "direction_notes_html": "<p>演出データの生成に失敗しました。手動で構成してください。</p>",
    }


def parse_json_array(text: str) -> list:
    """テキストからJSON配列を抽出"""
    text = text.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()

    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass

    return []


def parse_json_object(text: str) -> dict:
    """テキストからJSONオブジェクトを抽出"""
    text = text.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass

    return {}
