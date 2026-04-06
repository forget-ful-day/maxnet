#!/usr/bin/env python3
"""
MaxNet - Поисковая система с локальным хранилищем и синхронизацией через GitHub
Все настройки GitHub в коде (переменные)
"""

import os
import sys
import json
import zipfile
import shutil
import threading
import time
import hashlib
from pathlib import Path
from datetime import datetime

# Flask для сервера
from flask import Flask, request, jsonify, send_from_directory, render_template_string

# Git управление
import git

# WebView для отображения сайтов
try:
    import webview
    WEBVIEW_AVAILABLE = True
except ImportError:
    WEBVIEW_AVAILABLE = False

# Requests для работы с GitHub API
import requests

# ============================================================
# НАСТРОЙКИ GITHUB (измените эти переменные под себя)
# ============================================================
GITHUB_TOKEN = "your_github_token_here"  # Ваш токен GitHub
GITHUB_USERNAME = "your_username"         # Ваше имя пользователя
GITHUB_REPO_NAME = "maxnet-sites"         # Название репозитория
GITHUB_BRANCH = "main"                    # Ветка по умолчанию

# Пути
if sys.platform == 'win32':
    BASE_DIR = Path(os.environ['USERPROFILE']) / 'Documents' / 'maxnet'
else:
    BASE_DIR = Path.home() / 'Documents' / 'maxnet'

SITES_DIR = BASE_DIR / 'sites'
CONFIG_FILE = BASE_DIR / 'config.json'
REPO_DIR = BASE_DIR / 'repo'

# Сервер
SERVER_HOST = '127.0.0.1'
SERVER_PORT = 54321

# ============================================================
# Инициализация директорий
# ============================================================
def init_directories():
    """Создание необходимых директорий"""
    SITES_DIR.mkdir(parents=True, exist_ok=True)
    REPO_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        save_config({'sites': {}})

def load_config():
    """Загрузка конфигурации"""
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {'sites': {}}

def save_config(config):
    """Сохранение конфигурации"""
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

# ============================================================
# Работа с Git и GitHub
# ============================================================
def get_github_repo_url():
    """Получение URL репозитория с токеном"""
    return f"https://{GITHUB_TOKEN}@github.com/{GITHUB_USERNAME}/{GITHUB_REPO_NAME}.git"

def init_git_repo():
    """Инициализация локального репозитория"""
    if not (REPO_DIR / '.git').exists():
        try:
            repo = git.Repo.init(REPO_DIR)
            origin = repo.create_remote('origin', get_github_repo_url())
            return repo
        except Exception as e:
            print(f"Ошибка инициализации репозитория: {e}")
            return None
    return git.Repo(REPO_DIR)

def sync_with_github():
    """Синхронизация с GitHub - только добавление файлов, без удаления"""
    repo = init_git_repo()
    if not repo:
        return False
    
    try:
        # Пытаемся получить последние изменения
        try:
            repo.remotes.origin.fetch()
        except Exception as e:
            print(f"Не удалось получить изменения: {e}")
        
        # Проверяем файлы в репозитории
        github_files = set()
        local_files = set()
        
        # Получаем список файлов из удаленного репозитория
        try:
            for blob in repo.tree().traverse():
                if blob.type == 'blob':
                    github_files.add(blob.path)
        except Exception:
            pass
        
        # Получаем список локальных файлов
        for root, dirs, files in os.walk(SITES_DIR):
            for file in files:
                rel_path = os.path.relpath(os.path.join(root, file), SITES_DIR)
                local_files.add(rel_path)
        
        # Копируем новые файлы из репозитория (только тех которых нет локально)
        for file_path in github_files:
            local_file = SITES_DIR / file_path
            if not local_file.exists():
                try:
                    local_file.parent.mkdir(parents=True, exist_ok=True)
                    # Получаем файл из репозитория
                    blob = repo.tree()[file_path]
                    with open(local_file, 'wb') as f:
                        f.write(blob.data_stream.read())
                    print(f"Добавлен файл из репозитория: {file_path}")
                except Exception as e:
                    print(f"Ошибка копирования файла {file_path}: {e}")
        
        return True
    except Exception as e:
        print(f"Ошибка синхронизации: {e}")
        return False

def upload_sites_to_github():
    """Загрузка сайтов на GitHub (только добавление, без удаления)"""
    repo = init_git_repo()
    if not repo:
        return False
    
    try:
        # Копируем файлы сайтов в репозиторий
        for root, dirs, files in os.walk(SITES_DIR):
            for file in files:
                src_path = Path(root) / file
                rel_path = src_path.relative_to(SITES_DIR)
                dest_path = REPO_DIR / rel_path
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                
                # Копируем только если файл отличается или не существует
                if not dest_path.exists() or \
                   hashlib.md5(open(src_path, 'rb').read()).hexdigest() != \
                   hashlib.md5(open(dest_path, 'rb').read()).hexdigest():
                    shutil.copy2(src_path, dest_path)
        
        # Добавляем все новые файлы
        repo.git.add(A=True)
        
        # Проверяем есть ли изменения
        if repo.is_dirty() or repo.untracked_files:
            repo.index.commit(f"Auto-update: {datetime.now().isoformat()}")
            try:
                repo.remotes.origin.push(GITHUB_BRANCH)
                print("Файлы успешно загружены на GitHub")
                return True
            except Exception as e:
                print(f"Ошибка пуша: {e}")
                return False
        else:
            print("Нет изменений для загрузки")
            return True
    except Exception as e:
        print(f"Ошибка загрузки: {e}")
        return False

# ============================================================
# Flask сервер
# ============================================================
app = Flask(__name__)

@app.route('/')
def index():
    """Главная страница поисковой системы"""
    config = load_config()
    sites_html = ""
    for domain, site_info in config.get('sites', {}).items():
        sites_html += f'''
        <div class="site-card" onclick="loadSite('{domain}')">
            <h3>{domain}</h3>
            <p>{site_info.get('description', 'Нет описания')}</p>
            <small>Обновлено: {site_info.get('updated', 'Неизвестно')}</small>
        </div>
        '''
    
    html = f'''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>MaxNet - Главная</title>
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{ 
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
                padding: 20px;
            }}
            .container {{ max-width: 1200px; margin: 0 auto; }}
            h1 {{ 
                color: white; 
                text-align: center; 
                margin-bottom: 30px;
                font-size: 3em;
                text-shadow: 2px 2px 4px rgba(0,0,0,0.3);
            }}
            .search-bar {{
                display: flex;
                gap: 10px;
                margin-bottom: 30px;
                background: white;
                padding: 20px;
                border-radius: 10px;
                box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            }}
            .search-bar input {{
                flex: 1;
                padding: 15px;
                border: 2px solid #ddd;
                border-radius: 5px;
                font-size: 16px;
            }}
            .search-bar button {{
                padding: 15px 30px;
                background: #667eea;
                color: white;
                border: none;
                border-radius: 5px;
                cursor: pointer;
                font-size: 16px;
            }}
            .search-bar button:hover {{ background: #5568d3; }}
            .sites-grid {{
                display: grid;
                grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
                gap: 20px;
            }}
            .site-card {{
                background: white;
                padding: 20px;
                border-radius: 10px;
                cursor: pointer;
                transition: transform 0.2s, box-shadow 0.2s;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            }}
            .site-card:hover {{
                transform: translateY(-5px);
                box-shadow: 0 4px 12px rgba(0,0,0,0.2);
            }}
            .site-card h3 {{ color: #667eea; margin-bottom: 10px; }}
            .site-card p {{ color: #666; margin-bottom: 10px; }}
            .site-card small {{ color: #999; }}
            .upload-section {{
                background: white;
                padding: 20px;
                border-radius: 10px;
                margin-bottom: 30px;
                box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            }}
            .upload-section h2 {{ margin-bottom: 15px; color: #333; }}
            .upload-form {{ display: flex; flex-direction: column; gap: 10px; }}
            .upload-form input, .upload-form textarea {{
                padding: 10px;
                border: 2px solid #ddd;
                border-radius: 5px;
                font-size: 14px;
            }}
            .upload-form button {{
                padding: 12px;
                background: #764ba2;
                color: white;
                border: none;
                border-radius: 5px;
                cursor: pointer;
                font-size: 14px;
            }}
            .viewer-frame {{
                position: fixed;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                background: white;
                z-index: 1000;
                display: none;
            }}
            .viewer-header {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                padding: 10px 20px;
                background: #667eea;
                color: white;
            }}
            .viewer-header button {{
                padding: 8px 16px;
                background: #dc3545;
                color: white;
                border: none;
                border-radius: 5px;
                cursor: pointer;
            }}
            .viewer-content {{
                position: absolute;
                top: 50px;
                left: 0;
                width: 100%;
                height: calc(100% - 50px);
                border: none;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🌐 MaxNet</h1>
            
            <div class="search-bar">
                <input type="text" id="searchInput" placeholder="Поиск сайтов по названию или домену...">
                <button onclick="searchSites()">Поиск</button>
            </div>
            
            <div class="upload-section">
                <h2>📤 Опубликовать новый сайт</h2>
                <div class="upload-form">
                    <input type="text" id="domainName" placeholder="Домен (например: mysite.maxnet)">
                    <input type="text" id="siteDescription" placeholder="Описание сайта">
                    <input type="file" id="zipFile" accept=".zip">
                    <button onclick="uploadSite()">Загрузить ZIP архив</button>
                </div>
            </div>
            
            <div class="sites-grid" id="sitesGrid">
                {sites_html}
            </div>
        </div>
        
        <div class="viewer-frame" id="viewerFrame">
            <div class="viewer-header">
                <span id="viewerTitle">Просмотр сайта</span>
                <button onclick="closeViewer()">Закрыть</button>
            </div>
            <iframe class="viewer-content" id="viewerContent"></iframe>
        </div>
        
        <script>
            function searchSites() {{
                const query = document.getElementById('searchInput').value.toLowerCase();
                const cards = document.querySelectorAll('.site-card');
                cards.forEach(card => {{
                    const text = card.innerText.toLowerCase();
                    card.style.display = text.includes(query) ? 'block' : 'none';
                }});
            }}
            
            function loadSite(domain) {{
                fetch('/api/site/' + domain)
                    .then(r => r.json())
                    .then(data => {{
                        if (data.success) {{
                            document.getElementById('viewerContent').src = '/view/' + domain + '/index.html';
                            document.getElementById('viewerTitle').innerText = domain;
                            document.getElementById('viewerFrame').style.display = 'block';
                        }} else {{
                            alert('Ошибка: ' + data.error);
                        }}
                    }});
            }}
            
            function closeViewer() {{
                document.getElementById('viewerFrame').style.display = 'none';
                document.getElementById('viewerContent').src = '';
            }}
            
            function uploadSite() {{
                const domain = document.getElementById('domainName').value;
                const description = document.getElementById('siteDescription').value;
                const fileInput = document.getElementById('zipFile');
                
                if (!domain || !fileInput.files[0]) {{
                    alert('Введите домен и выберите ZIP файл');
                    return;
                }}
                
                const formData = new FormData();
                formData.append('domain', domain);
                formData.append('description', description);
                formData.append('file', fileInput.files[0]);
                
                fetch('/api/upload', {{
                    method: 'POST',
                    body: formData
                }})
                .then(r => r.json())
                .then(data => {{
                    if (data.success) {{
                        alert('Сайт успешно загружен!');
                        location.reload();
                    }} else {{
                        alert('Ошибка: ' + data.error);
                    }}
                }});
            }}
        </script>
    </body>
    </html>
    '''
    return html

@app.route('/api/upload', methods=['POST'])
def upload_site():
    """Загрузка сайта из ZIP архива"""
    domain = request.form.get('domain')
    description = request.form.get('description', '')
    
    if not domain:
        return jsonify({'success': False, 'error': 'Домен не указан'})
    
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'Файл не загружен'})
    
    file = request.files['file']
    if not file.filename.endswith('.zip'):
        return jsonify({'success': False, 'error': 'Файл должен быть ZIP архивом'})
    
    # Создаем директорию для сайта
    site_dir = SITES_DIR / domain
    site_dir.mkdir(parents=True, exist_ok=True)
    
    # Распаковываем архив
    try:
        with zipfile.ZipFile(file, 'r') as zip_ref:
            zip_ref.extractall(site_dir)
    except Exception as e:
        return jsonify({'success': False, 'error': f'Ошибка распаковки: {str(e)}'})
    
    # Обновляем конфигурацию
    config = load_config()
    config['sites'][domain] = {
        'description': description,
        'updated': datetime.now().isoformat(),
        'path': str(site_dir)
    }
    save_config(config)
    
    # Синхронизируем с GitHub
    threading.Thread(target=upload_sites_to_github).start()
    
    return jsonify({'success': True})

@app.route('/api/site/<domain>')
def get_site(domain):
    """Получение информации о сайте"""
    config = load_config()
    if domain in config.get('sites', {}):
        return jsonify({'success': True, 'info': config['sites'][domain]})
    return jsonify({'success': False, 'error': 'Сайт не найден'})

@app.route('/view/<domain>/<path:filename>')
def view_site(domain, filename):
    """Просмотр файлов сайта"""
    site_dir = SITES_DIR / domain
    if not site_dir.exists():
        return "Сайт не найден", 404
    
    filepath = site_dir / filename
    if not filepath.exists():
        return "Файл не найден", 404
    
    return send_from_directory(site_dir, filename)

@app.route('/api/sites')
def list_sites():
    """Список всех сайтов"""
    config = load_config()
    return jsonify(config.get('sites', {}))

def run_server():
    """Запуск Flask сервера"""
    app.run(host=SERVER_HOST, port=SERVER_PORT, debug=False, use_reloader=False)

# ============================================================
# Автозагрузка в трее (для Windows)
# ============================================================
def setup_autostart():
    """Настройка автозагрузки в трее"""
    if sys.platform == 'win32':
        import winreg
        startup_key = r"Software\Microsoft\Windows\CurrentVersion\Run"
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, startup_key, 0, winreg.KEY_SET_VALUE)
            script_path = os.path.abspath(__file__)
            winreg.SetValueEx(key, "MaxNet", 0, winreg.REG_SZ, f'python "{script_path}" --tray')
            winreg.CloseKey(key)
            print("Автозагрузка настроена")
        except Exception as e:
            print(f"Ошибка настройки автозагрузки: {e}")

def run_tray_mode():
    """Режим работы в трее (без графики, только сервер)"""
    print("Запуск в режиме трей...")
    init_directories()
    sync_with_github()
    
    # Запускаем сервер в фоне
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    
    print(f"Сервер запущен на http://{SERVER_HOST}:{SERVER_PORT}")
    print("Работает в фоновом режиме. Нажмите Ctrl+C для остановки.")
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Остановка сервера...")

# ============================================================
# Режим с WebView (графический интерфейс)
# ============================================================
def run_gui_mode():
    """Запуск с графическим интерфейсом через WebView"""
    init_directories()
    sync_with_github()
    
    # Запускаем сервер в отдельном потоке
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    time.sleep(2)  # Ждем запуска сервера
    
    if WEBVIEW_AVAILABLE:
        # Создаем окно WebView
        window = webview.create_window(
            'MaxNet - Поисковая система',
            f'http://{SERVER_HOST}:{SERVER_PORT}',
            width=1200,
            height=800,
            resizable=True,
            fullscreen=False
        )
        webview.start()
    else:
        # Если WebView недоступен, открываем в браузере
        import webbrowser
        webbrowser.open(f'http://{SERVER_HOST}:{SERVER_PORT}')
        print(f"Откройте браузер по адресу: http://{SERVER_HOST}:{SERVER_PORT}")
        
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("Остановка...")

# ============================================================
# Точка входа
# ============================================================
def main():
    """Основная функция"""
    if len(sys.argv) > 1 and sys.argv[1] == '--tray':
        # Режим трей (автозагрузка)
        run_tray_mode()
    else:
        # Обычный режим с графикой
        setup_autostart()
        run_gui_mode()

if __name__ == '__main__':
    main()
