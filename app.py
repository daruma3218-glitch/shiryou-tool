#!/usr/bin/env python3
"""資料作成ツール - Flask Webアプリケーション"""

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template, request, jsonify, send_from_directory, redirect, url_for

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10MB max

PROJECT_ROOT = Path(__file__).parent
OUTPUT_DIR = PROJECT_ROOT / "output"
INPUT_DIR = PROJECT_ROOT / "input"

# 進行中のジョブを管理
active_jobs = {}
job_logs = {}  # job_id -> list of log entries
job_agents = {}  # job_id -> dict of agent states


def load_env():
    """Load .env file"""
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                key, val = line.split("=", 1)
                val = val.strip().strip('"').strip("'")
                if val and val != "your_api_key_here":
                    os.environ[key] = val


load_env()


def update_agent(job_id: str, agent_id: str, status: str, message: str, count: int = 0, total: int = 0):
    """エージェントの状態を更新"""
    if job_id not in job_agents:
        job_agents[job_id] = {}
    now = datetime.now().strftime("%H:%M:%S")
    if agent_id not in job_agents[job_id]:
        job_agents[job_id][agent_id] = {"startedAt": "", "completedAt": ""}
    agent = job_agents[job_id][agent_id]
    agent["status"] = status
    agent["message"] = message
    agent["count"] = count
    agent["total"] = total
    if status == "running" and not agent.get("startedAt"):
        agent["startedAt"] = now
    if status in ("completed", "error"):
        agent["completedAt"] = now
    # ファイルにも保存
    agents_path = OUTPUT_DIR / job_id / "agents.json"
    if agents_path.parent.exists():
        agents_path.write_text(json.dumps(job_agents[job_id], ensure_ascii=False), encoding="utf-8")


def add_log(job_id: str, category: str, message: str, detail: str = ""):
    """アクティビティログを追加"""
    if job_id not in job_logs:
        job_logs[job_id] = []
    entry = {
        "time": datetime.now().strftime("%H:%M:%S"),
        "category": category,
        "message": message,
        "detail": detail,
    }
    job_logs[job_id].append(entry)
    # ファイルにも保存（ブラウザリロード対応）
    logs_path = OUTPUT_DIR / job_id / "logs.json"
    if logs_path.parent.exists():
        logs_path.write_text(json.dumps(job_logs[job_id], ensure_ascii=False), encoding="utf-8")


def update_progress(job_id: str, phase: int, message: str, percent: int, status: str = "running"):
    """ジョブの進捗を更新"""
    progress = {
        "phase": phase,
        "message": message,
        "percent": percent,
        "status": status,
        "updated_at": datetime.now().isoformat(),
    }
    active_jobs[job_id] = progress
    # ファイルにも保存
    progress_path = OUTPUT_DIR / job_id / "progress.json"
    if progress_path.parent.exists():
        progress_path.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")


def run_pipeline(job_id: str, manuscript_path: str):
    """パイプラインをバックグラウンドで実行"""
    try:
        from scripts.pipeline import MaterialPipeline
        pipeline = MaterialPipeline(
            manuscript_path=manuscript_path,
            output_dir=str(OUTPUT_DIR / job_id),
            project_root=str(PROJECT_ROOT),
            progress_callback=lambda phase, msg, pct: update_progress(job_id, phase, msg, pct),
            log_callback=lambda cat, msg, detail="": add_log(job_id, cat, msg, detail),
            agent_callback=lambda aid, status, msg, count=0, total=0: update_agent(job_id, aid, status, msg, count, total),
        )
        pipeline.run()
        update_progress(job_id, 4, "完了しました！", 100, "completed")
        add_log(job_id, "complete", "全工程が完了しました")
    except Exception as e:
        update_progress(job_id, -1, f"エラー: {str(e)}", 0, "error")
        add_log(job_id, "error", f"エラー: {str(e)}")
        import traceback
        traceback.print_exc()


@app.route("/")
def index():
    """アップロードページ"""
    # 過去のジョブ一覧を取得
    past_jobs = []
    if OUTPUT_DIR.exists():
        for d in sorted(OUTPUT_DIR.iterdir(), reverse=True):
            if d.is_dir() and (d / "index.html").exists():
                progress = {}
                progress_path = d / "progress.json"
                if progress_path.exists():
                    try:
                        progress = json.loads(progress_path.read_text(encoding="utf-8"))
                    except Exception:
                        pass
                data_path = d / "data.json"
                title = d.name
                if data_path.exists():
                    try:
                        data = json.loads(data_path.read_text(encoding="utf-8"))
                        title = data.get("title", d.name)
                    except Exception:
                        pass
                past_jobs.append({
                    "id": d.name,
                    "title": title,
                    "status": progress.get("status", "completed"),
                    "date": d.name[:8] if len(d.name) >= 8 else "",
                })
    has_gemini_key = bool(os.environ.get("GEMINI_API_KEY"))
    has_anthropic_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    return render_template("upload.html", past_jobs=past_jobs, has_gemini_key=has_gemini_key, has_anthropic_key=has_anthropic_key)


@app.route("/start", methods=["POST"])
def start_job():
    """ジョブを開始"""
    # APIキー確認
    missing = []
    if not os.environ.get("ANTHROPIC_API_KEY"):
        missing.append("ANTHROPIC_API_KEY")
    if not os.environ.get("GEMINI_API_KEY"):
        missing.append("GEMINI_API_KEY")
    if missing:
        return jsonify({"error": f"{', '.join(missing)} が設定されていません。.env ファイルを確認してください。"}), 400

    # 原稿取得
    manuscript_text = ""
    if "manuscript_file" in request.files and request.files["manuscript_file"].filename:
        file = request.files["manuscript_file"]
        manuscript_text = file.read().decode("utf-8")
    elif request.form.get("manuscript_text"):
        manuscript_text = request.form["manuscript_text"]
    else:
        return jsonify({"error": "原稿が入力されていません"}), 400

    if len(manuscript_text.strip()) < 100:
        return jsonify({"error": "原稿が短すぎます（100文字以上必要）"}), 400

    # ジョブID作成
    job_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    job_dir = OUTPUT_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    # 原稿を保存
    manuscript_path = job_dir / "manuscript.txt"
    manuscript_path.write_text(manuscript_text, encoding="utf-8")

    # 進捗初期化
    update_progress(job_id, 0, "セットアップ中...", 0)

    # バックグラウンドで実行
    thread = threading.Thread(target=run_pipeline, args=(job_id, str(manuscript_path)), daemon=True)
    thread.start()

    return jsonify({"job_id": job_id, "redirect": f"/progress/{job_id}"})


@app.route("/progress/<job_id>")
def progress_page(job_id):
    """進捗ページ"""
    return render_template("progress.html", job_id=job_id)


@app.route("/api/progress/<job_id>")
def api_progress(job_id):
    """進捗APIエンドポイント"""
    if job_id in active_jobs:
        return jsonify(active_jobs[job_id])
    progress_path = OUTPUT_DIR / job_id / "progress.json"
    if progress_path.exists():
        return jsonify(json.loads(progress_path.read_text(encoding="utf-8")))
    return jsonify({"phase": -1, "message": "ジョブが見つかりません", "percent": 0, "status": "not_found"})


@app.route("/api/logs/<job_id>")
def api_logs(job_id):
    """アクティビティログAPIエンドポイント"""
    since = int(request.args.get("since", 0))
    # メモリから取得
    if job_id in job_logs:
        logs = job_logs[job_id]
    else:
        # ファイルから読み込み
        logs_path = OUTPUT_DIR / job_id / "logs.json"
        if logs_path.exists():
            try:
                logs = json.loads(logs_path.read_text(encoding="utf-8"))
            except Exception:
                logs = []
        else:
            logs = []
    # sinceインデックス以降のログのみ返す
    return jsonify({"logs": logs[since:], "total": len(logs)})


@app.route("/api/agents/<job_id>")
def api_agents(job_id):
    """エージェント状態APIエンドポイント"""
    if job_id in job_agents:
        return jsonify(job_agents[job_id])
    agents_path = OUTPUT_DIR / job_id / "agents.json"
    if agents_path.exists():
        try:
            return jsonify(json.loads(agents_path.read_text(encoding="utf-8")))
        except Exception:
            pass
    return jsonify({})


@app.route("/results/<job_id>/")
@app.route("/results/<job_id>/<path:filename>")
def serve_results(job_id, filename="index.html"):
    """結果ファイルを配信"""
    result_dir = OUTPUT_DIR / job_id
    if not result_dir.exists():
        return "結果が見つかりません", 404
    return send_from_directory(str(result_dir), filename)


INPUT_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    print("\n" + "=" * 50)
    print("  資料作成ツール 起動中...")
    print(f"  http://localhost:{port}")
    print("=" * 50 + "\n")
    app.run(host="0.0.0.0", port=port, debug=False)
