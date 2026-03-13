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


def _find_section_position(manuscript_text: str, section_title: str) -> int:
    """原稿テキスト内でセクションタイトルに最も近い行の位置を探す"""
    lines = manuscript_text.split("\n")
    norm_title = _normalize(section_title)

    # タイトルからキーフレーズを抽出（記号除去・正規化済み）
    # 長いキーフレーズを優先的に検索
    key_phrases = []
    # 「：」や「——」で分割してキーフレーズを取得
    parts = re.split(r'[：:——\-]+', section_title)
    for part in parts:
        cleaned = part.strip()
        if len(cleaned) >= 4:
            key_phrases.append(cleaned)
            # さらに前半だけのサブフレーズも追加（「そもそも」等の挿入対策）
            if len(cleaned) >= 8:
                key_phrases.append(cleaned[:len(cleaned)//2])

    # 原稿の各行をチェック
    best_pos = -1
    best_score = 0.0

    for i, line in enumerate(lines):
        line_stripped = line.strip()
        if not line_stripped:
            continue

        norm_line = _normalize(line_stripped)

        # キーフレーズが行に含まれるかチェック
        for phrase in key_phrases:
            norm_phrase = _normalize(phrase)
            if norm_phrase and len(norm_phrase) >= 3 and norm_phrase in norm_line:
                score = len(norm_phrase) / max(len(norm_title), 1)
                if score > best_score:
                    best_score = score
                    best_pos = i

        # 正規化後の部分一致チェック
        if best_pos < 0 and norm_title:
            # タイトルの前半部分で検索
            title_prefix = norm_title[:min(len(norm_title), 10)]
            if title_prefix in norm_line:
                best_pos = i
                best_score = 0.5

    return best_pos


def _split_manuscript_by_sections(manuscript_text: str, sections: list) -> list:
    """原稿テキストをセクションごとに分割し、各セクションに原稿テキストを追加する"""
    if not manuscript_text or not sections:
        return sections

    lines = manuscript_text.split("\n")

    # 各セクションの原稿内での開始位置を特定
    section_positions = []
    for section in sections:
        title = section.get("title", "")
        pos = _find_section_position(manuscript_text, title)
        section_positions.append(pos)

    # 密集した位置を解消する（リスト形式の目次行に複数セクションが重なる場合）
    # 3行以内に密集しているセクション群を検出し、後続の内容範囲を均等に分割する
    sorted_by_pos = sorted(
        [(i, p) for i, p in enumerate(section_positions) if p >= 0],
        key=lambda x: x[1],
    )

    # 密集グループを検出
    clusters = []  # [(start_vi, end_vi), ...]
    i = 0
    while i < len(sorted_by_pos):
        j = i
        while j + 1 < len(sorted_by_pos) and sorted_by_pos[j + 1][1] - sorted_by_pos[j][1] <= 3:
            j += 1
        if j > i:
            # i〜jが密集グループ
            clusters.append((i, j))
        i = j + 1

    for cl_start, cl_end in clusters:
        cluster_items = sorted_by_pos[cl_start:cl_end + 1]
        cluster_min_pos = cluster_items[0][1]
        # 密集グループの後のコンテンツ開始位置（目次リストの直後）
        content_start = cluster_items[-1][1] + 1
        # コンテンツ終了位置（次の非密集セクションの開始位置）
        if cl_end + 1 < len(sorted_by_pos):
            content_end = sorted_by_pos[cl_end + 1][1]
        else:
            content_end = len(lines)

        # コンテンツ範囲を密集グループのセクション数で均等分割
        content_lines = content_end - content_start
        n_sections = len(cluster_items)
        per_section = max(content_lines // n_sections, 1)

        for ci, (sec_idx, _) in enumerate(cluster_items):
            new_pos = content_start + ci * per_section
            section_positions[sec_idx] = new_pos

    # 有効な位置を持つセクションのペアを作成
    indexed = [(i, pos) for i, pos in enumerate(section_positions)]
    # 位置が見つかったものだけを位置順にソート
    valid = sorted([(i, p) for i, p in indexed if p >= 0], key=lambda x: x[1])

    # 各セクションに原稿テキストを割り当て
    result = []
    for section in sections:
        result.append(dict(section))

    for vi, (sec_idx, start_pos) in enumerate(valid):
        # 終了位置: 次の有効なセクションの開始位置、または原稿の末尾
        if vi + 1 < len(valid):
            end_pos = valid[vi + 1][1]
        else:
            end_pos = len(lines)

        # セクションの原稿テキストを切り出し
        excerpt_lines = lines[start_pos:end_pos]
        # 末尾の空行を除去
        while excerpt_lines and not excerpt_lines[-1].strip():
            excerpt_lines.pop()
        excerpt = "\n".join(excerpt_lines)
        result[sec_idx]["manuscript_excerpt"] = excerpt

    # 位置が見つからなかったセクションには空文字を設定
    for sec in result:
        if "manuscript_excerpt" not in sec:
            sec["manuscript_excerpt"] = ""

    matched = sum(1 for p in section_positions if p >= 0)
    print(f"  原稿テキスト分割: {matched}/{len(sections)}セクションにマッチ")

    return result


def _is_fallback_placement(placements: list) -> bool:
    """material_placementが汎用フォールバックかどうか判定"""
    if not placements:
        return True
    # すべてのセクションで同一の配置指示の場合はフォールバック
    fallback_patterns = [
        "図1を全画面表示",
        "写真2を背景に",
        "Web素材3のグラフ",
    ]
    for p in placements:
        for pattern in fallback_patterns:
            if pattern in p:
                return True
    return False


def _generate_material_placements(enriched_sections: list) -> None:
    """マッピング済み素材から具体的な配置指示を自動生成（in-place）"""
    print("  素材配置タイムラインを生成中...")
    count = 0

    for section in enriched_sections:
        existing = section.get("material_placement", [])
        # フォールバックでない場合はスキップ（Claudeが具体的な指示を生成済み）
        if existing and not _is_fallback_placement(existing):
            continue

        materials = section.get("materials", [])
        if not materials:
            section["material_placement"] = []
            continue

        placements = []

        # 素材タイプ別にグループ化
        diagrams = [m for m in materials if m["type"] == "diagram"]
        ai_images = [m for m in materials if m["type"] == "realistic"]
        web_mats = [m for m in materials if m["type"] == "web"]
        youtube_mats = [m for m in materials if m["type"] == "youtube"]

        # 図解の配置指示
        for i, d in enumerate(diagrams[:5]):
            title = d.get("title", "")
            desc = d.get("description", "")[:40]
            if i == 0:
                placements.append(f"{title} をセクション冒頭で全画面表示しながらナレーション")
            else:
                placements.append(f"{title} を説明に合わせて表示")

        # AI画像の配置指示
        for i, img in enumerate(ai_images[:3]):
            desc = img.get("description", "")[:50]
            if desc:
                placements.append(f"AI画像（{desc}）を背景映像として使用")
            else:
                placements.append(f"AI画像をカットシーンの背景として挿入")

        # Web素材の配置指示
        for w in web_mats[:2]:
            desc = w.get("description", "")[:50]
            if desc:
                placements.append(f"Web素材（{desc}）のデータを図表として活用")

        # YouTube動画の配置指示
        for yt in youtube_mats[:1]:
            title = yt.get("title", "")[:40]
            if title:
                placements.append(f"参考映像「{title}」を参照可能")

        section["material_placement"] = placements
        count += 1

    print(f"  → {count}セクションの配置指示を生成しました")


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

    # 原稿テキストをセクションごとに分割
    sections = _split_manuscript_by_sections(manuscript_text, sections)

    # 画像をBase64に変換（HTML単体で画像表示可能にする）
    _embed_images(output_dir, diagram_images, realistic_images)

    # 素材を自動マッピング
    enriched_sections = map_materials_to_sections(
        sections, diagram_images, realistic_images, web_data, youtube_videos
    )

    # 素材配置タイムラインを自動生成（フォールバックの場合のみ）
    _generate_material_placements(enriched_sections)

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
