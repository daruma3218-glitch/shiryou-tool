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


def claude_research(client: anthropic.Anthropic, query: str, system: str, max_tokens: int = 4096, max_uses: int = 10) -> str:
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
                    "max_uses": max_uses,
                }],
                messages=[{"role": "user", "content": query}],
            )

            # テキストブロックと検索結果URLを両方抽出
            text_parts = []
            search_results = []
            block_types_found = []
            for block in response.content:
                block_type = getattr(block, "type", "unknown")
                block_types_found.append(block_type)
                if hasattr(block, "text"):
                    text_parts.append(block.text)
                # web_search_tool_result から実際のURLを抽出
                if block_type == "web_search_tool_result":
                    content = getattr(block, "content", [])
                    for result in content:
                        result_type = getattr(result, "type", "")
                        if result_type == "web_search_result":
                            url = getattr(result, "url", "")
                            title = getattr(result, "title", "")
                            if url:
                                search_results.append({"url": url, "title": title})

            print(f"  [DEBUG] Block types: {block_types_found}")
            print(f"  [DEBUG] Search results extracted: {len(search_results)}")

            text = "\n".join(text_parts)

            # 検索結果URLが見つかった場合、テキスト末尾に追記して
            # Claudeが生成したテキスト内のURLより実際のURLを優先させる
            if search_results:
                urls_info = "\n\n--- 実際の検索結果URL ---\n"
                for sr in search_results:
                    urls_info += f"- {sr['title']}: {sr['url']}\n"
                text += urls_info

            return text

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

            if not response or not response.content:
                print(f"  [WARN] Attempt {attempt+1}/{max_retries}: Empty response", flush=True)
                if attempt == max_retries - 1:
                    return ""
                time.sleep(3)
                continue

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


def _get_youtube_api_key(project_root: Path = None) -> str:
    """YOUTUBE_API_KEY を .env または環境変数から取得"""
    if project_root:
        env_path = project_root / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("YOUTUBE_API_KEY=") and not line.endswith("="):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if key and key != "your_api_key_here":
                        return key
    return os.environ.get("YOUTUBE_API_KEY", "")


def research_youtube(client: anthropic.Anthropic, keywords: list, manuscript_summary: str) -> list:
    """YouTube Data API v3 で関連動画を検索"""
    import urllib.request
    import urllib.parse

    project_root = Path(__file__).parent.parent
    api_key = _get_youtube_api_key(project_root)

    if not api_key:
        print("  [WARN] YOUTUBE_API_KEY が未設定です。YouTube検索をスキップします。", flush=True)
        return []

    keyword_str = " ".join(keywords[:5])
    # 複数クエリで検索して幅広く収集
    search_queries = [
        keyword_str,
        f"{keywords[0]} 解説" if keywords else keyword_str,
    ]

    all_videos = {}  # video_id -> video_data

    for q in search_queries:
        try:
            params = urllib.parse.urlencode({
                "part": "snippet",
                "q": q,
                "type": "video",
                "maxResults": 10,
                "relevanceLanguage": "ja",
                "key": api_key,
            })
            url = f"https://www.googleapis.com/youtube/v3/search?{params}"

            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            for item in data.get("items", []):
                video_id = item.get("id", {}).get("videoId", "")
                if not video_id or video_id in all_videos:
                    continue
                snippet = item.get("snippet", {})
                all_videos[video_id] = {
                    "title": snippet.get("title", ""),
                    "channel": snippet.get("channelTitle", ""),
                    "views": "",
                    "url": f"https://www.youtube.com/watch?v={video_id}",
                    "description": snippet.get("description", "")[:100],
                    "thumbnail": snippet.get("thumbnails", {}).get("high", {}).get("url", ""),
                    "relevance": "",
                }

            print(f"  [YouTube API] 「{q}」: {len(data.get('items', []))}件", flush=True)

        except Exception as e:
            print(f"  [YouTube API ERROR] {e}", flush=True)

        if len(all_videos) >= 15:
            break
        time.sleep(0.5)

    results = list(all_videos.values())[:15]
    print(f"  [YouTube] 合計{len(results)}件の動画を収集", flush=True)
    return results


def research_web_data(client: anthropic.Anthropic, keywords: list, manuscript_summary: str, sections: list) -> list:
    """Web画像・データを40件収集（Claude Web検索 - 実URL抽出方式）"""
    import re
    keyword_str = ", ".join(keywords[:10])
    sections_str = "\n".join([f"- {s}" for s in sections[:10]])
    system = (
        "あなたはWebリサーチの専門家です。Web検索を使って画像・データ・統計情報を調査してください。"
        "必ずWeb検索を実行して実在するURLを含めてください。"
        "公的機関・研究機関のデータを優先してください。"
        "結果はJSON配列形式で返してください。他のテキストは不要です。"
        "\n\n重要: JSONのurlフィールドには、Web検索で実際に見つかったURLをそのまま使ってください。"
        "URLを推測・生成・変更しないでください。検索結果に含まれるURLだけを使うこと。"
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

実際にWeb検索を行い、検索結果のURLをそのまま使ってください。URLを推測・変更しないこと。

以下のJSON配列形式で返してください（JSONのみ返すこと）:
[
  {{
    "index": {batch * 20 + 1},
    "description": "画像/データの説明（何の情報か）",
    "url": "検索結果の実際のURL（変更しないこと）",
    "section": "対応する原稿セクション名",
    "type": "image|chart|data|infographic のいずれか"
  }}
]"""

        result = claude_research(client, query, system, max_tokens=8000)

        # 検索結果テキストから実URLを抽出
        real_urls = re.findall(r'https?://[^\s\'"<>\]）」]+', result)
        real_urls_set = set(u.rstrip(".,;:)」") for u in real_urls)

        batch_results = parse_json_array(result)

        # JSON内のURLを検証・修正
        for item in batch_results:
            url = item.get("url", "")
            if url and url not in real_urls_set:
                # URLがハルシネーションの可能性 - 検索結果の実URLで置き換え
                matched = [u for u in real_urls_set if u.startswith("http") and "youtube.com" not in u]
                if matched:
                    item["url"] = matched[len(all_results) % len(matched)]

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
            "動画で使う図解画像のプロンプトを作成してください。"
            "図解内のテキスト・ラベル・タイトルは全て日本語で指定してください。"
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
- プロンプトは英語で書くが、図解内に表示するテキスト・ラベル・数値・タイトルは必ず日本語で指定すること
- 例: "A bar chart showing ... with Japanese labels: 売上高, 利益率, 前年比120%"
- シンプルな図解（棒グラフ、円グラフ、フローチャート、比較図、タイムライン等）
- 色は統一感を持って
- 象徴的なデータは必ず図解にする
- 16:9の横長レイアウト
- 各プロンプトに対応するセクション名を記載
- 重要: 各プロンプトは必ず異なる内容にすること。同じテーマでも視点・切り口・データを変える
- 同じセクションから複数枚作る場合も、それぞれ別のデータや観点を扱うこと

以下のJSON配列形式で返してください（JSONのみ返すこと）:
[
  {{"prompt": "英語プロンプト（日本語テキスト指定を含む）", "section": "セクション名"}}
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
- 重要: 各プロンプトは必ず異なる内容にすること。同じテーマでも被写体・構図・シチュエーションを変える
- 図解画像とは別の「写真的」な素材にすること（グラフやチャートは不要）

以下のJSON配列形式で返してください（JSONのみ返すこと）:
[
  {{"prompt": "英文プロンプト", "section": "セクション名"}}
]"""

    result = claude_query(client, query, system, max_tokens=8000)
    prompts = parse_json_array(result)

    # 重複排除: プロンプトテキストが類似しているものを除去
    seen_prompts = set()
    unique_prompts = []
    for p in prompts:
        prompt_text = p.get("prompt", "").strip().lower()
        # 先頭60文字で簡易的に重複判定
        key = prompt_text[:60]
        if key and key not in seen_prompts:
            seen_prompts.add(key)
            unique_prompts.append(p)

    return unique_prompts[:count]


def generate_direction_data(
    client: anthropic.Anthropic,
    manuscript_text: str,
    analysis: dict,
    youtube_results: list,
    web_results: list,
    diagram_results: list,
    realistic_results: list,
) -> dict:
    """演出エージェント: 原稿と素材リストを分析して具体的な素材配置指示を含む演出プランを生成"""
    sections = analysis.get("sections", [])
    keywords = analysis.get("keywords", [])
    title = analysis.get("title", "動画素材集")
    summary = analysis.get("summary", "")

    # セクション一覧をJSON化（Claude に正確なセクション名を使わせる）
    sections_list = json.dumps(
        [{"id": f"s{i+1}", "title": s} for i, s in enumerate(sections)],
        ensure_ascii=False,
    )

    # 素材リストを整理（Claudeに具体的な素材を参照させる）
    material_list_parts = []

    # 図解画像リスト
    if diagram_results:
        diagram_items = []
        for d in diagram_results:
            diagram_items.append(f"  - 図{d.get('index', '?')}: {d.get('section', '')}（{d.get('prompt', '')[:40]}）")
        material_list_parts.append(f"【図解画像】{len(diagram_results)}枚\n" + "\n".join(diagram_items))

    # リアル画像リスト
    if realistic_results:
        realistic_items = []
        for r in realistic_results:
            realistic_items.append(f"  - 写真{r.get('index', '?')}: {r.get('section', '')}（{r.get('prompt', '')[:40]}）")
        material_list_parts.append(f"【リアル画像】{len(realistic_results)}枚\n" + "\n".join(realistic_items))

    # YouTube動画リスト
    if youtube_results:
        yt_items = []
        for i, v in enumerate(youtube_results[:15], 1):
            yt_items.append(f"  - YT{i}: {v.get('title', '')}（{v.get('channel', '')}）")
        material_list_parts.append(f"【YouTube動画】{len(youtube_results)}件\n" + "\n".join(yt_items))

    # Web素材リスト
    if web_results:
        web_items = []
        for i, w in enumerate(web_results[:20], 1):
            web_items.append(f"  - Web{i}: {w.get('description', '')[:50]}（{w.get('section', '')}）")
        material_list_parts.append(f"【Web素材】{len(web_results)}件\n" + "\n".join(web_items))

    materials_text = "\n\n".join(material_list_parts)

    system = (
        "あなたはプロの動画ディレクター・演出家です。"
        "原稿と収集済み素材リストを分析して、動画編集者がすぐに作業できる具体的な演出プランを作成してください。"
        "結果はJSON形式のみで返してください。マークダウンやコードブロックは不要です。"
        "\n\n重要ルール:"
        "\n- 全ての値はプレーンテキスト（HTMLタグ禁止）"
        "\n- ダブルクォートの代わりに「」を使うこと"
        "\n- sectionsのtitleは指定されたセクション名をそのまま使うこと"
        "\n- material_placementでは必ず素材番号（図1、写真3、YT2、Web5など）を使って具体的に指示すること"
    )

    query = f"""以下の原稿と素材リストから、30分動画の演出プランをJSON形式で作成してください。
動画編集者が「どの素材をどのタイミングで使うか」すぐわかるよう、具体的に指示してください。

原稿（冒頭2000文字）:
{manuscript_text[:2000]}

セクション構成（このtitleをそのまま使うこと）:
{sections_list}

===== 収集済み素材一覧 =====
{materials_text}
=============================

以下のJSON形式で返してください（コードブロック不要、プレーンテキストのみ）:
{{
  "title": "キャッチーな動画タイトル",
  "overview": "動画全体の概要（200-300文字、プレーンテキスト）",
  "sections": [
    {{
      "id": "s1",
      "title": "上記セクション名をそのまま使う",
      "time_start": "00:00",
      "duration": "X分",
      "narration_summary": "このセクションのナレーション要約（100-200文字）",
      "visual_direction": "映像全体の方針（具体的な画面構成の指示）",
      "material_placement": [
        "0:00〜 オープニング: 図1（税制の仕組み）を全画面表示しながらナレーション開始",
        "0:30〜 写真3（宗教施設の外観）をバックに統計データをテロップ表示",
        "1:00〜 YT2の映像を参考資料として右下にワイプ表示",
        "1:30〜 Web5のグラフを画面中央に配置し、ポイントを矢印で強調"
      ],
      "bgm": "BGMの雰囲気・テンポ・楽器の指示",
      "telop": "テロップ・字幕の演出指示",
      "cut_notes": "カット割り・構図の指示",
      "transition": "次セクションへの繋ぎ方"
    }}
  ]
}}

ルール:
- sectionsは上記セクション構成と同じ数だけ作る（各セクション1つずつ）
- 合計30分になるよう各セクションのdurationを割り振る
- material_placementが最重要: 素材番号（図1、写真3、YT2、Web5など）を使って「いつ・どこに・どう配置するか」を3〜6個の指示として書く
- 各セクションで使える素材は上記リストから選ぶ。セクションに最も関連する素材を選択すること
- 全ての値はプレーンテキスト。HTMLタグは絶対に使わないこと"""

    # レート制限対策
    time.sleep(2)

    # 最大3回試行
    for attempt in range(3):
        print(f"  [演出] 試行 {attempt+1}/3 開始...", flush=True)
        result = claude_query(client, query, system, max_tokens=8000)
        if not result:
            print(f"  [WARN] 演出データ生成: 空の応答 (attempt {attempt+1})", flush=True)
            time.sleep(5)
            continue

        print(f"  [演出] 応答取得: {len(result)}文字", flush=True)

        data = parse_json_object(result)
        if data and data.get("sections"):
            print(f"  [演出] パース成功: sections={len(data['sections'])}", flush=True)
            return data

        print(f"  [WARN] 演出データ: パース失敗またはsections空 (attempt {attempt+1})", flush=True)
        time.sleep(5)

    # フォールバック: セクション構造だけでも生成
    print("  [WARN] 演出データ生成: フォールバックを使用", flush=True)
    total_minutes = 30
    per_section = total_minutes // max(len(sections), 1)
    fallback_sections = []
    for i, s in enumerate(sections):
        fallback_sections.append({
            "id": f"s{i+1}",
            "title": s,
            "time_start": f"{i * per_section:02d}:00",
            "duration": f"{per_section}分",
            "narration_summary": "",
            "visual_direction": "",
            "material_placement": [],
            "bgm": "",
            "telop": "",
            "cut_notes": "",
            "transition": "",
        })
    return {
        "title": title,
        "overview": summary or manuscript_text[:300],
        "sections": fallback_sections,
    }


def parse_json_array(text: str) -> list:
    """テキストからJSON配列を抽出"""
    text = text.strip()
    if "```json" in text:
        text = text.split("```json", 1)[1]
        if "```" in text:
            text = text.split("```", 1)[0]
        text = text.strip()
    elif "```" in text:
        parts = text.split("```")
        if len(parts) >= 3:
            text = parts[1].strip()
        elif len(parts) >= 2:
            text = parts[1].strip()

    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError as e:
            print(f"  [JSON PARSE ARRAY] Failed: {e}", flush=True)

    return []


def _repair_json_string(text: str) -> str:
    """JSON文字列内のエスケープされていないダブルクォートを修復"""
    result = []
    i = 0
    in_string = False
    string_start = -1

    while i < len(text):
        c = text[i]

        if not in_string:
            result.append(c)
            if c == '"':
                in_string = True
                string_start = i
        else:
            if c == '\\':
                # エスケープシーケンス - 次の文字もそのまま追加
                result.append(c)
                i += 1
                if i < len(text):
                    result.append(text[i])
            elif c == '"':
                # この " が文字列の終端かチェック
                # 次の文字が , : ] } 空白 のいずれかなら終端
                j = i + 1
                while j < len(text) and text[j] in ' \t\n\r':
                    j += 1
                if j >= len(text) or text[j] in ',:]}\n':
                    # 文字列の終端
                    result.append(c)
                    in_string = False
                else:
                    # 文字列内のエスケープされていないクォート → エスケープする
                    result.append('\\"')
            elif c == '\n':
                # 文字列内の改行をエスケープ
                result.append('\\n')
            else:
                result.append(c)

        i += 1

    return ''.join(result)


def parse_json_object(text: str) -> dict:
    """テキストからJSONオブジェクトを抽出（堅牢版）"""
    text = text.strip()

    # ```json ブロックの除去（複数パターン対応）
    if "```json" in text:
        text = text.split("```json", 1)[1]
        if "```" in text:
            text = text.split("```", 1)[0]
        text = text.strip()
    elif "```" in text:
        parts = text.split("```")
        if len(parts) >= 3:
            text = parts[1].strip()
        elif len(parts) >= 2:
            text = parts[1].strip()

    # 方法1: 最初の { から最後の } まで（そのまま）
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError as e:
            print(f"  [JSON PARSE] Method 1 failed: {e}", flush=True)

    # 方法2: JSON文字列のダブルクォートを修復してパース
    if start != -1 and end != -1 and end > start:
        try:
            repaired = _repair_json_string(text[start:end + 1])
            result = json.loads(repaired)
            print(f"  [JSON PARSE] Method 2 (repair) succeeded", flush=True)
            return result
        except json.JSONDecodeError as e:
            print(f"  [JSON PARSE] Method 2 (repair) failed: {e}", flush=True)

    # 方法3: 切り詰められたJSONの修復を試みる
    if start != -1:
        json_text = text[start:]
        for extra in ['"}]}', '"]}}', '"}', '}']:
            try:
                return json.loads(json_text + extra)
            except json.JSONDecodeError:
                pass
            # 修復も試す
            try:
                repaired = _repair_json_string(json_text + extra)
                result = json.loads(repaired)
                print(f"  [JSON PARSE] Method 3 (repair+extend) succeeded", flush=True)
                return result
            except json.JSONDecodeError:
                continue

    print(f"  [JSON PARSE] All methods failed. Text length={len(text)}, first 200 chars: {text[:200]}", flush=True)
    return {}
