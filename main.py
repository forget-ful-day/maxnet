#!/usr/bin/env python3
"""
maxnet - автономная локальная P2P-like система сайтов.

Один файл `main.py` запускает:
- локальный HTTP-сервер (хостинг страниц maxnet);
- API для публикации и поиска;
- синхронизацию между устройствами (через Telegram Bot API, опционально);
- автозапуск фонового режима (на Windows через Startup папку);
- встроенное окно приложения (через pywebview) вместо внешнего браузера.

Важно: это прототип "в стиле Chrome", но не полноценный движок Chromium.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import http.server
import io
import json
import os
import pathlib
import shutil
import socketserver
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

# ---------------------------
# Константы и пути
# ---------------------------
APP_NAME = "maxnet"
HOST = "127.0.0.1"
PORT = 9438
SYNC_INTERVAL_SEC = 90

DOCUMENTS_DIR = pathlib.Path.home() / "Documents"
DATA_DIR = DOCUMENTS_DIR / "maxnet_data"
SITES_DIR = DATA_DIR / "sites"
META_FILE = DATA_DIR / "sites.json"
CONFIG_FILE = DATA_DIR / "config.json"
LOCK_FILE = DATA_DIR / "instance.lock"
SNAPSHOT_FILE = DATA_DIR / "snapshot.zip"

SITE_DOMAIN_SUFFIX = ".maxnet"


# ---------------------------
# Модель
# ---------------------------
@dataclass
class SiteRecord:
    domain: str
    title: str
    path: str
    created_at: str
    updated_at: str
    description: str = ""


class Storage:
    def __init__(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        SITES_DIR.mkdir(parents=True, exist_ok=True)
        if not META_FILE.exists():
            self._write_meta({})
        if not CONFIG_FILE.exists():
            self._write_config(
                {
                    "first_run_done": False,
                    "telegram_token": "",
                    "telegram_chat_id": "",
                    "device_id": self._make_device_id(),
                    "last_remote_file_id": "",
                    "autostart_enabled": False,
                    "created_at": self._now_iso(),
                }
            )

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _make_device_id() -> str:
        seed = f"{os.getenv('COMPUTERNAME', '')}-{os.getenv('HOSTNAME', '')}-{time.time_ns()}"
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]

    def load_meta(self) -> Dict[str, SiteRecord]:
        data = json.loads(META_FILE.read_text(encoding="utf-8"))
        result: Dict[str, SiteRecord] = {}
        for k, v in data.items():
            result[k] = SiteRecord(**v)
        return result

    def _write_meta(self, data: Dict[str, dict]) -> None:
        META_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def save_meta(self, meta: Dict[str, SiteRecord]) -> None:
        serial = {k: asdict(v) for k, v in meta.items()}
        self._write_meta(serial)

    def load_config(self) -> dict:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))

    def _write_config(self, cfg: dict) -> None:
        CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    def save_config(self, cfg: dict) -> None:
        self._write_config(cfg)


storage = Storage()


# ---------------------------
# Утилиты
# ---------------------------
def sanitize_domain(raw: str) -> str:
    s = raw.strip().lower().replace(" ", "-")
    allowed = "abcdefghijklmnopqrstuvwxyz0123456789-"
    s = "".join(ch for ch in s if ch in allowed).strip("-")
    if not s:
        s = f"site-{int(time.time())}"
    return s


def ensure_domain_suffix(domain: str) -> str:
    if domain.endswith(SITE_DOMAIN_SUFFIX):
        return domain
    return f"{domain}{SITE_DOMAIN_SUFFIX}"


def slug_from_domain(domain: str) -> str:
    d = domain
    if d.endswith(SITE_DOMAIN_SUFFIX):
        d = d[: -len(SITE_DOMAIN_SUFFIX)]
    return sanitize_domain(d)


def zip_folder(src: pathlib.Path, dst_zip: pathlib.Path) -> None:
    with zipfile.ZipFile(dst_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in src.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(src))


def unzip_to(src_zip: pathlib.Path, dst: pathlib.Path) -> None:
    with zipfile.ZipFile(src_zip, "r") as zf:
        zf.extractall(dst)


def setup_windows_autostart(python_exe: str, script_path: str) -> bool:
    if os.name != "nt":
        return False
    startup = pathlib.Path(os.getenv("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    if not startup.exists():
        return False
    bat = startup / "maxnet_background.bat"
    cmd = f'@echo off\nstart "" "{python_exe}" "{script_path}" --background\n'
    bat.write_text(cmd, encoding="utf-8")
    return True


def read_request_body(handler: http.server.BaseHTTPRequestHandler) -> bytes:
    length = int(handler.headers.get("Content-Length", "0"))
    if length <= 0:
        return b""
    return handler.rfile.read(length)


# ---------------------------
# Синхронизация (Telegram)
# ---------------------------
class TelegramSync:
    def __init__(self, store: Storage) -> None:
        self.store = store
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.sync_once()
            except Exception as e:
                print(f"[sync] error: {e}")
            self.stop_event.wait(SYNC_INTERVAL_SEC)

    def sync_once(self) -> None:
        cfg = self.store.load_config()
        token = cfg.get("telegram_token", "").strip()
        chat_id = str(cfg.get("telegram_chat_id", "")).strip()
        if not token or not chat_id:
            return

        zip_folder(DATA_DIR, SNAPSHOT_FILE)
        self._upload_snapshot(token, chat_id, SNAPSHOT_FILE)
        remote = self._fetch_latest_snapshot(token, chat_id)
        if not remote:
            return

        file_id, file_url = remote
        if file_id == cfg.get("last_remote_file_id", ""):
            return

        with urllib.request.urlopen(file_url, timeout=60) as resp:
            raw = resp.read()

        tmp_zip = DATA_DIR / "remote_snapshot.zip"
        tmp_zip.write_bytes(raw)

        with tempfile.TemporaryDirectory(prefix="maxnet_merge_") as td:
            tdp = pathlib.Path(td)
            unzip_to(tmp_zip, tdp)
            self._merge_from(tdp)

        cfg["last_remote_file_id"] = file_id
        self.store.save_config(cfg)

    def _upload_snapshot(self, token: str, chat_id: str, zip_path: pathlib.Path) -> None:
        boundary = "----maxnet-boundary"
        fields = []

        def add_field(name: str, val: str) -> None:
            fields.append(f"--{boundary}\r\n".encode())
            fields.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n{val}\r\n'.encode())

        add_field("chat_id", chat_id)
        add_field("caption", f"maxnet snapshot {datetime.now().isoformat()}")

        filename = zip_path.name
        mime = "application/zip"
        fields.append(f"--{boundary}\r\n".encode())
        fields.append(
            f'Content-Disposition: form-data; name="document"; filename="{filename}"\r\n'.encode()
        )
        fields.append(f"Content-Type: {mime}\r\n\r\n".encode())
        fields.append(zip_path.read_bytes())
        fields.append(b"\r\n")
        fields.append(f"--{boundary}--\r\n".encode())

        body = b"".join(fields)
        req = urllib.request.Request(
            url=f"https://api.telegram.org/bot{token}/sendDocument",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as _:
            pass

    def _fetch_latest_snapshot(self, token: str, chat_id: str) -> Optional[Tuple[str, str]]:
        # Получаем последние апдейты и ищем последнее сообщение с zip документом в нужном чате
        updates_url = f"https://api.telegram.org/bot{token}/getUpdates"
        with urllib.request.urlopen(updates_url, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        if not data.get("ok"):
            return None

        best_file_id = ""
        for upd in reversed(data.get("result", [])):
            msg = upd.get("message") or upd.get("channel_post") or {}
            if str(msg.get("chat", {}).get("id", "")) != str(chat_id):
                continue
            doc = msg.get("document")
            if not doc:
                continue
            if not str(doc.get("file_name", "")).lower().endswith(".zip"):
                continue
            best_file_id = doc.get("file_id", "")
            if best_file_id:
                break

        if not best_file_id:
            return None

        fi_url = f"https://api.telegram.org/bot{token}/getFile?file_id={urllib.parse.quote(best_file_id)}"
        with urllib.request.urlopen(fi_url, timeout=30) as resp:
            fi_data = json.loads(resp.read().decode("utf-8"))
        if not fi_data.get("ok"):
            return None

        file_path = fi_data["result"]["file_path"]
        dl_url = f"https://api.telegram.org/file/bot{token}/{file_path}"
        return best_file_id, dl_url

    def _merge_from(self, other_data: pathlib.Path) -> None:
        other_meta_file = other_data / "sites.json"
        other_sites_dir = other_data / "sites"
        if not other_meta_file.exists() or not other_sites_dir.exists():
            return

        mine = storage.load_meta()
        theirs_raw = json.loads(other_meta_file.read_text(encoding="utf-8"))

        for domain, info in theirs_raw.items():
            try:
                rec = SiteRecord(**info)
            except Exception:
                continue
            my_rec = mine.get(domain)
            if not my_rec or rec.updated_at > my_rec.updated_at:
                src = other_sites_dir / rec.path
                dst = SITES_DIR / rec.path
                if src.exists():
                    if dst.exists():
                        shutil.rmtree(dst, ignore_errors=True)
                    shutil.copytree(src, dst)
                    mine[domain] = rec

        storage.save_meta(mine)


# ---------------------------
# HTTP/UI
# ---------------------------
def render_shell(title: str, body: str) -> bytes:
    doc = f"""<!doctype html>
<html lang=\"ru\">
<head>
<meta charset=\"utf-8\" />
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
<title>{html.escape(title)}</title>
<style>
:root {{ color-scheme: dark; }}
body {{ margin:0; font-family: Arial, sans-serif; background:#111; color:#eee; }}
header {{ position:sticky; top:0; background:#181818; padding:10px; border-bottom:1px solid #333; }}
input,button,select,textarea {{ background:#222; color:#fff; border:1px solid #444; border-radius:8px; padding:8px; }}
a {{ color:#7db3ff; text-decoration:none; }}
.container {{ max-width:1000px; margin:0 auto; padding:16px; }}
.card {{ background:#1a1a1a; border:1px solid #333; border-radius:12px; padding:12px; margin:10px 0; }}
.row {{ display:flex; gap:8px; flex-wrap:wrap; }}
iframe {{ width:100%; height:70vh; border:1px solid #333; border-radius:10px; background:white; }}
small {{ color:#aaa; }}
</style>
</head>
<body>
{body}
</body>
</html>"""
    return doc.encode("utf-8")


def main_page(q: str = "") -> bytes:
    meta = storage.load_meta()
    ql = q.strip().lower()

    rows = []
    for domain, rec in sorted(meta.items(), key=lambda kv: kv[1].updated_at, reverse=True):
        hay = f"{domain} {rec.title} {rec.description}".lower()
        if ql and ql not in hay:
            continue
        rows.append(
            f"""
<div class='card'>
  <h3>{html.escape(rec.title)} <small>{html.escape(domain)}</small></h3>
  <div>{html.escape(rec.description or '')}</div>
  <div class='row' style='margin-top:8px'>
    <a href='/go?domain={urllib.parse.quote(domain)}'>Открыть</a>
    <a href='/site/{urllib.parse.quote(rec.path)}/'>Файлы</a>
  </div>
</div>
"""
        )

    body = f"""
<header>
  <div class='row'>
    <form method='get' action='/' class='row'>
      <input name='q' placeholder='Поиск по сайтам и доменам' value='{html.escape(q)}' size='42'/>
      <button type='submit'>Найти</button>
    </form>
    <a href='/editor'><button>Создать сайт</button></a>
    <a href='/settings'><button>Настройки синхронизации</button></a>
  </div>
</header>
<div class='container'>
  <h2>MaxNet — главная страница сети</h2>
  <p>Здесь показаны все сайты системы (локальные + синхронизированные).</p>
  {''.join(rows) if rows else '<p><i>Пока пусто. Создайте первый сайт.</i></p>'}
</div>
"""
    return render_shell("MaxNet", body)


def editor_page(message: str = "") -> bytes:
    msg = f"<div class='card'>{html.escape(message)}</div>" if message else ""
    body = f"""
<header><div class='row'><a href='/'><button>← Главная</button></a></div></header>
<div class='container'>
  <h2>Публикация сайта</h2>
  {msg}
  <div class='card'>
    <form method='post' action='/publish'>
      <div class='row'>
        <input name='title' placeholder='Название сайта' required size='30'/>
        <input name='domain' placeholder='my-site (домен .maxnet добавится)' required size='30'/>
      </div>
      <p><input name='description' placeholder='Описание' size='80'/></p>
      <p><b>index.html</b></p>
      <textarea name='index_html' rows='14' style='width:100%;' placeholder='<!doctype html>...'></textarea>
      <p><button type='submit'>Опубликовать текстовый сайт</button></p>
    </form>
  </div>

  <div class='card'>
    <h3>Или загрузите ZIP архива сайта</h3>
    <form method='post' action='/upload_zip'>
      <p>Вставьте ZIP как Base64 (для простоты single-file API). В архиве должен быть index.html.</p>
      <input name='title' placeholder='Название сайта' required size='30'/>
      <input name='domain' placeholder='my-zip-site' required size='30'/>
      <input name='description' placeholder='Описание' size='60'/>
      <textarea name='zip_b64' rows='10' style='width:100%;' placeholder='UEsDB...'></textarea>
      <p><button type='submit'>Опубликовать ZIP</button></p>
    </form>
  </div>
</div>
"""
    return render_shell("Редактор MaxNet", body)


def settings_page(message: str = "") -> bytes:
    cfg = storage.load_config()
    msg = f"<div class='card'>{html.escape(message)}</div>" if message else ""
    body = f"""
<header><div class='row'><a href='/'><button>← Главная</button></a></div></header>
<div class='container'>
  <h2>Настройки</h2>
  {msg}
  <div class='card'>
    <form method='post' action='/save_settings'>
      <p>Telegram Bot Token: <input name='telegram_token' value='{html.escape(cfg.get('telegram_token',''))}' size='70'/></p>
      <p>Telegram Chat ID: <input name='telegram_chat_id' value='{html.escape(str(cfg.get('telegram_chat_id','')))}' size='30'/></p>
      <p><button type='submit'>Сохранить</button></p>
    </form>
    <small>Если заполнено, устройства будут обмениваться snapshot.zip через Telegram.</small>
  </div>
</div>
"""
    return render_shell("Настройки MaxNet", body)


class MaxnetHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        q = urllib.parse.parse_qs(parsed.query)

        if path == "/":
            self._send_html(main_page(q.get("q", [""])[0]))
            return
        if path == "/editor":
            self._send_html(editor_page())
            return
        if path == "/settings":
            self._send_html(settings_page())
            return
        if path == "/go":
            domain = q.get("domain", [""])[0]
            meta = storage.load_meta()
            rec = meta.get(domain)
            if rec:
                self.send_response(302)
                self.send_header("Location", f"/view/{urllib.parse.quote(rec.path)}/")
                self.end_headers()
            else:
                self._send_html(main_page())
            return
        if path.startswith("/view/"):
            slug = path[len("/view/") :].strip("/")
            iframe_src = f"/site/{urllib.parse.quote(slug)}/"
            self._send_html(
                render_shell(
                    f"View {slug}",
                    f"<header><div class='row'><a href='/'><button>← Главная</button></a></div></header>"
                    f"<div class='container'><h3>{html.escape(slug)}{SITE_DOMAIN_SUFFIX}</h3>"
                    f"<iframe src='{iframe_src}'></iframe></div>",
                )
            )
            return

        if path.startswith("/site/"):
            rel = urllib.parse.unquote(path[len("/site/") :]).lstrip("/")
            fs_path = SITES_DIR / rel
            if fs_path.is_dir():
                fs_path = fs_path / "index.html"
            if fs_path.exists() and fs_path.is_file():
                self._send_file(fs_path)
                return
            self.send_error(404, "Not found")
            return

        self.send_error(404, "Not found")

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        body = read_request_body(self)
        form = urllib.parse.parse_qs(body.decode("utf-8"), keep_blank_values=True)

        if parsed.path == "/publish":
            title = form.get("title", [""])[0].strip() or "Untitled"
            domain = ensure_domain_suffix(sanitize_domain(form.get("domain", [""])[0]))
            desc = form.get("description", [""])[0].strip()
            index_html = form.get("index_html", [""])[0].strip() or "<h1>Hello MaxNet</h1>"
            slug = slug_from_domain(domain)
            site_dir = SITES_DIR / slug
            site_dir.mkdir(parents=True, exist_ok=True)
            (site_dir / "index.html").write_text(index_html, encoding="utf-8")

            meta = storage.load_meta()
            now = datetime.now(timezone.utc).isoformat()
            created = meta[domain].created_at if domain in meta else now
            meta[domain] = SiteRecord(
                domain=domain,
                title=title,
                path=slug,
                created_at=created,
                updated_at=now,
                description=desc,
            )
            storage.save_meta(meta)
            self._redirect("/editor?ok=1")
            return

        if parsed.path == "/upload_zip":
            title = form.get("title", [""])[0].strip() or "ZIP Site"
            domain = ensure_domain_suffix(sanitize_domain(form.get("domain", [""])[0]))
            desc = form.get("description", [""])[0].strip()
            zip_b64 = form.get("zip_b64", [""])[0].strip()

            try:
                raw = base64.b64decode(zip_b64, validate=True)
            except Exception:
                self._send_html(editor_page("Ошибка: некорректный Base64 ZIP"))
                return

            slug = slug_from_domain(domain)
            site_dir = SITES_DIR / slug
            if site_dir.exists():
                shutil.rmtree(site_dir)
            site_dir.mkdir(parents=True, exist_ok=True)

            zpath = site_dir / "upload.zip"
            zpath.write_bytes(raw)
            try:
                unzip_to(zpath, site_dir)
            except Exception:
                shutil.rmtree(site_dir, ignore_errors=True)
                self._send_html(editor_page("Ошибка: ZIP не распакован"))
                return
            finally:
                if zpath.exists():
                    zpath.unlink(missing_ok=True)

            if not (site_dir / "index.html").exists():
                self._send_html(editor_page("Ошибка: в архиве должен быть index.html"))
                return

            meta = storage.load_meta()
            now = datetime.now(timezone.utc).isoformat()
            created = meta[domain].created_at if domain in meta else now
            meta[domain] = SiteRecord(
                domain=domain,
                title=title,
                path=slug,
                created_at=created,
                updated_at=now,
                description=desc,
            )
            storage.save_meta(meta)
            self._redirect("/")
            return

        if parsed.path == "/save_settings":
            token = form.get("telegram_token", [""])[0].strip()
            chat_id = form.get("telegram_chat_id", [""])[0].strip()
            cfg = storage.load_config()
            cfg["telegram_token"] = token
            cfg["telegram_chat_id"] = chat_id
            storage.save_config(cfg)
            self._send_html(settings_page("Сохранено"))
            return

        self.send_error(404, "Not found")

    def _redirect(self, where: str) -> None:
        self.send_response(302)
        self.send_header("Location", where)
        self.end_headers()

    def _send_html(self, data: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_file(self, p: pathlib.Path) -> None:
        ctype = self.guess_type(str(p))
        raw = p.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, fmt: str, *args) -> None:
        sys.stdout.write("[http] " + fmt % args + "\n")


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True


def run_http_server(stop_event: threading.Event) -> None:
    with ThreadedTCPServer((HOST, PORT), MaxnetHandler) as httpd:
        httpd.timeout = 1
        while not stop_event.is_set():
            httpd.handle_request()


def launch_ui() -> None:
    url = f"http://{HOST}:{PORT}/"
    try:
        import webview  # type: ignore

        webview.create_window("maxnet", url, width=1280, height=820, resizable=True)
        webview.start()
    except Exception as e:
        print(f"[ui] pywebview unavailable ({e}), fallback to terminal mode: {url}")
        print("Откройте ссылку вручную, если нужно.")
        while True:
            time.sleep(3600)


def acquire_single_instance() -> bool:
    try:
        LOCK_FILE.write_text(str(os.getpid()), encoding="utf-8")
        return True
    except Exception:
        return False


def run(background: bool) -> None:
    if not acquire_single_instance():
        print("maxnet уже запущен.")
        return

    cfg = storage.load_config()
    if not cfg.get("first_run_done"):
        enabled = setup_windows_autostart(sys.executable, os.path.abspath(__file__))
        cfg["autostart_enabled"] = bool(enabled)
        cfg["first_run_done"] = True
        storage.save_config(cfg)

    stop_event = threading.Event()
    server_thread = threading.Thread(target=run_http_server, args=(stop_event,), daemon=True)
    server_thread.start()

    sync = TelegramSync(storage)
    sync.start()

    mode = "background" if background else "ui"
    print(f"maxnet started in {mode} mode on http://{HOST}:{PORT}/")

    try:
        if background:
            while True:
                time.sleep(3600)
        else:
            launch_ui()
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        sync.stop()
        LOCK_FILE.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="maxnet single-file app")
    parser.add_argument("--background", action="store_true", help="run without visual window")
    args = parser.parse_args()
    run(background=args.background)


if __name__ == "__main__":
    main()
