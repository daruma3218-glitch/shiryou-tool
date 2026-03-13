#!/usr/bin/env python3
"""演出エージェントが生成したdata.jsonと各素材を統合してHTMLを生成する"""

import argparse
import base64
import json
import re
import sys
from io import BytesIO
from pathlib import Path
from datetime import datetime

try:
    from PIL import Image
except ImportError:
    Image = None

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


def _image_to_base64(image_path: Path, max_width: int = 800, quality: int = 75) -> str:
    """画像ファイルを圧縮してBase64データURIに変換する"""
    if not image_path.exists():
        return ""
    try:
        if Image is None:
            # Pillowがない場合はそのまま読み込み
            data = image_path.read_bytes()
            ext = image_path.suffix.lower()
            mime = "image/png" if ext == ".png" else "image/jpeg"
            return f"data:{mime};base64,{base64.b64encode(data).decode()}"

        img = Image.open(image_path)
        # RGBA→RGB変換（JPEG用）
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        # リサイズ（幅がmax_widthを超える場合）
        if img.width > max_width:
            ratio = max_width / img.width
            new_size = (max_width, int(img.height * ratio))
            img = img.resize(new_size, Image.LANCZOS)
        # JPEG圧縮してBase64化
        buffer = BytesIO()
        img.save(buffer, format="JPEG", quality=quality, optimize=True)
        b64 = base64.b64encode(buffer.getvalue()).decode()
        return f"data:image/jpeg;base64,{b64}"
    except Exception as e:
        print(f"  [WARN] 画像変換エラー ({image_path}): {e}")
        return ""


def _embed_images(output_dir: Path, diagram_images: list, realistic_images: list) -> None:
    """画像リストにBase64データURIを追加（in-place）"""
    print("  画像をBase64に変換中...")
    count = 0
    for img in diagram_images:
        if not img.get("success"):
            continue
        fpath = output_dir / "images" / "diagrams" / img.get("filename", "")
        data_uri = _image_to_base64(fpath, max_width=900, quality=70)
        if data_uri:
            img["image_data"] = data_uri
            count += 1
    for img in realistic_images:
        if not img.get("success"):
            continue
        fpath = output_dir / "images" / "realistic" / img.get("filename", "")
        data_uri = _image_to_base64(fpath, max_width=700, quality=65)
        if data_uri:
            img["image_data"] = data_uri
            count += 1
    print(f"  → {count}枚の画像を埋め込みました")


def _normalize(name: str) -> str:
    """セクション名を正規化（マッチング用）"""
    name = name.strip()
    name = re.sub(r'[：:、,。.「」『』【】（）()\s]+', '', name)
    return name.lower()


def _section_match_score(section_title: str, material_section: str) -> float:
    """セクション名のマッチングスコアを計算（0.0〜1.0）"""
    norm_sec = _normalize(section_title)
    norm_mat = _normalize(material_section)

    if not norm_sec or not norm_mat:
        return 0.0

    # 完全一致
    if norm_sec == norm_mat:
        return 1.0

    # 部分一致（一方が他方に含まれる）
    if norm_sec in norm_mat or norm_mat in norm_sec:
        return 0.8

    # 文字の重なり度合い
    sec_chars = set(norm_sec)
    mat_chars = set(norm_mat)
    if not sec_chars or not mat_chars:
        return 0.0
    overlap = len(sec_chars & mat_chars) / max(len(sec_chars), len(mat_chars))
    return overlap * 0.6


def _youtube_relevance(video: dict, section_title: str) -> float:
    """YouTube動画とセクション名の関連度スコア"""
    title = video.get("title", "")
    desc = video.get("description", "")
    video_text = _normalize(title + desc)
    sec_norm = _normalize(section_title)

    if not sec_norm or not video_text:
        return 0.0

    # セクション名の各文字がビデオテキストに含まれる割合
    sec_chars = list(sec_norm)
    match_count = sum(1 for c in sec_chars if c in video_text)
    return match_count / max(len(sec_chars), 1)


def map_materials_to_sections(
    sections: list,
    diagram_images: list,
    realistic_images: list,
    web_data: list,
    youtube_videos: list,
) -> list:
    """各セクションに素材を自動マッピングして返す"""
    enriched = []

    for section in sections:
        sec_title = section.get("title", "")
        materials = []

        # 図解画像をマッピング
        for img in diagram_images:
            if not img.get("success"):
                continue
            score = _section_match_score(sec_title, img.get("section", ""))
            if score >= 0.4:
                materials.append({
                    "type": "diagram",
                    "type_label": "図解",
                    "title": f"図{img.get('index', '')}: {img.get('section', '')}",
                    "image_path": f"images/diagrams/{img.get('filename', '')}",
                    "image_data": img.get("image_data", ""),
                    "thumbnail": "",
                    "url": "",
                    "description": img.get("prompt", "")[:80],
                    "_score": score,
                })

        # AI画像をマッピング
        for img in realistic_images:
            if not img.get("success"):
                continue
            score = _section_match_score(sec_title, img.get("section", ""))
            if score >= 0.4:
                materials.append({
                    "type": "realistic",
                    "type_label": "AI画像",
                    "title": img.get("section", ""),
                    "image_path": f"images/realistic/{img.get('filename', '')}",
                    "image_data": img.get("image_data", ""),
                    "thumbnail": "",
                    "url": "",
                    "description": img.get("prompt", "")[:80],
                    "_score": score,
                })

        # Web素材をマッピング
        for item in web_data:
            score = _section_match_score(sec_title, item.get("section", ""))
            if score >= 0.4:
                materials.append({
                    "type": "web",
                    "type_label": "Web",
                    "title": item.get("description", "")[:60],
                    "image_path": "",
                    "thumbnail": "",
                    "url": item.get("url", ""),
                    "description": item.get("description", ""),
                    "_score": score,
                })

        # YouTube動画をマッピング（キーワードマッチ、上位3件まで）
        yt_scored = []
        for video in youtube_videos:
            score = _youtube_relevance(video, sec_title)
            if score >= 0.4:
                yt_scored.append((score, video))
        yt_scored.sort(key=lambda x: x[0], reverse=True)
        for score, video in yt_scored[:3]:
            if score >= 0.4:
                materials.append({
                    "type": "youtube",
                    "type_label": "YouTube",
                    "title": video.get("title", ""),
                    "image_path": "",
                    "thumbnail": video.get("thumbnail", ""),
                    "url": video.get("url", ""),
                    "description": video.get("channel", ""),
                    "_score": score,
                })

        # スコア順にソート（高い順）、スコアフィールドを削除
        materials.sort(key=lambda m: m.get("_score", 0), reverse=True)
        for m in materials:
            m.pop("_score", None)

        enriched_section = dict(section)
        enriched_section["materials"] = materials
        enriched.append(enriched_section)

    return enriched


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
    youtube_data = load_json(output_dir / "research" / "youtube_results.json", {})
    web_images_data = load_json(output_dir / "web_images" / "web_images.json", {})
    diagrams_manifest = load_json(output_dir / "images" / "diagrams" / "diagrams_manifest.json", {})
    realistic_manifest = load_json(output_dir / "images" / "realistic" / "realistic_manifest.json", {})

    # データ整理
    youtube_videos = youtube_data.get("results", [])
    web_data = web_images_data.get("results", [])
    diagram_images = diagrams_manifest.get("results", [])
    realistic_images = realistic_manifest.get("results", [])

    # 演出データから抽出
    title = data.get("title", "動画素材集")
    # 新旧フォーマット対応
    overview = data.get("overview", data.get("overview_html", f"{manuscript_text[:500]}..."))
    if not overview.strip().startswith("<"):
        overview = f"<p>{overview}</p>"
    sections = data.get("sections", [])

    # 画像をBase64に変換（HTML単体で画像表示可能にする）
    _embed_images(output_dir, diagram_images, realistic_images)

    # 素材を自動マッピング
    enriched_sections = map_materials_to_sections(
        sections, diagram_images, realistic_images, web_data, youtube_videos
    )

    # テンプレートレンダリング
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=False,
    )
    template = env.get_template("index.html")

    # job_idはoutput_dirのディレクトリ名
    job_id = output_dir.name

    html = template.render(
        title=title,
        job_id=job_id,
        generated_date=datetime.now().strftime("%Y年%m月%d日 %H:%M"),
        overview_html=overview,
        sections=enriched_sections,
        youtube_videos=youtube_videos,
        web_data=web_data,
        diagram_images=diagram_images,
        realistic_images=realistic_images,
        youtube_count=len(youtube_videos),
        webdata_count=len(web_data),
        diagrams_count=len([i for i in diagram_images if i.get("success")]),
        realistic_count=len([i for i in realistic_images if i.get("success")]),
    )

    # HTML出力
    html_path = output_dir / "index.html"
    html_path.write_text(html, encoding="utf-8")

    total_materials = sum(len(s.get("materials", [])) for s in enriched_sections)
    print(f"\n{'='*60}")
    print(f"  HTML資料生成完了")
    print(f"  出力: {html_path}")
    print(f"  YouTube: {len(youtube_videos)}件")
    print(f"  Web素材: {len(web_data)}件")
    print(f"  図解: {len([i for i in diagram_images if i.get('success')])}枚")
    print(f"  AI画像: {len([i for i in realistic_images if i.get('success')])}枚")
    print(f"  セクション: {len(enriched_sections)}個 (素材マッピング: {total_materials}件)")
    print(f"{'='*60}")

    return str(html_path)


if __name__ == "__main__":
    main()
