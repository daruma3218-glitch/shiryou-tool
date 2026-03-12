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
        log_callback: Optional[Callable] = None,
        agent_callback: Optional[Callable] = None,
    ):
        self.manuscript_path = Path(manuscript_path)
        self.output_dir = Path(output_dir)
        self.project_root = Path(project_root)
        self.progress_callback = progress_callback or (lambda *a: None)
        self.log_callback = log_callback or (lambda *a, **kw: None)
        self.agent_callback = agent_callback or (lambda *a, **kw: None)

    def report(self, phase: int, message: str, percent: int):
        """進捗を報告"""
        print(f"  [Phase {phase}] {message} ({percent}%)")
        self.progress_callback(phase, message, percent)

    def log(self, category: str, message: str, detail: str = ""):
        """詳細ログを記録"""
        print(f"  [{category}] {message}" + (f" - {detail}" if detail else ""))
        self.log_callback(category, message, detail)

    def agent(self, agent_id: str, status: str, message: str, count: int = 0, total: int = 0):
        """エージェント状態を更新"""
        self.agent_callback(agent_id, status, message, count, total)

    def run(self):
        """パイプライン全体を実行"""
        # ===== Phase 0: セットアップ =====
        self.report(0, "原稿を読み込み中...", 0)
        self.log("setup", "原稿ファイルを読み込み中...")

        manuscript_text = self.manuscript_path.read_text(encoding="utf-8")
        if len(manuscript_text.strip()) < 100:
            raise ValueError("原稿が短すぎます（100文字以上必要）")

        self.log("setup", f"原稿読み込み完了", f"{len(manuscript_text)}文字")

        # ディレクトリ作成
        (self.output_dir / "images" / "diagrams").mkdir(parents=True, exist_ok=True)
        (self.output_dir / "images" / "realistic").mkdir(parents=True, exist_ok=True)
        (self.output_dir / "research").mkdir(parents=True, exist_ok=True)
        (self.output_dir / "web_images").mkdir(parents=True, exist_ok=True)

        self.report(0, "原稿を分析中...", 3)
        self.log("ai", "Claude AIで原稿を分析中...", "テーマ・キーワード・セクション抽出")
        self.agent("analyze", "running", "原稿を分析中...")
        client = get_client()
        analysis = analyze_manuscript(client, manuscript_text)

        title = analysis.get("title", "無題")
        keywords = analysis.get("keywords", [])
        sections = analysis.get("sections", [])
        summary = analysis.get("summary", manuscript_text[:200])

        self.agent("analyze", "completed", f"「{title}」キーワード{len(keywords)}個, セクション{len(sections)}個")
        self.log("ai", f"原稿分析完了: 「{title}」", f"キーワード{len(keywords)}個, セクション{len(sections)}個")
        self.report(0, f"分析完了: {title} (キーワード{len(keywords)}個, セクション{len(sections)}個)", 5)

        # ===== Phase 1: 並行リサーチ =====
        self.report(1, "4つの並行エージェント起動中...", 8)
        self.log("system", "4つの並行エージェントを起動します")

        # エージェント初期状態を設定
        self.agent("twitter", "running", "X/Twitter投稿を検索中...")
        self.agent("youtube", "running", "YouTube動画を検索中...")
        self.agent("web", "running", "Web素材を収集中...")
        self.agent("diagrams", "running", "図解プロンプトを作成中...")
        self.agent("realistic", "waiting", "Phase 2で開始予定")
        self.agent("direction", "waiting", "Phase 3で開始予定")

        twitter_results = []
        youtube_results = []
        web_results = []
        diagram_prompts = []

        def task_twitter():
            self.report(1, "[Agent 1] X/Twitter投稿検索中...", 10)
            self.log("twitter", "X/Twitter投稿の検索を開始", f"キーワード: {', '.join(keywords[:3])}")
            self.agent("twitter", "running", f"キーワード「{', '.join(keywords[:3])}」で検索中...")
            results = research_twitter(client, keywords, summary)
            save_json(self.output_dir / "research" / "twitter_results.json", {"results": results})
            self.agent("twitter", "completed", f"{len(results)}件の投稿を収集", count=len(results))
            self.log("twitter", f"X/Twitter検索完了: {len(results)}件の投稿を収集")
            self.report(1, f"[Agent 1] X/Twitter: {len(results)}件収集完了", 25)
            return results

        def task_youtube():
            self.report(1, "[Agent 2] YouTube動画検索中...", 12)
            self.log("youtube", "YouTube動画の検索を開始", f"キーワード: {', '.join(keywords[:3])}")
            self.agent("youtube", "running", f"キーワード「{', '.join(keywords[:3])}」で検索中...")
            results = research_youtube(client, keywords, summary)
            save_json(self.output_dir / "research" / "youtube_results.json", {"results": results})
            self.agent("youtube", "completed", f"{len(results)}件の動画を収集", count=len(results))
            self.log("youtube", f"YouTube検索完了: {len(results)}件の動画を収集")
            self.report(1, f"[Agent 2] YouTube: {len(results)}件収集完了", 25)
            return results

        def task_webdata():
            self.report(1, "[Agent 3] Web画像・データ収集中...", 14)
            self.log("web", "Web画像・データの収集を開始", f"目標: 40件")
            self.agent("web", "running", "画像・データ・統計を収集中...", total=40)
            results = research_web_data(client, keywords, summary, sections)
            save_json(self.output_dir / "web_images" / "web_images.json", {"results": results})
            self.agent("web", "completed", f"{len(results)}件の素材を収集", count=len(results), total=40)
            self.log("web", f"Web素材収集完了: {len(results)}件", "画像URL・統計データ・引用を収集")
            self.report(1, f"[Agent 3] Web素材: {len(results)}件収集完了", 30)
            return results

        def task_diagrams():
            self.report(1, "[Agent 4] 図解プロンプト作成中...", 16)
            self.log("image", "図解画像のプロンプト作成を開始", "Claudeでプロンプト生成中")
            self.agent("diagrams", "running", "Claudeでプロンプトを作成中...")
            prompts = generate_image_prompts(client, manuscript_text, keywords, sections, "diagrams", 20)
            prompts_path = self.output_dir / "diagram_prompts.json"
            save_json(prompts_path, {"prompts": prompts})
            self.log("image", f"図解プロンプト{len(prompts)}件作成完了")

            if prompts:
                self.agent("diagrams", "running", f"Geminiで{len(prompts)}枚生成中...", count=0, total=len(prompts))
                self.report(1, f"[Agent 4] 図解画像{len(prompts)}枚生成中...", 20)
                self.log("image", f"Geminiで図解画像{len(prompts)}枚の生成を開始", "カラーパレット: 青/白/ダークグレー")
                self._run_image_generation("diagrams", 20, prompts_path)
                self.log("image", "図解画像の生成処理が完了")

            # 最終結果を確認
            manifest = load_json(self.output_dir / "images" / "diagrams" / "diagrams_manifest.json")
            ok_count = len([r for r in manifest.get("results", []) if r.get("success")])
            self.agent("diagrams", "completed", f"{ok_count}枚の図解を生成", count=ok_count, total=len(prompts))
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
                    agent_map = {"twitter": "twitter", "youtube": "youtube", "webdata": "web", "diagrams": "diagrams"}
                    self.agent(agent_map.get(name, name), "error", str(e)[:100])
                    self.log("error", f"{name} タスクでエラー発生", str(e))
                    print(f"  [ERROR] {name} タスク失敗: {e}")
                    import traceback
                    traceback.print_exc()

        self.log("system", "Phase 1 完了", f"Twitter {len(twitter_results)}件, YouTube {len(youtube_results)}件, Web {len(web_results)}件, 図解 {len(diagram_prompts)}枚")
        self.report(1, "Phase 1 完了: 並行リサーチ終了", 45)

        # ===== Phase 2: 追加リアル画像生成 =====
        self.report(2, "追加画像プロンプト作成中...", 48)
        self.log("ai", "リアル画像のプロンプトを作成中...", "収集済み素材を分析して不足を補う画像を設計")
        self.agent("realistic", "running", "プロンプトを作成中...")

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
        self.log("image", f"リアル画像プロンプト{len(realistic_prompts)}件作成完了")

        if realistic_prompts:
            self.agent("realistic", "running", f"Geminiで{len(realistic_prompts)}枚生成中...", count=0, total=len(realistic_prompts))
            self.report(2, f"リアル画像{len(realistic_prompts)}枚生成中...", 52)
            self.log("image", f"Geminiでリアル画像{len(realistic_prompts)}枚の生成を開始", "フォトリアリスティック品質")
            self._run_image_generation("realistic", 30, realistic_prompts_path)
            self.log("image", "リアル画像の生成処理が完了")

        # 最終結果を確認
        r_manifest = load_json(self.output_dir / "images" / "realistic" / "realistic_manifest.json")
        r_ok = len([r for r in r_manifest.get("results", []) if r.get("success")])
        self.agent("realistic", "completed", f"{r_ok}枚のリアル画像を生成", count=r_ok, total=len(realistic_prompts))

        self.log("system", "Phase 2 完了")
        self.report(2, "Phase 2 完了: 追加画像生成終了", 70)

        # ===== Phase 3: 演出エージェント =====
        self.report(3, "演出AIエージェント起動中...", 72)
        self.log("ai", "演出AIエージェントを起動", "全素材を統合して動画編集者向けの構成を設計")
        self.agent("direction", "running", "全素材を読み込み中...")

        # マニフェスト読み込み
        diagram_manifest = load_json(self.output_dir / "images" / "diagrams" / "diagrams_manifest.json")
        realistic_manifest = load_json(self.output_dir / "images" / "realistic" / "realistic_manifest.json")

        diagram_results = diagram_manifest.get("results", [])
        realistic_results = realistic_manifest.get("results", [])

        diagram_ok = len([d for d in diagram_results if d.get("success")])
        realistic_ok = len([d for d in realistic_results if d.get("success")])
        self.log("system", "素材の集計",
                 f"Twitter {len(twitter_results)}件, YouTube {len(youtube_results)}件, "
                 f"Web {len(web_results)}件, 図解 {diagram_ok}枚, リアル画像 {realistic_ok}枚")

        self.agent("direction", "running", "素材を統合・タイムラインを構成中...")
        self.report(3, "素材を統合・構成中...", 78)
        self.log("ai", "Claudeが素材を統合・構成中...", "タイムライン・演出メモ・セクション対応を作成")

        direction_data = generate_direction_data(
            client=client,
            manuscript_text=manuscript_text,
            sections=sections,
            twitter_count=len(twitter_results),
            youtube_count=len(youtube_results),
            web_count=len(web_results),
            diagram_count=diagram_ok,
            realistic_count=realistic_ok,
            twitter_data=twitter_results,
            youtube_data=youtube_results,
            web_data=web_results,
            diagram_manifest=diagram_results,
            realistic_manifest=realistic_results,
        )

        save_json(self.output_dir / "data.json", direction_data)
        self.agent("direction", "running", "HTML資料を生成中...")
        self.log("ai", "演出データ(data.json)作成完了")
        self.report(3, "演出データ作成完了", 85)

        # HTML生成
        self.report(3, "HTML資料を生成中...", 88)
        self.log("system", "HTMLファイルを生成中...", "Tailwind CSS + Alpine.js")
        self._build_html()
        self.log("system", "HTML資料の生成が完了", "index.html")
        self.agent("direction", "completed", "演出データ・HTML資料を作成完了")

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
        """画像生成スクリプトを実行（進捗をポーリング）"""
        output_subdir = self.output_dir / "images" / mode
        progress_path = output_subdir / f"{mode}_progress.json"
        mode_label = "図解" if mode == "diagrams" else "リアル画像"
        agent_id = mode  # "diagrams" or "realistic"
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
            # サブプロセスをバックグラウンドで起動
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

            # 進捗をポーリング
            last_reported = 0
            while process.poll() is None:
                time.sleep(3)
                if progress_path.exists():
                    try:
                        pg = json.loads(progress_path.read_text(encoding="utf-8"))
                        current = pg.get("current", 0)
                        total = pg.get("total", count)
                        section = pg.get("section", "")
                        status = pg.get("status", "")
                        if current > last_reported:
                            if status == "generating":
                                self.log("image", f"{mode_label} {current}/{total} 生成中...", section)
                                self.agent(agent_id, "running", f"{current}/{total}枚 生成中... ({section})", count=current, total=total)
                            elif status == "ok":
                                self.log("image", f"{mode_label} {current}/{total} 生成完了", section)
                                self.agent(agent_id, "running", f"{current}/{total}枚 完了", count=current, total=total)
                            elif status == "failed":
                                self.log("image", f"{mode_label} {current}/{total} 生成失敗", section)
                                self.agent(agent_id, "running", f"{current}/{total}枚 (一部失敗)", count=current, total=total)
                            last_reported = current
                    except Exception:
                        pass

            stdout, stderr = process.communicate(timeout=60)
            if stdout:
                print(stdout)
            if process.returncode != 0:
                err_msg = stderr[:500] if stderr else "不明なエラー"
                self.log("error", f"{mode_label}生成でエラー", err_msg)
                print(f"  [WARN] 画像生成エラー (code={process.returncode}): {err_msg}")
            else:
                # 最終進捗を報告
                if progress_path.exists():
                    try:
                        pg = json.loads(progress_path.read_text(encoding="utf-8"))
                        current = pg.get("current", 0)
                        total = pg.get("total", count)
                        if current > last_reported:
                            self.log("image", f"{mode_label} {current}/{total} 生成完了")
                    except Exception:
                        pass

        except subprocess.TimeoutExpired:
            self.log("error", f"{mode_label}生成がタイムアウトしました")
            print(f"  [WARN] 画像生成タイムアウト (mode={mode})")
            if process:
                process.kill()
        except Exception as e:
            self.log("error", f"{mode_label}生成に失敗", str(e))
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
