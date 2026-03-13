#!/usr/bin/env python3
"""Gemini APIを使った画像生成スクリプト（図解 / AI画像）"""

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path

from google import genai
from google.genai import types


def load_api_key(project_root: Path) -> str:
    """プロジェクトルートの .env から GEMINI_API_KEY を読み込む"""
    env_path = project_root / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("GEMINI_API_KEY=") and not line.endswith("="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
                if key and key != "your_api_key_here":
                    return key
    return os.environ.get("GEMINI_API_KEY", "")


def build_prompt(user_prompt: str, mode: str) -> str:
    """モードに応じたシステムプレフィックスを付加"""
    if mode == "diagrams":
        prefix = (
            "Create a clean, simple infographic or diagram image. "
            "Use a unified color palette: primary blue (#2563EB), "
            "white (#FFFFFF), dark gray (#1F2937), and light gray (#F3F4F6). "
            "The design should be minimal, professional, and easy to read "
            "at a glance. Use large, bold text for key numbers and labels. "
            "IMPORTANT: All text, labels, titles, and annotations in the image MUST be in Japanese (日本語). "
            "Aspect ratio should be 16:9 for video presentation. "
        )
    else:  # realistic
        prefix = (
            "Create a photorealistic, high-quality image "
            "suitable for use in a professional video production. "
            "The image should be cinematic, well-lit, and visually compelling. "
            "Aspect ratio should be 16:9 for video presentation. "
        )
    return prefix + user_prompt


IMAGE_MODEL = "gemini-3.1-flash-image-preview"


def generate_single_image(
    client: genai.Client, prompt: str, output_path: Path, mode: str, max_retries: int = 3
) -> bool:
    """1枚の画像を生成して保存。リトライ付き。"""
    full_prompt = build_prompt(prompt, mode)

    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model=IMAGE_MODEL,
                contents=full_prompt,
                config=types.GenerateContentConfig(
                    response_modalities=["TEXT", "IMAGE"],
                ),
            )

            # 新しいSDK形式: response.parts を直接使う
            if hasattr(response, "parts") and response.parts:
                for part in response.parts:
                    if hasattr(part, "inline_data") and part.inline_data and part.inline_data.mime_type.startswith("image/"):
                        img_data = part.inline_data.data
                        if isinstance(img_data, str):
                            img_data = base64.b64decode(img_data)
                        output_path.write_bytes(img_data)
                        return True

            # フォールバック: candidates形式
            if hasattr(response, "candidates") and response.candidates:
                for part in response.candidates[0].content.parts:
                    if hasattr(part, "inline_data") and part.inline_data and part.inline_data.mime_type.startswith("image/"):
                        img_data = part.inline_data.data
                        if isinstance(img_data, str):
                            img_data = base64.b64decode(img_data)
                        output_path.write_bytes(img_data)
                        return True

            print(f"  [WARN] No image in response for: {prompt[:50]}...")
            # レスポンスの内容をデバッグ出力
            if hasattr(response, "parts") and response.parts:
                for part in response.parts:
                    if hasattr(part, "text") and part.text:
                        print(f"  [DEBUG] Text response: {part.text[:200]}")
            return False

        except Exception as e:
            err_str = str(e)
            print(f"  [ERROR] Attempt {attempt + 1}/{max_retries}: {err_str[:300]}")
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                wait_time = 30 * (attempt + 1)
                print(f"  [RATE LIMIT] Waiting {wait_time}s...")
                time.sleep(wait_time)
            elif "safety" in err_str.lower() or "block" in err_str.lower():
                print(f"  [BLOCKED] Safety filter blocked: {prompt[:50]}...")
                return False
            elif "not found" in err_str.lower() or "404" in err_str:
                print(f"  [MODEL ERROR] Model '{IMAGE_MODEL}' not available. Check model name.")
                return False
            else:
                if attempt == max_retries - 1:
                    return False
                time.sleep(5)

    return False


def main():
    parser = argparse.ArgumentParser(description="Gemini画像生成ツール")
    parser.add_argument("--mode", choices=["diagrams", "realistic"], required=True,
                        help="生成モード: diagrams=図解, realistic=AI画像")
    parser.add_argument("--count", type=int, required=True,
                        help="生成する画像の枚数")
    parser.add_argument("--output-dir", required=True,
                        help="画像の出力先ディレクトリ")
    parser.add_argument("--prompts-file", required=True,
                        help="プロンプトJSONファイルのパス")
    parser.add_argument("--delay", type=float, default=5.0,
                        help="画像生成間の待機秒数（レート制限対策、デフォルト: 5秒）")
    parser.add_argument("--project-root", default=None,
                        help="プロジェクトルートディレクトリ（.envの場所）")
    args = parser.parse_args()

    # プロジェクトルート特定
    project_root = Path(args.project_root) if args.project_root else Path(__file__).parent.parent

    # APIキー読み込み
    api_key = load_api_key(project_root)
    if not api_key:
        print("ERROR: GEMINI_API_KEY が設定されていません。", file=sys.stderr)
        print("  .env ファイルに GEMINI_API_KEY=your_key を設定してください。", file=sys.stderr)
        sys.exit(1)

    # クライアント初期化
    client = genai.Client(api_key=api_key)

    # 出力ディレクトリ作成
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # プロンプト読み込み
    prompts_path = Path(args.prompts_file)
    if not prompts_path.exists():
        print(f"ERROR: プロンプトファイルが見つかりません: {prompts_path}", file=sys.stderr)
        sys.exit(1)

    with open(prompts_path, "r", encoding="utf-8") as f:
        prompts_data = json.load(f)

    prompts = prompts_data["prompts"][:args.count]
    results = []

    print(f"\n{'='*60}")
    print(f"  Gemini 画像生成開始 [{args.mode}]")
    print(f"  生成枚数: {len(prompts)}")
    print(f"  出力先: {output_dir}")
    print(f"{'='*60}\n")

    # 進捗ファイル（パイプラインがポーリングする）
    progress_path = output_dir / f"{args.mode}_progress.json"

    for i, prompt_entry in enumerate(prompts):
        prompt_text = prompt_entry["prompt"]
        section = prompt_entry.get("section", "")
        filename = f"{args.mode}_{i+1:03d}.png"
        output_path = output_dir / filename

        print(f"[{i+1}/{len(prompts)}] {section}")
        print(f"  Prompt: {prompt_text[:80]}...")

        # 進捗を書き込み
        progress = {"current": i + 1, "total": len(prompts), "section": section, "status": "generating"}
        progress_path.write_text(json.dumps(progress, ensure_ascii=False), encoding="utf-8")

        success = generate_single_image(client, prompt_text, output_path, args.mode)

        status = "OK" if success else "FAILED"
        print(f"  -> {status}: {filename}\n")

        results.append({
            "index": i + 1,
            "prompt": prompt_text,
            "section": section,
            "filename": filename,
            "success": success,
        })

        # 進捗を更新
        progress["status"] = "ok" if success else "failed"
        progress_path.write_text(json.dumps(progress, ensure_ascii=False), encoding="utf-8")

        # レート制限対策
        if i < len(prompts) - 1:
            time.sleep(args.delay)

    # マニフェスト保存
    manifest_path = output_dir / f"{args.mode}_manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump({"mode": args.mode, "results": results}, f, ensure_ascii=False, indent=2)

    # サマリー
    success_count = sum(1 for r in results if r["success"])
    fail_count = len(results) - success_count
    print(f"{'='*60}")
    print(f"  完了: {success_count}/{len(prompts)} 枚生成成功")
    if fail_count > 0:
        print(f"  失敗: {fail_count} 枚")
    print(f"  マニフェスト: {manifest_path}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
