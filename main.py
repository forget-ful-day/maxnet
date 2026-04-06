#!/usr/bin/env python3
"""
MaxNet: локальная мини-поисковая система с публикацией сайтов,
встроенным WebView-окном и синхронизацией через GitHub.

Запуск:
  python main.py            # GUI (webview) + сервер
  python main.py --daemon   # только сервер (без графики)
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import shutil
import threading
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List

import requests
import webview
from flask import Flask, jsonify, redirect, render_template_string, request, send_from_directory, session, url_for
from werkzeug.utils import secure_filename

APP_NAME = "maxnet"
PORT = 8765
SYNC_INTERVAL_SECONDS = 120

# Настройка GitHub прямо в коде (как просили).
# Заполните эти значения вручную:
GITHUB_REPO = ""  # пример: "your_login/your_repo"
GITHUB_TOKEN = ""  # пример: "ghp_xxx"
GITHUB_BRANCH = "main"
GITHUB_ROOT = "maxnet/sites"
ADMIN_PASSWORD = "admin123"


@dataclass
class AppPaths:
    root: Path
    sites_dir: Path
    config_file: Path
    index_file: Path
    chat_file: Path


class Storage:
    def __init__(self) -> None:
        documents = Path.home() / "Documents"
        root = documents / "MaxNet"
        self.paths = AppPaths(
            root=root,
            sites_dir=root / "sites",
            config_file=root / "config.json",
            index_file=root / "sites_index.json",
            chat_file=root / "chat.json",
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
            }
            self.save_json(self.paths.config_file, default)
        if not self.paths.index_file.exists():
            self.save_json(self.paths.index_file, {"sites": []})
        if not self.paths.chat_file.exists():
            self.save_json(self.paths.chat_file, {"messages": []})

    def load_json(self, file_path: Path, fallback: dict) -> dict:
        if not file_path.exists():
            return fallback
        with file_path.open("r", encoding="utf-8") as file_obj:
            return json.load(file_obj)

    def save_json(self, file_path: Path, data: dict) -> None:
        with file_path.open("w", encoding="utf-8") as file_obj:
            json.dump(data, file_obj, ensure_ascii=False, indent=2)

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
        return sorted(sites, key=lambda item: item.get("domain", ""))

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
            sites.append({"domain": domain, "title": title, "updated_at": int(time.time())})
        index_data["sites"] = sites
        self.save_index(index_data)

    def _extract_title_from_index(self, index_file: Path, fallback: str) -> str:
        if not index_file.exists():
            return fallback
        html = index_file.read_text(encoding="utf-8", errors="ignore")
        match = re.search(r"<title>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
        if match:
            title = re.sub(r"\s+", " ", match.group(1)).strip()
            if title:
                return title
        return fallback

    def rebuild_index_from_sites(self) -> None:
        sites: List[dict] = []
        for domain_dir in sorted(self.paths.sites_dir.iterdir()):
            if not domain_dir.is_dir():
                continue
            domain = domain_dir.name
            title = self._extract_title_from_index(domain_dir / "index.html", domain)
            sites.append({"domain": domain, "title": title, "updated_at": int(time.time())})
        self.save_index({"sites": sites})

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

    def save_site_archive(self, domain: str, zip_path: Path) -> None:
        site_dir = self._domain_dir(domain)
        if site_dir.exists():
            shutil.rmtree(site_dir)
        site_dir.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(zip_path, "r") as zip_file:
            zip_file.extractall(site_dir)

        index_file = site_dir / "index.html"
        if not index_file.exists():
            raise ValueError("В архиве нет index.html")

        title = self._extract_title_from_index(index_file, domain)
        self.add_or_update_site(domain, title)

    def get_site_index_html(self, domain: str) -> str:
        index_file = self._domain_dir(domain) / "index.html"
        if not index_file.exists():
            return ""
        return index_file.read_text(encoding="utf-8", errors="ignore")

    def save_site_index_html(self, domain: str, title: str, html: str) -> None:
        site_dir = self._domain_dir(domain)
        site_dir.mkdir(parents=True, exist_ok=True)
        index_file = site_dir / "index.html"
        index_file.write_text(html, encoding="utf-8")
        self.add_or_update_site(domain, title or domain)

    def delete_site(self, domain: str) -> None:
        site_dir = self._domain_dir(domain)
        if site_dir.exists():
            shutil.rmtree(site_dir)
        self.rebuild_index_from_sites()

    def get_chat_messages(self) -> List[dict]:
        payload = self.load_json(self.paths.chat_file, {"messages": []})
        return payload.get("messages", [])

    def append_chat_message(self, nickname: str, text: str) -> None:
        payload = self.load_json(self.paths.chat_file, {"messages": []})
        messages = payload.get("messages", [])
        messages.append(
            {
                "nickname": nickname,
                "text": text,
                "created_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
            }
        )
        payload["messages"] = messages[-200:]
        self.save_json(self.paths.chat_file, payload)


class GitHubSync:
    def __init__(self, storage: Storage) -> None:
        self.storage = storage

    def _headers(self, token: str) -> dict:
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _contents_url(self, repo: str, remote_path: str) -> str:
        return f"https://api.github.com/repos/{repo}/contents/{remote_path}"

    def _tree_url(self, repo: str, branch: str) -> str:
        return f"https://api.github.com/repos/{repo}/git/trees/{branch}"

    def _cfg(self) -> tuple[str, str, str, str]:
        repo = GITHUB_REPO.strip()
        token = GITHUB_TOKEN.strip()
        branch = GITHUB_BRANCH.strip() or "main"
        root = GITHUB_ROOT.strip().strip("/") or "maxnet/sites"
        return repo, token, branch, root

    def _remote_tree_paths(self, repo: str, token: str, branch: str) -> Dict[str, str]:
        response = requests.get(
            self._tree_url(repo, branch),
            headers=self._headers(token),
            params={"recursive": "1"},
            timeout=30,
        )
        if response.status_code != 200:
            return {}

        tree = response.json().get("tree", [])
        result: Dict[str, str] = {}
        for item in tree:
            if item.get("type") == "blob":
                result[item.get("path", "")] = item.get("sha", "")
        return result

    def pull_missing_files(self) -> None:
        repo, token, branch, root = self._cfg()
        if not repo or not token:
            return

        remote_paths = self._remote_tree_paths(repo, token, branch)
        if not remote_paths:
            return

        changed = False
        prefix = f"{root}/"
        for remote_path in sorted(remote_paths.keys()):
            if not remote_path.startswith(prefix):
                continue
            rel = remote_path[len(prefix) :]
            if not rel:
                continue

            local_path = self.storage.paths.sites_dir / rel
            if local_path.exists():
                continue

            resp = requests.get(
                self._contents_url(repo, remote_path),
                headers=self._headers(token),
                params={"ref": branch},
                timeout=30,
            )
            if resp.status_code != 200:
                continue

            payload = resp.json()
            encoded = payload.get("content", "")
            encoding = payload.get("encoding", "")
            if encoding != "base64" or not encoded:
                continue

            file_bytes = base64.b64decode(encoded)
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_bytes(file_bytes)
            changed = True

        if changed:
            self.storage.rebuild_index_from_sites()

    def push_missing_files_only(self) -> None:
        repo, token, branch, root = self._cfg()
        if not repo or not token:
            return

        remote_paths = self._remote_tree_paths(repo, token, branch)
        prefix = f"{root}/"

        for path in self.storage.paths.sites_dir.rglob("*"):
            if not path.is_file():
                continue

            rel = path.relative_to(self.storage.paths.sites_dir).as_posix()
            remote_path = f"{prefix}{rel}"

            if remote_path in remote_paths:
                continue

            raw = path.read_bytes()
            body = {
                "message": f"Add {remote_path} from MaxNet",
                "content": base64.b64encode(raw).decode("ascii"),
                "branch": branch,
            }
            requests.put(self._contents_url(repo, remote_path), headers=self._headers(token), json=body, timeout=40)


class MaxNetServer:
    def __init__(self, storage: Storage, sync: GitHubSync) -> None:
        self.storage = storage
        self.sync = sync
        self.app = Flask(APP_NAME)
        self.app.secret_key = "maxnet-local-secret"
        self._setup_routes()

    def _search(self, query: str) -> List[dict]:
        q = query.lower().strip()
        sites = self.storage.list_sites()
        if not q:
            return sites

        results = []
        for site in sites:
            domain = site.get("domain", "").lower()
            title = site.get("title", "").lower()
            if q in domain or q in title:
                results.append(site)
        return results

    def _setup_routes(self) -> None:
        def admin_required() -> bool:
            return bool(session.get("is_admin"))

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

        @self.app.post("/sync/pull")
        def sync_pull():
            self.sync.pull_missing_files()
            return redirect(url_for("home"))

        @self.app.post("/sync/push")
        def sync_push():
            self.sync.push_missing_files_only()
            return redirect(url_for("home"))

        @self.app.post("/sync/update")
        def sync_update():
            self.sync.pull_missing_files()
            self.sync.push_missing_files_only()
            return redirect(url_for("home"))

        @self.app.post("/publish/simple")
        def publish_simple():
            domain = secure_filename(request.form.get("domain", "").strip().lower())
            title = request.form.get("title", domain).strip()
            html_text = request.form.get("html", "").strip()
            if not domain or not html_text:
                return "Нужно заполнить домен и HTML", 400
            self.storage.save_simple_page(domain, title or domain, html_text)
            self.sync.push_missing_files_only()
            return redirect(url_for("home"))

        @self.app.post("/publish/zip")
        def publish_zip():
            domain = secure_filename(request.form.get("domain", "").strip().lower())
            file = request.files.get("archive")
            if not domain or file is None:
                return "Нужны домен и zip", 400

            tmp_zip = self.storage.paths.root / f"upload_{int(time.time())}.zip"
            file.save(tmp_zip)
            try:
                self.storage.save_site_archive(domain, tmp_zip)
            except ValueError as err:
                return str(err), 400
            finally:
                if tmp_zip.exists():
                    tmp_zip.unlink()

            self.sync.push_missing_files_only()
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
            elif "github" in q or "git" in q:
                answer = "Откройте main.py и заполните переменные GITHUB_REPO/GITHUB_TOKEN/GITHUB_BRANCH/GITHUB_ROOT."
            else:
                answer = "Я бот MaxNet: помогаю с поиском, публикацией и синхронизацией сайтов."
            return jsonify({"q": q, "answer": answer})

        @self.app.get("/chat")
        def chat():
            messages = self.storage.get_chat_messages()
            return render_template_string(CHAT_TEMPLATE, messages=messages)

        @self.app.post("/chat/post")
        def chat_post():
            nickname = request.form.get("nickname", "").strip() or "Гость"
            text = request.form.get("text", "").strip()
            if text:
                self.storage.append_chat_message(nickname, text)
            return redirect(url_for("chat"))

        @self.app.get("/admin/login")
        def admin_login_page():
            return render_template_string(ADMIN_LOGIN_TEMPLATE, error="")

        @self.app.post("/admin/login")
        def admin_login_submit():
            password = request.form.get("password", "")
            if password == ADMIN_PASSWORD:
                session["is_admin"] = True
                return redirect(url_for("admin_panel"))
            return render_template_string(ADMIN_LOGIN_TEMPLATE, error="Неверный пароль")

        @self.app.get("/admin/logout")
        def admin_logout():
            session.clear()
            return redirect(url_for("home"))

        @self.app.get("/admin")
        def admin_panel():
            if not admin_required():
                return redirect(url_for("admin_login_page"))
            sites = self.storage.list_sites()
            return render_template_string(ADMIN_PANEL_TEMPLATE, sites=sites)

        @self.app.get("/admin/edit/<domain>")
        def admin_edit_site(domain: str):
            if not admin_required():
                return redirect(url_for("admin_login_page"))
            html = self.storage.get_site_index_html(domain)
            site = next((s for s in self.storage.list_sites() if s.get("domain") == domain), None)
            title = site.get("title", domain) if site else domain
            return render_template_string(ADMIN_EDIT_TEMPLATE, domain=domain, title=title, html=html)

        @self.app.post("/admin/edit/<domain>")
        def admin_edit_site_save(domain: str):
            if not admin_required():
                return redirect(url_for("admin_login_page"))
            title = request.form.get("title", "").strip() or domain
            html = request.form.get("html", "")
            self.storage.save_site_index_html(domain, title, html)
            self.sync.push_missing_files_only()
            return redirect(url_for("admin_panel"))

        @self.app.post("/admin/delete/<domain>")
        def admin_delete_site(domain: str):
            if not admin_required():
                return redirect(url_for("admin_login_page"))
            self.storage.delete_site(domain)
            return redirect(url_for("admin_panel"))

    def run(self) -> None:
        self.app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False)


class SyncWorker(threading.Thread):
    def __init__(self, sync: GitHubSync):
        super().__init__(daemon=True)
        self.sync = sync
        self.active = True

    def stop(self) -> None:
        self.active = False

    def run(self) -> None:
        while self.active:
            self.sync.pull_missing_files()
            time.sleep(SYNC_INTERVAL_SECONDS)


class ServerWorker(threading.Thread):
    def __init__(self, server: MaxNetServer):
        super().__init__(daemon=True)
        self.server = server

    def run(self) -> None:
        self.server.run()


def run_daemon_forever() -> None:
    while True:
        time.sleep(3600)


def open_webview() -> None:
    window = webview.create_window("MaxNet", f"http://127.0.0.1:{PORT}/", width=1280, height=800)
    webview.start(gui=None, debug=False)
    _ = window


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
    .row { display:flex; gap: 8px; align-items:center; margin-top: 8px; }
    .btn { display:inline-block; padding: 8px 12px; border-radius: 10px; background:#1344d4; color:#fff; border:0; }
    form.inline { display:inline; }
  </style>
</head>
<body>
  <h1>MaxNet — главная</h1>

  <div class="box">
    <form method="get" action="/">
      <input type="text" name="q" placeholder="Поиск по сайтам и доменам" value="{{ q }}" />
    </form>
    <div class="row">
      <a class="btn" href="/create">Создать сайт</a>
      <a class="btn" href="/chat">Мини-чат</a>
      <a class="btn" href="/admin">Админ-панель</a>
      <form class="inline" method="post" action="/sync/update"><button class="btn" type="submit">Обновить</button></form>
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
    code { background: #eff3ff; padding: 2px 4px; border-radius: 4px; }
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

  <div class="box">
    <h3>Важно про синхронизацию</h3>
    <p>MaxNet работает в режиме "только добавление":</p>
    <ul>
      <li>из GitHub добавляются только отсутствующие локально файлы;</li>
      <li>в GitHub отправляются только отсутствующие в репозитории файлы;</li>
      <li>удаление файлов из репозитория MaxNet не делает.</li>
    </ul>
    <p>Настройки GitHub задаются прямо в <code>main.py</code> (переменные вверху файла).</p>
  </div>
</body>
</html>
"""


CHAT_TEMPLATE = """
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Мини-чат — MaxNet</title>
  <style>
    body { font-family: system-ui, Arial, sans-serif; margin: 24px; background: #f4f6fb; color: #222; }
    .box { background: #fff; padding: 16px; border-radius: 12px; box-shadow: 0 1px 3px rgba(0,0,0,.1); margin-bottom: 16px; }
    input, textarea { width: 100%; padding: 10px; border-radius: 10px; border: 1px solid #ccc; margin-top:6px; }
    button { margin-top: 12px; padding: 10px 14px; border: 0; border-radius: 10px; background:#1344d4; color:#fff; }
    .msg { padding: 8px 0; border-bottom: 1px solid #eee; }
  </style>
</head>
<body>
  <h1>Мини-чат</h1>
  <a href="/">← Назад на главную</a>
  <div class="box">
    <form method="post" action="/chat/post">
      <label>Ник</label>
      <input type="text" name="nickname" placeholder="Ваш ник">
      <label>Сообщение</label>
      <textarea name="text" placeholder="Напишите сообщение..." required></textarea>
      <button type="submit">Отправить</button>
    </form>
  </div>
  <div class="box">
    <h3>Сообщения</h3>
    {% if messages %}
      {% for msg in messages|reverse %}
        <div class="msg"><b>{{ msg.nickname }}</b> ({{ msg.created_at }}):<br>{{ msg.text }}</div>
      {% endfor %}
    {% else %}
      <p>Пока сообщений нет.</p>
    {% endif %}
  </div>
</body>
</html>
"""


ADMIN_LOGIN_TEMPLATE = """
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Вход в админ-панель — MaxNet</title>
  <style>
    body { font-family: system-ui, Arial, sans-serif; margin: 24px; background: #f4f6fb; color: #222; }
    .box { background: #fff; padding: 16px; border-radius: 12px; box-shadow: 0 1px 3px rgba(0,0,0,.1); max-width: 480px; }
    input { width: 100%; padding: 10px; border-radius: 10px; border: 1px solid #ccc; margin-top:6px; }
    button { margin-top: 12px; padding: 10px 14px; border: 0; border-radius: 10px; background:#1344d4; color:#fff; }
    .err { color:#b30000; margin-top:8px; }
  </style>
</head>
<body>
  <h1>Вход в админ-панель</h1>
  <a href="/">← Назад на главную</a>
  <div class="box">
    <form method="post" action="/admin/login">
      <label>Пароль</label>
      <input type="password" name="password" required>
      <button type="submit">Войти</button>
      {% if error %}<div class="err">{{ error }}</div>{% endif %}
    </form>
  </div>
</body>
</html>
"""


ADMIN_PANEL_TEMPLATE = """
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Админ-панель — MaxNet</title>
  <style>
    body { font-family: system-ui, Arial, sans-serif; margin: 24px; background: #f4f6fb; color: #222; }
    .box { background: #fff; padding: 16px; border-radius: 12px; box-shadow: 0 1px 3px rgba(0,0,0,.1); margin-bottom: 16px; }
    .row { display:flex; gap: 8px; align-items:center; }
    .btn { display:inline-block; padding: 8px 12px; border-radius: 10px; background:#1344d4; color:#fff; text-decoration:none; border:0; }
    .danger { background:#b30000; }
  </style>
</head>
<body>
  <h1>Админ-панель</h1>
  <a href="/">← Назад на главную</a> | <a href="/admin/logout">Выйти</a>
  <div class="box">
    <h3>Сайты</h3>
    {% if sites %}
      {% for site in sites %}
        <div class="row" style="margin-bottom:8px">
          <div style="min-width:220px"><b>{{ site.title }}</b><br><small>{{ site.domain }}</small></div>
          <a class="btn" href="/admin/edit/{{ site.domain }}">Редактировать</a>
          <form method="post" action="/admin/delete/{{ site.domain }}" onsubmit="return confirm('Удалить сайт?');">
            <button class="btn danger" type="submit">Удалить</button>
          </form>
        </div>
      {% endfor %}
    {% else %}
      <p>Сайтов пока нет.</p>
    {% endif %}
  </div>
</body>
</html>
"""


ADMIN_EDIT_TEMPLATE = """
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Редактирование сайта — MaxNet</title>
  <style>
    body { font-family: system-ui, Arial, sans-serif; margin: 24px; background: #f4f6fb; color: #222; }
    .box { background: #fff; padding: 16px; border-radius: 12px; box-shadow: 0 1px 3px rgba(0,0,0,.1); margin-bottom: 16px; }
    input, textarea { width: 100%; padding: 10px; border-radius: 10px; border: 1px solid #ccc; margin-top:6px; }
    textarea { min-height: 360px; font-family: ui-monospace, monospace; }
    button { margin-top: 12px; padding: 10px 14px; border: 0; border-radius: 10px; background:#1344d4; color:#fff; }
  </style>
</head>
<body>
  <h1>Редактирование: {{ domain }}</h1>
  <a href="/admin">← Назад в админ-панель</a>
  <div class="box">
    <form method="post" action="/admin/edit/{{ domain }}">
      <label>Название</label>
      <input type="text" name="title" value="{{ title }}" required>
      <label>Код index.html</label>
      <textarea name="html" required>{{ html }}</textarea>
      <button type="submit">Сохранить</button>
    </form>
  </div>
</body>
</html>
"""


def run_app(with_gui: bool) -> None:
    storage = Storage()
    storage.rebuild_index_from_sites()

    sync = GitHubSync(storage)
    server = MaxNetServer(storage, sync)
    sync.pull_missing_files()
    sync.push_missing_files_only()

    server_worker = ServerWorker(server)
    server_worker.start()

    sync_worker = SyncWorker(sync)
    sync_worker.start()

    if with_gui:
        open_webview()
        return

    run_daemon_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="MaxNet launcher")
    parser.add_argument("--daemon", action="store_true", help="run only server + sync (no GUI)")
    args = parser.parse_args()

    run_app(with_gui=not args.daemon)


if __name__ == "__main__":
    main()
