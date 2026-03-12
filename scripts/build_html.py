#!/usr/bin/env python3
"""演出エージェントが生成したdata.jsonと各素材を統合してHTMLを生成する"""

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime

try:
    from jinja2 import Environment, FileSystemLoader
except ImportError:
    print("ERROR: jinja2 がインストールされていません。", file=sys.stderr)
    print("  pip3 install jinja2 を実行してください。", file=sys.stderr)
    sys.exit(1)


def load_json(path: Path, default=None):
    """JSONファイルを安全に読み込む"""
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"  [WARN] JSON読み込みエラー ({path}): {e}")
    return default if default is not None else {}


def main():
    parser = argparse.ArgumentParser(description="HTML資料生成ツール")
    parser.add_argument("--output-dir", required=True, help="出力ディレクトリ")
    parser.add_argument("--manuscript", required=True, help="原稿ファイルパス")
    parser.add_argument("--template-dir", default=None, help="テンプレートディレクトリ")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    project_root = Path(__file__).parent.parent

    # テンプレートディレクトリ
    template_dir = Path(args.template_dir) if args.template_dir else project_root / "template"
    if not (template_dir / "index.html").exists():
        print(f"ERROR: テンプレートが見つかりません: {template_dir / 'index.html'}", file=sys.stderr)
        sys.exit(1)

    # 原稿読み込み
    manuscript_path = Path(args.manuscript)
    manuscript_text = ""
    if manuscript_path.exists():
        manuscript_text = manuscript_path.read_text(encoding="utf-8")

    # 演出エージェントのデータ読み込み
    data = load_json(output_dir / "data.json", {})

    # 各素材データ読み込み
    twitter_data = load_json(output_dir / "research" / "twitter_results.json", {})
    youtube_data = load_json(output_dir / "research" / "youtube_results.json", {})
    web_images_data = load_json(output_dir / "web_images" / "web_images.json", {})
    diagrams_manifest = load_json(output_dir / "images" / "diagrams" / "diagrams_manifest.json", {})
    realistic_manifest = load_json(output_dir / "images" / "realistic" / "realistic_manifest.json", {})

    # データ整理
    twitter_posts = twitter_data.get("results", [])
    youtube_videos = youtube_data.get("results", [])
    web_data = web_images_data.get("results", [])
    diagram_images = diagrams_manifest.get("results", [])
    realistic_images = realistic_manifest.get("results", [])

    # 演出データから抽出
    title = data.get("title", "動画素材集")
    overview_html = data.get("overview_html", f"<p>{manuscript_text[:500]}...</p>")
    timeline = data.get("timeline", [])
    sections = data.get("sections", [])
    direction_notes_html = data.get("direction_notes_html", "<p>演出メモはありません</p>")

    # テンプレートレンダリング
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=False,  # HTMLを直接埋め込むため
    )
    template = env.get_template("index.html")

    html = template.render(
        title=title,
        generated_date=datetime.now().strftime("%Y年%m月%d日 %H:%M"),
        overview_html=overview_html,
        timeline=timeline,
        sections=sections,
        twitter_posts=twitter_posts,
        youtube_videos=youtube_videos,
        web_data=web_data,
        diagram_images=diagram_images,
        realistic_images=realistic_images,
        direction_notes_html=direction_notes_html,
        twitter_count=len(twitter_posts),
        youtube_count=len(youtube_videos),
        webdata_count=len(web_data),
        diagrams_count=len([i for i in diagram_images if i.get("success")]),
        realistic_count=len([i for i in realistic_images if i.get("success")]),
    )

    # HTML出力
    html_path = output_dir / "index.html"
    html_path.write_text(html, encoding="utf-8")

    print(f"\n{'='*60}")
    print(f"  HTML資料生成完了")
    print(f"  出力: {html_path}")
    print(f"  Twitter: {len(twitter_posts)}件")
    print(f"  YouTube: {len(youtube_videos)}件")
    print(f"  Web素材: {len(web_data)}件")
    print(f"  図解: {len([i for i in diagram_images if i.get('success')])}枚")
    print(f"  リアル画像: {len([i for i in realistic_images if i.get('success')])}枚")
    print(f"  セクション: {len(sections)}個")
    print(f"{'='*60}")

    return str(html_path)


if __name__ == "__main__":
    main()
