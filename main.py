#!/usr/bin/env python3
"""
MaxNet: локальная мини-поисковая система с публикацией сайтов,
встроенным Chromium-движком и синхронизацией через GitHub.

Запуск:
  python main.py            # GUI + встроенный браузер + сервер
  python main.py --daemon   # только сервер + трей
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import shutil
import sys
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

import requests
from flask import (
    Flask,
    jsonify,
    redirect,
    render_template_string,
    request,
    send_from_directory,
    url_for,
)
from werkzeug.utils import secure_filename

from PyQt6.QtCore import QTimer, QUrl
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import QApplication, QFileDialog, QInputDialog, QMainWindow, QMenu, QMessageBox, QSystemTrayIcon
from PyQt6.QtWebEngineWidgets import QWebEngineView

APP_NAME = "maxnet"
PORT = 8765
SYNC_INTERVAL_SECONDS = 120


@dataclass
class AppPaths:
    root: Path
    sites_dir: Path
    config_file: Path
    index_file: Path
    cache_zip: Path


class Storage:
    def __init__(self) -> None:
        documents = Path.home() / "Documents"
        root = documents / "MaxNet"
        self.paths = AppPaths(
            root=root,
            sites_dir=root / "sites",
            config_file=root / "config.json",
            index_file=root / "sites_index.json",
            cache_zip=root / "sites_bundle.zip",
        )
        self._prepare_dirs()
        self._ensure_defaults()

    def _prepare_dirs(self) -> None:
        self.paths.root.mkdir(parents=True, exist_ok=True)
        self.paths.sites_dir.mkdir(parents=True, exist_ok=True)

    def _ensure_defaults(self) -> None:
        if not self.paths.config_file.exists():
            default = {
                "first_run": True,
                "github_repo": "",
                "github_token": "",
                "github_branch": "main",
                "bundle_path": "maxnet/sites_bundle.zip",
                "last_remote_sha": "",
            }
            self.save_json(self.paths.config_file, default)
        if not self.paths.index_file.exists():
            self.save_json(self.paths.index_file, {"sites": []})

    def load_json(self, file_path: Path, fallback: dict) -> dict:
        if not file_path.exists():
            return fallback
        with file_path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def save_json(self, file_path: Path, data: dict) -> None:
        with file_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def get_config(self) -> dict:
        return self.load_json(self.paths.config_file, {})

    def save_config(self, config: dict) -> None:
        self.save_json(self.paths.config_file, config)

    def get_index(self) -> dict:
        return self.load_json(self.paths.index_file, {"sites": []})

    def save_index(self, index_data: dict) -> None:
        self.save_json(self.paths.index_file, index_data)

    def list_sites(self) -> List[dict]:
        index_data = self.get_index()
        sites = index_data.get("sites", [])
        return sorted(sites, key=lambda x: x.get("domain", ""))

    def _domain_dir(self, domain: str) -> Path:
        clean = secure_filename(domain.lower())
        return self.paths.sites_dir / clean

    def add_or_update_site(self, domain: str, title: str) -> None:
        index_data = self.get_index()
        sites = index_data.get("sites", [])
        found = False
        for site in sites:
            if site.get("domain") == domain:
                site["title"] = title
                site["updated_at"] = int(time.time())
                found = True
                break
        if not found:
            sites.append(
                {
                    "domain": domain,
                    "title": title,
                    "updated_at": int(time.time()),
                }
            )
        index_data["sites"] = sites
        self.save_index(index_data)

    def save_simple_page(self, domain: str, title: str, html_content: str) -> None:
        site_dir = self._domain_dir(domain)
        site_dir.mkdir(parents=True, exist_ok=True)
        index_file = site_dir / "index.html"
        wrapped_html = f"""<!doctype html>
<html lang=\"ru\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width,initial-scale=1\" />
  <title>{title}</title>
</head>
<body>
{html_content}
</body>
</html>
"""
        index_file.write_text(wrapped_html, encoding="utf-8")
        self.add_or_update_site(domain, title)

    def save_site_archive(self, domain: str, zip_bytes: bytes) -> None:
        site_dir = self._domain_dir(domain)
        if site_dir.exists():
            shutil.rmtree(site_dir)
        site_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            zf.extractall(site_dir)
        index_file = site_dir / "index.html"
        if not index_file.exists():
            raise ValueError("В архиве нет index.html")
        self.add_or_update_site(domain, domain)

    def bundle_sites_to_zip(self) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in self.paths.sites_dir.rglob("*"):
                if path.is_file():
                    rel = path.relative_to(self.paths.root)
                    zf.write(path, rel.as_posix())
            zf.write(self.paths.index_file, self.paths.index_file.relative_to(self.paths.root).as_posix())
        return buffer.getvalue()

    def apply_bundle_zip(self, zip_bytes: bytes) -> None:
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            zf.extractall(self.paths.root)


class GitHubSync:
    def __init__(self, storage: Storage) -> None:
        self.storage = storage

    def _headers(self, token: str) -> dict:
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _contents_url(self, repo: str, bundle_path: str) -> str:
        return f"https://api.github.com/repos/{repo}/contents/{bundle_path}"

    def pull_if_changed(self) -> None:
        cfg = self.storage.get_config()
        repo = cfg.get("github_repo", "").strip()
        token = cfg.get("github_token", "").strip()
        branch = cfg.get("github_branch", "main").strip()
        bundle_path = cfg.get("bundle_path", "maxnet/sites_bundle.zip").strip()
        if not repo or not token:
            return

        url = self._contents_url(repo, bundle_path)
        resp = requests.get(url, headers=self._headers(token), params={"ref": branch}, timeout=20)
        if resp.status_code != 200:
            return

        data = resp.json()
        remote_sha = data.get("sha", "")
        if remote_sha == cfg.get("last_remote_sha", ""):
            return

        download_url = data.get("download_url", "")
        if not download_url:
            return

        raw_resp = requests.get(download_url, timeout=30)
        if raw_resp.status_code != 200:
            return

        zip_bytes = raw_resp.content
        self.storage.paths.cache_zip.write_bytes(zip_bytes)
        self.storage.apply_bundle_zip(zip_bytes)

        cfg["last_remote_sha"] = remote_sha
        self.storage.save_config(cfg)

    def push_bundle(self) -> None:
        cfg = self.storage.get_config()
        repo = cfg.get("github_repo", "").strip()
        token = cfg.get("github_token", "").strip()
        branch = cfg.get("github_branch", "main").strip()
        bundle_path = cfg.get("bundle_path", "maxnet/sites_bundle.zip").strip()
        if not repo or not token:
            return

        local_zip = self.storage.bundle_sites_to_zip()
        self.storage.paths.cache_zip.write_bytes(local_zip)

        url = self._contents_url(repo, bundle_path)
        sha = ""
        get_resp = requests.get(url, headers=self._headers(token), params={"ref": branch}, timeout=20)
        if get_resp.status_code == 200:
            sha = get_resp.json().get("sha", "")

        body = {
            "message": "MaxNet sync bundle",
            "content": base64.b64encode(local_zip).decode("ascii"),
            "branch": branch,
        }
        if sha:
            body["sha"] = sha

        put_resp = requests.put(url, headers=self._headers(token), json=body, timeout=40)
        if put_resp.status_code in (200, 201):
            new_sha = put_resp.json().get("content", {}).get("sha", "")
            if new_sha:
                cfg["last_remote_sha"] = new_sha
                self.storage.save_config(cfg)


class MaxNetServer:
    def __init__(self, storage: Storage, sync: GitHubSync) -> None:
        self.storage = storage
        self.sync = sync
        self.app = Flask(APP_NAME)
        self._setup_routes()

    def _search(self, query: str) -> List[dict]:
        q = query.lower().strip()
        sites = self.storage.list_sites()
        if not q:
            return sites
        result = []
        for site in sites:
            domain = site.get("domain", "").lower()
            title = site.get("title", "").lower()
            if q in domain or q in title:
                result.append(site)
        return result

    def _setup_routes(self) -> None:
        @self.app.get("/")
        def home():
            q = request.args.get("q", "")
            sites = self._search(q)
            return render_template_string(HOME_TEMPLATE, sites=sites, q=q)

        @self.app.get("/site/<domain>/")
        def site_root(domain: str):
            site_dir = self.storage.paths.sites_dir / secure_filename(domain.lower())
            return send_from_directory(site_dir, "index.html")

        @self.app.get("/site/<domain>/<path:filename>")
        def site_files(domain: str, filename: str):
            site_dir = self.storage.paths.sites_dir / secure_filename(domain.lower())
            return send_from_directory(site_dir, filename)

        @self.app.get("/create")
        def create_form():
            return render_template_string(CREATE_TEMPLATE)

        @self.app.post("/publish/simple")
        def publish_simple():
            domain = secure_filename(request.form.get("domain", "").strip().lower())
            title = request.form.get("title", domain).strip()
            html_text = request.form.get("html", "").strip()
            if not domain or not html_text:
                return "Нужно заполнить домен и HTML", 400
            self.storage.save_simple_page(domain, title or domain, html_text)
            self.sync.push_bundle()
            return redirect(url_for("home"))

        @self.app.post("/publish/zip")
        def publish_zip():
            domain = secure_filename(request.form.get("domain", "").strip().lower())
            file = request.files.get("archive")
            if not domain or file is None:
                return "Нужны домен и zip", 400
            raw = file.read()
            try:
                self.storage.save_site_archive(domain, raw)
            except ValueError as err:
                return str(err), 400
            self.sync.push_bundle()
            return redirect(url_for("home"))

        @self.app.get("/api/sites")
        def api_sites():
            return jsonify(self.storage.list_sites())

        @self.app.get("/api/search")
        def api_search():
            q = request.args.get("q", "")
            return jsonify(self._search(q))

        @self.app.get("/bot")
        def bot_help():
            q = request.args.get("q", "").lower().strip()
            if "создать" in q or "сайт" in q:
                answer = "Откройте Создать сайт, введите домен и загрузите ZIP или вставьте HTML."
            elif "github" in q:
                answer = "Настройте github_repo и github_token в ~/Documents/MaxNet/config.json."
            else:
                answer = "Я бот MaxNet: помогаю с поиском, публикацией и синхронизацией сайтов."
            return jsonify({"q": q, "answer": answer})

    def run(self) -> None:
        self.app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False)


class SyncWorker(threading.Thread):
    def __init__(self, sync: GitHubSync):
        super().__init__(daemon=True)
        self.sync = sync
        self._active = True

    def stop(self) -> None:
        self._active = False

    def run(self) -> None:
        while self._active:
            self.sync.pull_if_changed()
            time.sleep(SYNC_INTERVAL_SECONDS)


class ServerWorker(threading.Thread):
    def __init__(self, server: MaxNetServer):
        super().__init__(daemon=True)
        self.server = server

    def run(self) -> None:
        self.server.run()


def setup_autostart(storage: Storage) -> None:
    cfg = storage.get_config()
    if not cfg.get("first_run", True):
        return

    script_path = Path(__file__).resolve()

    if os.name == "nt":
        startup = Path.home() / "AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Startup"
        startup.mkdir(parents=True, exist_ok=True)
        bat = startup / "maxnet_autostart.bat"
        bat.write_text(f'@echo off\nstart "" "{sys.executable}" "{script_path}" --daemon\n', encoding="utf-8")
    else:
        autostart = Path.home() / ".config/autostart"
        autostart.mkdir(parents=True, exist_ok=True)
        desktop = autostart / "maxnet.desktop"
        desktop.write_text(
            "\n".join(
                [
                    "[Desktop Entry]",
                    "Type=Application",
                    "Name=MaxNet Daemon",
                    f"Exec={sys.executable} {script_path} --daemon",
                    "X-GNOME-Autostart-enabled=true",
                ]
            ),
            encoding="utf-8",
        )

    cfg["first_run"] = False
    storage.save_config(cfg)


class MaxNetWindow(QMainWindow):
    def __init__(self, storage: Storage):
        super().__init__()
        self.storage = storage
        self.setWindowTitle("MaxNet Browser")
        self.resize(1280, 800)

        self.browser = QWebEngineView()
        self.setCentralWidget(self.browser)
        self.browser.load(QUrl(f"http://127.0.0.1:{PORT}/"))

        self._build_menu()

    def _build_menu(self) -> None:
        menu = self.menuBar()

        app_menu = menu.addMenu("MaxNet")
        open_home = QAction("Главная", self)
        open_home.triggered.connect(lambda: self.browser.load(QUrl(f"http://127.0.0.1:{PORT}/")))

        open_create = QAction("Создать сайт", self)
        open_create.triggered.connect(lambda: self.browser.load(QUrl(f"http://127.0.0.1:{PORT}/create")))

        upload_zip = QAction("Загрузить ZIP", self)
        upload_zip.triggered.connect(self.upload_zip_dialog)

        app_menu.addAction(open_home)
        app_menu.addAction(open_create)
        app_menu.addAction(upload_zip)

    def upload_zip_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Выберите zip", str(Path.home()), "ZIP (*.zip)")
        if not path:
            return
        domain, ok = QInputDialog.getText(self, "Домен", "Введите домен (например: my-site):")
        if not ok or not domain:
            return

        with open(path, "rb") as f:
            files = {"archive": (Path(path).name, f, "application/zip")}
            data = {"domain": domain}
            resp = requests.post(f"http://127.0.0.1:{PORT}/publish/zip", files=files, data=data, timeout=60)
            if resp.status_code not in (200, 302):
                QMessageBox.warning(self, "Ошибка", f"Не удалось загрузить ZIP: {resp.text}")
                return

        self.browser.load(QUrl(f"http://127.0.0.1:{PORT}/"))


class TrayController:
    def __init__(self, app: QApplication):
        self.app = app
        self.tray = QSystemTrayIcon(QIcon())
        self.tray.setToolTip("MaxNet daemon")
        menu = QMenu()
        quit_action = QAction("Выход", self.app)
        quit_action.triggered.connect(self.app.quit)
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.show()


HOME_TEMPLATE = """
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MaxNet</title>
  <style>
    body { font-family: system-ui, Arial, sans-serif; margin: 24px; background: #f4f6fb; color: #222; }
    .box { background: #fff; padding: 16px; border-radius: 12px; box-shadow: 0 1px 3px rgba(0,0,0,.1); margin-bottom: 16px; }
    input[type=text] { width: 100%; padding: 10px; border-radius: 10px; border: 1px solid #ccc; }
    a { text-decoration: none; color: #1344d4; }
    .site { padding: 8px 0; border-bottom: 1px solid #eee; }
    .site:last-child { border-bottom: none; }
    .row { display:flex; gap: 8px; align-items:center; }
    .btn { display:inline-block; padding: 8px 12px; border-radius: 10px; background:#1344d4; color:#fff; }
  </style>
</head>
<body>
  <h1>MaxNet — главная</h1>

  <div class="box">
    <form method="get" action="/">
      <input type="text" name="q" placeholder="Поиск по сайтам и доменам" value="{{ q }}" />
    </form>
    <div class="row" style="margin-top:12px">
      <a class="btn" href="/create">Создать сайт</a>
    </div>
  </div>

  <div class="box">
    <h2>Сайты системы</h2>
    {% if sites %}
      {% for site in sites %}
        <div class="site">
          <a href="/site/{{ site.domain }}/"><b>{{ site.title }}</b></a>
          <div>{{ site.domain }}</div>
        </div>
      {% endfor %}
    {% else %}
      <p>Пока пусто. Добавьте первый сайт.</p>
    {% endif %}
  </div>
</body>
</html>
"""


CREATE_TEMPLATE = """
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Создать сайт — MaxNet</title>
  <style>
    body { font-family: system-ui, Arial, sans-serif; margin: 24px; background: #f4f6fb; color: #222; }
    .box { background: #fff; padding: 16px; border-radius: 12px; box-shadow: 0 1px 3px rgba(0,0,0,.1); margin-bottom: 16px; }
    input, textarea { width: 100%; padding: 10px; border-radius: 10px; border: 1px solid #ccc; margin-top:6px; }
    textarea { min-height: 220px; }
    button { margin-top: 12px; padding: 10px 14px; border: 0; border-radius: 10px; background:#1344d4; color:#fff; }
    a { color:#1344d4; text-decoration:none; }
  </style>
</head>
<body>
  <h1>Создание сайта</h1>
  <a href="/">← Назад на главную</a>

  <div class="box">
    <h3>Простая страница (HTML)</h3>
    <form method="post" action="/publish/simple">
      <label>Домен</label>
      <input type="text" name="domain" placeholder="my-site" required>
      <label>Название</label>
      <input type="text" name="title" placeholder="Мой сайт" required>
      <label>HTML</label>
      <textarea name="html" placeholder="<h1>Привет</h1>" required></textarea>
      <button type="submit">Опубликовать страницу</button>
    </form>
  </div>

  <div class="box">
    <h3>Публикация ZIP (поддержка JS/CSS)</h3>
    <form method="post" action="/publish/zip" enctype="multipart/form-data">
      <label>Домен</label>
      <input type="text" name="domain" placeholder="my-site" required>
      <label>ZIP архив сайта (в корне должен быть index.html)</label>
      <input type="file" name="archive" accept=".zip" required>
      <button type="submit">Загрузить ZIP</button>
    </form>
  </div>
</body>
</html>
"""


def run_app(with_gui: bool) -> None:
    storage = Storage()
    setup_autostart(storage)
    sync = GitHubSync(storage)
    server = MaxNetServer(storage, sync)

    server_worker = ServerWorker(server)
    server_worker.start()

    sync_worker = SyncWorker(sync)
    sync_worker.start()

    if not with_gui:
        app = QApplication(sys.argv)
        TrayController(app)
        timer = QTimer()
        timer.start(1000)
        timer.timeout.connect(lambda: None)
        sys.exit(app.exec())

    app = QApplication(sys.argv)
    TrayController(app)
    window = MaxNetWindow(storage)
    window.show()
    sys.exit(app.exec())


def main() -> None:
    parser = argparse.ArgumentParser(description="MaxNet launcher")
    parser.add_argument("--daemon", action="store_true", help="run only daemon + tray")
    args = parser.parse_args()
    run_app(with_gui=not args.daemon)


if __name__ == "__main__":
    main()
