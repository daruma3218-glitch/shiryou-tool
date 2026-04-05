#!/usr/bin/env python3
"""資料作成ツール - Flask Webアプリケーション"""

import functools
import io
import json
import os
import secrets
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template, request, jsonify, send_from_directory, send_file, redirect, url_for, session

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

# --- 認証設定 ---
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")


def login_required(f):
    """パスワード認証が必要なルートに適用するデコレータ"""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        # APP_PASSWORD 未設定なら認証なしで通過
        if not APP_PASSWORD:
            return f(*args, **kwargs)
        if not session.get("authenticated"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


@app.route("/login", methods=["GET", "POST"])
def login():
    """ログインページ"""
    # パスワード未設定なら直接トップへ
    if not APP_PASSWORD:
        return redirect(url_for("index"))
    # 認証済みなら直接トップへ
    if session.get("authenticated"):
        return redirect(url_for("index"))

    error = None
    if request.method == "POST":
        password = request.form.get("password", "")
        if password == APP_PASSWORD:
            session["authenticated"] = True
            return redirect(url_for("index"))
        else:
            error = "パスワードが正しくありません"

    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    """ログアウト"""
    session.clear()
    return redirect(url_for("login"))


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


def run_pipeline(job_id: str, manuscript_path: str, image_instructions: str = ""):
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
            image_instructions=image_instructions,
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
@login_required
def index():
    """アップロードページ"""
    # 過去のジョブ一覧を取得
    past_jobs = []
    try:
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
    except PermissionError:
        pass
    has_gemini_key = bool(os.environ.get("GEMINI_API_KEY"))
    has_anthropic_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    has_youtube_key = bool(os.environ.get("YOUTUBE_API_KEY"))
    return render_template("upload.html", past_jobs=past_jobs, has_gemini_key=has_gemini_key, has_anthropic_key=has_anthropic_key, has_youtube_key=has_youtube_key)


@app.route("/start", methods=["POST"])
@login_required
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

    # 画像指示を取得
    image_instructions = request.form.get("image_instructions", "").strip()

    # 原稿を保存
    manuscript_path = job_dir / "manuscript.txt"
    manuscript_path.write_text(manuscript_text, encoding="utf-8")

    # 画像指示を保存
    if image_instructions:
        (job_dir / "image_instructions.txt").write_text(image_instructions, encoding="utf-8")

    # 進捗初期化
    update_progress(job_id, 0, "セットアップ中...", 0)

    # バックグラウンドで実行
    thread = threading.Thread(target=run_pipeline, args=(job_id, str(manuscript_path), image_instructions), daemon=True)
    thread.start()

    return jsonify({"job_id": job_id, "redirect": f"/progress/{job_id}"})


@app.route("/progress/<job_id>")
@login_required
def progress_page(job_id):
    """進捗ページ"""
    return render_template("progress.html", job_id=job_id)


@app.route("/api/progress/<job_id>")
@login_required
def api_progress(job_id):
    """進捗APIエンドポイント"""
    if job_id in active_jobs:
        return jsonify(active_jobs[job_id])
    progress_path = OUTPUT_DIR / job_id / "progress.json"
    if progress_path.exists():
        return jsonify(json.loads(progress_path.read_text(encoding="utf-8")))
    return jsonify({"phase": -1, "message": "ジョブが見つかりません", "percent": 0, "status": "not_found"})


@app.route("/api/logs/<job_id>")
@login_required
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
@login_required
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
@login_required
def serve_results(job_id, filename="index.html"):
    """結果ファイルを配信"""
    result_dir = OUTPUT_DIR / job_id
    if not result_dir.exists():
        return "結果が見つかりません", 404
    return send_from_directory(str(result_dir), filename)


@app.route("/download/<job_id>")
@login_required
def download_zip(job_id):
    """生成した資料をZIPでダウンロード"""
    result_dir = OUTPUT_DIR / job_id
    if not result_dir.exists() or not (result_dir / "index.html").exists():
        return "結果が見つかりません", 404

    # タイトルを取得（ファイル名用）
    title = job_id
    data_path = result_dir / "data.json"
    if data_path.exists():
        try:
            data = json.loads(data_path.read_text(encoding="utf-8"))
            t = data.get("title", "")
            if t:
                # ファイル名に使えない文字を除去
                title = "".join(c for c in t if c not in r'\/:*?"<>|').strip()[:50]
        except Exception:
            pass

    # ZIPをメモリ上に作成
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in result_dir.rglob("*"):
            if file_path.is_file():
                # 不要なファイルをスキップ
                name = file_path.name
                if name in ("progress.json", "logs.json", "agents.json",
                            "manuscript.txt", "diagram_prompts.json",
                            "realistic_prompts.json", "data_backup.json"):
                    continue
                # _progress.json もスキップ
                if name.endswith("_progress.json"):
                    continue
                # ZIP内のパスを設定
                arcname = file_path.relative_to(result_dir)
                zf.write(file_path, arcname)

    zip_buffer.seek(0)
    return send_file(
        zip_buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{title}.zip",
    )


INPUT_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    print("\n" + "=" * 50)
    print("  資料作成ツール 起動中...")
    print(f"  http://localhost:{port}")
    print("=" * 50 + "\n")
    app.run(host="0.0.0.0", port=port, debug=False)
