#!/usr/bin/env python3
"""メインパイプライン - 全フェーズを統合実行"""

import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Optional

from scripts.research import (
    get_client,
    analyze_manuscript,
    research_twitter,
    research_youtube,
    research_web_data,
    generate_image_prompts,
    generate_direction_data,
)


class MaterialPipeline:
    """資料作成パイプライン"""

    def __init__(
        self,
        manuscript_path: str,
        output_dir: str,
        project_root: str,
        progress_callback: Optional[Callable] = None,
    ):
        self.manuscript_path = Path(manuscript_path)
        self.output_dir = Path(output_dir)
        self.project_root = Path(project_root)
        self.progress_callback = progress_callback or (lambda *a: None)

    def report(self, phase: int, message: str, percent: int):
        """進捗を報告"""
        print(f"  [Phase {phase}] {message} ({percent}%)")
        self.progress_callback(phase, message, percent)

    def run(self):
        """パイプライン全体を実行"""
        # ===== Phase 0: セットアップ =====
        self.report(0, "原稿を読み込み中...", 0)

        manuscript_text = self.manuscript_path.read_text(encoding="utf-8")
        if len(manuscript_text.strip()) < 100:
            raise ValueError("原稿が短すぎます（100文字以上必要）")

        # ディレクトリ作成
        (self.output_dir / "images" / "diagrams").mkdir(parents=True, exist_ok=True)
        (self.output_dir / "images" / "realistic").mkdir(parents=True, exist_ok=True)
        (self.output_dir / "research").mkdir(parents=True, exist_ok=True)
        (self.output_dir / "web_images").mkdir(parents=True, exist_ok=True)

        self.report(0, "原稿を分析中...", 3)
        client = get_client()
        analysis = analyze_manuscript(client, manuscript_text)

        title = analysis.get("title", "無題")
        keywords = analysis.get("keywords", [])
        sections = analysis.get("sections", [])
        summary = analysis.get("summary", manuscript_text[:200])

        self.report(0, f"分析完了: {title} (キーワード{len(keywords)}個, セクション{len(sections)}個)", 5)

        # ===== Phase 1: 並行リサーチ =====
        self.report(1, "4つの並行エージェント起動中...", 8)

        twitter_results = []
        youtube_results = []
        web_results = []
        diagram_prompts = []

        def task_twitter():
            self.report(1, "[Agent 1] X/Twitter投稿検索中...", 10)
            results = research_twitter(client, keywords, summary)
            save_json(self.output_dir / "research" / "twitter_results.json", {"results": results})
            self.report(1, f"[Agent 1] X/Twitter: {len(results)}件収集完了", 25)
            return results

        def task_youtube():
            self.report(1, "[Agent 2] YouTube動画検索中...", 12)
            results = research_youtube(client, keywords, summary)
            save_json(self.output_dir / "research" / "youtube_results.json", {"results": results})
            self.report(1, f"[Agent 2] YouTube: {len(results)}件収集完了", 25)
            return results

        def task_webdata():
            self.report(1, "[Agent 3] Web画像・データ収集中...", 14)
            results = research_web_data(client, keywords, summary, sections)
            save_json(self.output_dir / "web_images" / "web_images.json", {"results": results})
            self.report(1, f"[Agent 3] Web素材: {len(results)}件収集完了", 30)
            return results

        def task_diagrams():
            self.report(1, "[Agent 4] 図解プロンプト作成中...", 16)
            prompts = generate_image_prompts(client, manuscript_text, keywords, sections, "diagrams", 20)
            prompts_path = self.output_dir / "diagram_prompts.json"
            save_json(prompts_path, {"prompts": prompts})

            if prompts:
                self.report(1, f"[Agent 4] 図解画像{len(prompts)}枚生成中...", 20)
                self._run_image_generation("diagrams", 20, prompts_path)

            return prompts

        # 並行実行
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {
                executor.submit(task_twitter): "twitter",
                executor.submit(task_youtube): "youtube",
                executor.submit(task_webdata): "webdata",
                executor.submit(task_diagrams): "diagrams",
            }

            for future in as_completed(futures):
                name = futures[future]
                try:
                    result = future.result()
                    if name == "twitter":
                        twitter_results = result
                    elif name == "youtube":
                        youtube_results = result
                    elif name == "webdata":
                        web_results = result
                    elif name == "diagrams":
                        diagram_prompts = result
                except Exception as e:
                    print(f"  [ERROR] {name} タスク失敗: {e}")
                    import traceback
                    traceback.print_exc()

        self.report(1, "Phase 1 完了: 並行リサーチ終了", 45)

        # ===== Phase 2: 追加リアル画像生成 =====
        self.report(2, "追加画像プロンプト作成中...", 48)

        # 既存素材の概要をまとめる
        existing_summary = (
            f"Twitter {len(twitter_results)}件, "
            f"YouTube {len(youtube_results)}件, "
            f"Web素材 {len(web_results)}件, "
            f"図解 {len(diagram_prompts)}枚生成済み"
        )

        realistic_prompts = generate_image_prompts(
            client, manuscript_text, keywords, sections,
            "realistic", 30, existing_summary
        )
        realistic_prompts_path = self.output_dir / "realistic_prompts.json"
        save_json(realistic_prompts_path, {"prompts": realistic_prompts})

        if realistic_prompts:
            self.report(2, f"リアル画像{len(realistic_prompts)}枚生成中...", 52)
            self._run_image_generation("realistic", 30, realistic_prompts_path)

        self.report(2, "Phase 2 完了: 追加画像生成終了", 70)

        # ===== Phase 3: 演出エージェント =====
        self.report(3, "演出AIエージェント起動中...", 72)

        # マニフェスト読み込み
        diagram_manifest = load_json(self.output_dir / "images" / "diagrams" / "diagrams_manifest.json")
        realistic_manifest = load_json(self.output_dir / "images" / "realistic" / "realistic_manifest.json")

        diagram_results = diagram_manifest.get("results", [])
        realistic_results = realistic_manifest.get("results", [])

        self.report(3, "素材を統合・構成中...", 78)

        direction_data = generate_direction_data(
            client=client,
            manuscript_text=manuscript_text,
            sections=sections,
            twitter_count=len(twitter_results),
            youtube_count=len(youtube_results),
            web_count=len(web_results),
            diagram_count=len([d for d in diagram_results if d.get("success")]),
            realistic_count=len([d for d in realistic_results if d.get("success")]),
            twitter_data=twitter_results,
            youtube_data=youtube_results,
            web_data=web_results,
            diagram_manifest=diagram_results,
            realistic_manifest=realistic_results,
        )

        save_json(self.output_dir / "data.json", direction_data)
        self.report(3, "演出データ作成完了", 85)

        # HTML生成
        self.report(3, "HTML資料を生成中...", 88)
        self._build_html()

        self.report(3, "Phase 3 完了: 統合完了", 95)

        # ===== Phase 4: 完了 =====
        self.report(4, "全工程完了！", 100)

        print(f"\n{'='*60}")
        print(f"  資料作成完了！")
        print(f"  出力先: {self.output_dir}")
        print(f"  結果: {self.output_dir / 'index.html'}")
        print(f"  Twitter: {len(twitter_results)}件")
        print(f"  YouTube: {len(youtube_results)}件")
        print(f"  Web素材: {len(web_results)}件")
        print(f"  図解: {len([d for d in diagram_results if d.get('success')])}枚")
        print(f"  リアル画像: {len([d for d in realistic_results if d.get('success')])}枚")
        print(f"{'='*60}")

    def _run_image_generation(self, mode: str, count: int, prompts_path: Path):
        """画像生成スクリプトを実行"""
        output_subdir = self.output_dir / "images" / mode
        cmd = [
            sys.executable,
            str(self.project_root / "scripts" / "generate_images.py"),
            "--mode", mode,
            "--count", str(count),
            "--output-dir", str(output_subdir),
            "--prompts-file", str(prompts_path),
            "--project-root", str(self.project_root),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if result.stdout:
                print(result.stdout)
            if result.returncode != 0 and result.stderr:
                print(f"  [WARN] 画像生成エラー: {result.stderr[:200]}")
        except subprocess.TimeoutExpired:
            print(f"  [WARN] 画像生成タイムアウト (mode={mode})")
        except Exception as e:
            print(f"  [ERROR] 画像生成失敗: {e}")

    def _build_html(self):
        """HTML資料を生成"""
        cmd = [
            sys.executable,
            str(self.project_root / "scripts" / "build_html.py"),
            "--output-dir", str(self.output_dir),
            "--manuscript", str(self.manuscript_path),
            "--template-dir", str(self.project_root / "template"),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.stdout:
                print(result.stdout)
            if result.returncode != 0 and result.stderr:
                print(f"  [WARN] HTML生成エラー: {result.stderr[:200]}")
        except Exception as e:
            print(f"  [ERROR] HTML生成失敗: {e}")


def save_json(path: Path, data):
    """JSONファイルを保存"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_json(path: Path) -> dict:
    """JSONファイルを読み込み"""
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}
