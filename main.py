#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MaxNet - Поисковая система с веб-интерфейсом
Хранение: ~/Documents/maxnet_sites
GitHub как хранилище файлов сайтов
"""

import os
import sys
import json
import zipfile
import shutil
import hashlib
import threading
import webview
import requests
import git
from datetime import datetime
from flask import Flask, render_template_string, request, jsonify, send_from_directory
from flask_socketio import SocketIO, emit
from flask_cors import CORS

# ==================== НАСТРОЙКИ (переменные в коде) ====================
GITHUB_TOKEN = "your_github_token_here"  # Ваш GitHub токен
GITHUB_USERNAME = "your_github_username"  # Ваш GitHub username
GITHUB_REPO = "maxnet-sites"  # Имя репозитория для хранения сайтов
ADMIN_PASSWORD = "admin123"  # Пароль администратора
BASE_DIR = os.path.expanduser("~/Documents/maxnet_sites")
SITES_DIR = os.path.join(BASE_DIR, "sites")
CHAT_FILE = os.path.join(BASE_DIR, "chat.json")
SITES_DB = os.path.join(BASE_DIR, "sites.json")
PORT = 5000
HOST = "0.0.0.0"
# =======================================================================

app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Глобальные переменные
current_window = None
server_thread = None

def ensure_dirs():
    """Создать необходимые директории"""
    os.makedirs(SITES_DIR, exist_ok=True)
    if not os.path.exists(CHAT_FILE):
        with open(CHAT_FILE, 'w', encoding='utf-8') as f:
            json.dump([], f)
    if not os.path.exists(SITES_DB):
        with open(SITES_DB, 'w', encoding='utf-8') as f:
            json.dump({}, f)

def load_sites():
    """Загрузить базу сайтов"""
    try:
        with open(SITES_DB, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {}

def save_sites(sites):
    """Сохранить базу сайтов"""
    with open(SITES_DB, 'w', encoding='utf-8') as f:
        json.dump(sites, f, indent=2, ensure_ascii=False)

def load_chat():
    """Загрузить чат"""
    try:
        with open(CHAT_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return []

def save_chat(messages):
    """Сохранить чат"""
    with open(CHAT_FILE, 'w', encoding='utf-8') as f:
        json.dump(messages, f, indent=2, ensure_ascii=False)

def get_github_repo_path():
    """Получить путь к локальной копии репозитория"""
    return os.path.join(BASE_DIR, "github_repo")

def init_git_repo():
    """Инициализировать или клонировать репозиторий"""
    repo_path = get_github_repo_path()
    
    if os.path.exists(repo_path):
        try:
            repo = git.Repo(repo_path)
            return repo
        except:
            shutil.rmtree(repo_path)
    
    # Клонирование или создание нового репозитория
    try:
        repo_url = f"https://{GITHUB_TOKEN}@github.com/{GITHUB_USERNAME}/{GITHUB_REPO}.git"
        repo = git.Repo.clone_from(repo_url, repo_path)
        return repo
    except:
        # Создаем новый репозиторий локально
        os.makedirs(repo_path, exist_ok=True)
        repo = git.Repo.init(repo_path)
        return repo

def sync_with_github():
    """Синхронизация с GitHub: проверка и добавление отсутствующих файлов"""
    try:
        repo = init_git_repo()
        
        # Добавляем все файлы из SITES_DIR в репозиторий
        for root, dirs, files in os.walk(SITES_DIR):
            for file in files:
                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, BASE_DIR)
                repo.git.add(rel_path)
        
        # Коммит изменений (только добавление)
        if repo.is_dirty():
            repo.index.commit(f"Auto-sync: {datetime.now().isoformat()}")
            
            # Push в GitHub
            try:
                origin = repo.remote(name='origin')
                origin.push()
            except:
                pass
        
        # Pull изменений с GitHub
        try:
            origin = repo.remote(name='origin')
            origin.pull()
            
            # Копируем новые файлы из репозитория в SITES_DIR
            repo_path = get_github_repo_path()
            for root, dirs, files in os.walk(os.path.join(repo_path, "sites")):
                for file in files:
                    src = os.path.join(root, file)
                    rel = os.path.relpath(src, os.path.join(repo_path, "sites"))
                    dst = os.path.join(SITES_DIR, rel)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    if not os.path.exists(dst):
                        shutil.copy2(src, dst)
        except:
            pass
            
        return True
    except Exception as e:
        print(f"Sync error: {e}")
        return False

@app.route('/')
def index():
    """Главная страница"""
    sites = load_sites()
    chat_messages = load_chat()
    
    html = '''
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MaxNet - Поисковая система</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        .container { max-width: 1200px; margin: 0 auto; }
        .header { 
            background: white; 
            border-radius: 15px; 
            padding: 30px; 
            margin-bottom: 30px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
        }
        .logo { 
            font-size: 48px; 
            font-weight: bold; 
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 20px;
        }
        .search-box { 
            width: 100%; 
            padding: 15px 25px; 
            border: 2px solid #e0e0e0; 
            border-radius: 50px; 
            font-size: 18px;
            transition: all 0.3s;
        }
        .search-box:focus { 
            outline: none; 
            border-color: #667eea;
            box-shadow: 0 0 20px rgba(102, 126, 234, 0.3);
        }
        .sites-grid { 
            display: grid; 
            grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); 
            gap: 20px; 
            margin-bottom: 30px;
        }
        .site-card { 
            background: white; 
            border-radius: 15px; 
            padding: 25px; 
            cursor: pointer;
            transition: all 0.3s;
            box-shadow: 0 5px 15px rgba(0,0,0,0.1);
        }
        .site-card:hover { 
            transform: translateY(-5px); 
            box-shadow: 0 15px 30px rgba(0,0,0,0.2);
        }
        .site-title { 
            font-size: 20px; 
            font-weight: bold; 
            color: #333; 
            margin-bottom: 10px;
        }
        .site-domain { 
            color: #667eea; 
            font-size: 14px; 
            margin-bottom: 10px;
        }
        .site-desc { color: #666; font-size: 14px; line-height: 1.5; }
        .actions { 
            display: flex; 
            gap: 10px; 
            margin-top: 20px; 
            flex-wrap: wrap;
        }
        .btn { 
            padding: 12px 25px; 
            border: none; 
            border-radius: 8px; 
            cursor: pointer; 
            font-size: 14px;
            font-weight: 600;
            transition: all 0.3s;
        }
        .btn-primary { 
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
            color: white; 
        }
        .btn-primary:hover { transform: scale(1.05); }
        .btn-secondary { background: #f0f0f0; color: #333; }
        .btn-secondary:hover { background: #e0e0e0; }
        .modal { 
            display: none; 
            position: fixed; 
            top: 0; 
            left: 0; 
            width: 100%; 
            height: 100%; 
            background: rgba(0,0,0,0.5); 
            z-index: 1000;
            justify-content: center;
            align-items: center;
        }
        .modal-content { 
            background: white; 
            border-radius: 15px; 
            padding: 40px; 
            max-width: 600px; 
            width: 90%;
            max-height: 80vh;
            overflow-y: auto;
        }
        .form-group { margin-bottom: 20px; }
        .form-group label { display: block; margin-bottom: 8px; font-weight: 600; }
        .form-group input, .form-group textarea, .form-group select { 
            width: 100%; 
            padding: 12px; 
            border: 2px solid #e0e0e0; 
            border-radius: 8px; 
            font-size: 14px;
        }
        .form-group textarea { min-height: 150px; resize: vertical; }
        .chat-container { 
            background: white; 
            border-radius: 15px; 
            padding: 25px; 
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
        }
        .chat-messages { 
            height: 300px; 
            overflow-y: auto; 
            border: 1px solid #e0e0e0; 
            border-radius: 8px; 
            padding: 15px; 
            margin-bottom: 15px;
            background: #f9f9f9;
        }
        .chat-message { 
            margin-bottom: 10px; 
            padding: 10px; 
            border-radius: 8px; 
            background: white;
        }
        .chat-author { font-weight: bold; color: #667eea; margin-bottom: 5px; }
        .chat-text { color: #333; }
        .chat-input { display: flex; gap: 10px; }
        .chat-input input { flex: 1; padding: 12px; border: 2px solid #e0e0e0; border-radius: 8px; }
        .hidden { display: none; }
        .admin-panel { background: #fff3cd; border: 2px solid #ffc107; }
        .delete-btn { background: #dc3545; color: white; }
        .delete-btn:hover { background: #c82333; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="logo">🔍 MaxNet</div>
            <input type="text" class="search-box" id="searchInput" placeholder="Поиск сайтов по названию или домену...">
            <div class="actions">
                <button class="btn btn-primary" onclick="showPublishModal()">📤 Опубликовать сайт</button>
                <button class="btn btn-secondary" onclick="showChat()">💬 Чат</button>
                <button class="btn btn-secondary" onclick="showAdminLogin()">⚙️ Админ панель</button>
                <button class="btn btn-primary" onclick="syncSites()">🔄 Обновить</button>
            </div>
        </div>
        
        <div class="sites-grid" id="sitesGrid"></div>
        
        <div class="chat-container hidden" id="chatContainer">
            <h2 style="margin-bottom: 20px;">💬 Мини-чат</h2>
            <div class="chat-messages" id="chatMessages"></div>
            <div class="chat-input">
                <input type="text" id="chatMessageInput" placeholder="Введите сообщение...">
                <button class="btn btn-primary" onclick="sendChatMessage()">Отправить</button>
                <button class="btn btn-secondary" onclick="hideChat()">Закрыть</button>
            </div>
        </div>
    </div>
    
    <!-- Модальное окно публикации -->
    <div class="modal" id="publishModal">
        <div class="modal-content">
            <h2 style="margin-bottom: 20px;">📤 Опубликовать сайт</h2>
            <div class="form-group">
                <label>Название сайта</label>
                <input type="text" id="siteTitle" placeholder="Мой сайт">
            </div>
            <div class="form-group">
                <label>Домен (уникальный)</label>
                <input type="text" id="siteDomain" placeholder="mysite.maxnet">
            </div>
            <div class="form-group">
                <label>Описание</label>
                <textarea id="siteDesc" placeholder="Описание вашего сайта..."></textarea>
            </div>
            <div class="form-group">
                <label>Загрузить ZIP архив с файлами сайта</label>
                <input type="file" id="siteZip" accept=".zip">
            </div>
            <div class="actions">
                <button class="btn btn-primary" onclick="publishSite()">Опубликовать</button>
                <button class="btn btn-secondary" onclick="closeModal('publishModal')">Отмена</button>
            </div>
        </div>
    </div>
    
    <!-- Модальное окно админ панели -->
    <div class="modal" id="adminModal">
        <div class="modal-content admin-panel">
            <h2 style="margin-bottom: 20px;">⚙️ Админ панель</h2>
            <div id="adminLogin">
                <div class="form-group">
                    <label>Пароль администратора</label>
                    <input type="password" id="adminPassword">
                </div>
                <button class="btn btn-primary" onclick="adminLogin()">Войти</button>
                <button class="btn btn-secondary" onclick="closeModal('adminModal')">Отмена</button>
            </div>
            <div id="adminContent" class="hidden">
                <div class="sites-grid" id="adminSitesGrid"></div>
                <button class="btn btn-secondary" onclick="closeModal('adminModal')">Закрыть</button>
            </div>
        </div>
    </div>
    
    <!-- Модальное окно редактирования сайта -->
    <div class="modal" id="editModal">
        <div class="modal-content">
            <h2 style="margin-bottom: 20px;">✏️ Редактировать сайт</h2>
            <input type="hidden" id="editSiteDomain">
            <div class="form-group">
                <label>Название сайта</label>
                <input type="text" id="editTitle">
            </div>
            <div class="form-group">
                <label>Описание</label>
                <textarea id="editDesc"></textarea>
            </div>
            <div class="form-group">
                <label>HTML код (index.html)</label>
                <textarea id="editHtml" style="min-height: 300px; font-family: monospace;"></textarea>
            </div>
            <div class="actions">
                <button class="btn btn-primary" onclick="saveEdit()">Сохранить</button>
                <button class="btn btn-danger delete-btn" onclick="deleteSite()">Удалить сайт</button>
                <button class="btn btn-secondary" onclick="closeModal('editModal')">Отмена</button>
            </div>
        </div>
    </div>
    
    <script>
        const sites = ''' + json.dumps(sites, ensure_ascii=False) + ''';
        const chatMessages = ''' + json.dumps(chat_messages, ensure_ascii=False) + ''';
        
        function renderSites(filter = '') {
            const grid = document.getElementById('sitesGrid');
            grid.innerHTML = '';
            
            Object.values(sites).forEach(site => {
                if (filter && !site.title.toLowerCase().includes(filter.toLowerCase()) && 
                    !site.domain.toLowerCase().includes(filter.toLowerCase())) {
                    return;
                }
                
                const card = document.createElement('div');
                card.className = 'site-card';
                card.innerHTML = `
                    <div class="site-title">${site.title}</div>
                    <div class="site-domain">${site.domain}</div>
                    <div class="site-desc">${site.description}</div>
                `;
                card.onclick = () => openSite(site.domain);
                grid.appendChild(card);
            });
        }
        
        function openSite(domain) {
            fetch('/site/' + domain)
                .then(r => r.json())
                .then(data => {
                    if (data.success) {
                        // Открываем сайт в WebView
                        window.location.href = '/view/' + domain;
                    } else {
                        alert('Сайт не найден');
                    }
                });
        }
        
        function showPublishModal() {
            document.getElementById('publishModal').style.display = 'flex';
        }
        
        function closeModal(id) {
            document.getElementById(id).style.display = 'none';
        }
        
        function publishSite() {
            const title = document.getElementById('siteTitle').value;
            const domain = document.getElementById('siteDomain').value;
            const desc = document.getElementById('siteDesc').value;
            const zipFile = document.getElementById('siteZip').files[0];
            
            if (!title || !domain) {
                alert('Введите название и домен');
                return;
            }
            
            const formData = new FormData();
            formData.append('title', title);
            formData.append('domain', domain);
            formData.append('description', desc);
            if (zipFile) formData.append('zip', zipFile);
            
            fetch('/publish', {
                method: 'POST',
                body: formData
            })
            .then(r => r.json())
            .then(data => {
                if (data.success) {
                    alert('Сайт опубликован!');
                    location.reload();
                } else {
                    alert('Ошибка: ' + data.error);
                }
            });
        }
        
        function showChat() {
            document.getElementById('chatContainer').classList.remove('hidden');
            renderChat();
        }
        
        function hideChat() {
            document.getElementById('chatContainer').classList.add('hidden');
        }
        
        function renderChat() {
            const container = document.getElementById('chatMessages');
            container.innerHTML = '';
            chatMessages.forEach(msg => {
                const div = document.createElement('div');
                div.className = 'chat-message';
                div.innerHTML = `
                    <div class="chat-author">${msg.author}</div>
                    <div class="chat-text">${msg.text}</div>
                `;
                container.appendChild(div);
            });
            container.scrollTop = container.scrollHeight;
        }
        
        function sendChatMessage() {
            const input = document.getElementById('chatMessageInput');
            const text = input.value.trim();
            if (!text) return;
            
            fetch('/chat', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({author: 'User', text: text})
            })
            .then(r => r.json())
            .then(data => {
                if (data.success) {
                    chatMessages.push({author: 'User', text: text, time: new Date().toISOString()});
                    renderChat();
                    input.value = '';
                }
            });
        }
        
        function showAdminLogin() {
            document.getElementById('adminModal').style.display = 'flex';
        }
        
        function adminLogin() {
            const password = document.getElementById('adminPassword').value;
            fetch('/admin/login', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({password: password})
            })
            .then(r => r.json())
            .then(data => {
                if (data.success) {
                    document.getElementById('adminLogin').classList.add('hidden');
                    document.getElementById('adminContent').classList.remove('hidden');
                    renderAdminSites();
                } else {
                    alert('Неверный пароль');
                }
            });
        }
        
        function renderAdminSites() {
            const grid = document.getElementById('adminSitesGrid');
            grid.innerHTML = '';
            
            Object.entries(sites).forEach(([domain, site]) => {
                const card = document.createElement('div');
                card.className = 'site-card';
                card.innerHTML = `
                    <div class="site-title">${site.title}</div>
                    <div class="site-domain">${site.domain}</div>
                    <div class="site-desc">${site.description}</div>
                    <div class="actions">
                        <button class="btn btn-primary" onclick="editSite('${domain}')">Редактировать</button>
                    </div>
                `;
                grid.appendChild(card);
            });
        }
        
        function editSite(domain) {
            fetch('/admin/site/' + domain)
                .then(r => r.json())
                .then(data => {
                    if (data.success) {
                        document.getElementById('editSiteDomain').value = domain;
                        document.getElementById('editTitle').value = data.site.title;
                        document.getElementById('editDesc').value = data.site.description;
                        document.getElementById('editHtml').value = data.site.html || '';
                        document.getElementById('editModal').style.display = 'flex';
                    }
                });
        }
        
        function saveEdit() {
            const domain = document.getElementById('editSiteDomain').value;
            const title = document.getElementById('editTitle').value;
            const desc = document.getElementById('editDesc').value;
            const html = document.getElementById('editHtml').value;
            
            fetch('/admin/edit', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({domain, title, description: desc, html: html})
            })
            .then(r => r.json())
            .then(data => {
                if (data.success) {
                    alert('Сохранено!');
                    location.reload();
                } else {
                    alert('Ошибка: ' + data.error);
                }
            });
        }
        
        function deleteSite() {
            const domain = document.getElementById('editSiteDomain').value;
            if (!confirm('Вы уверены, что хотите удалить этот сайт?')) return;
            
            fetch('/admin/delete', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({domain: domain})
            })
            .then(r => r.json())
            .then(data => {
                if (data.success) {
                    alert('Сайт удалён');
                    location.reload();
                } else {
                    alert('Ошибка: ' + data.error);
                }
            });
        }
        
        function syncSites() {
            fetch('/sync', {method: 'POST'})
                .then(r => r.json())
                .then(data => {
                    if (data.success) {
                        alert('Синхронизация завершена!');
                        location.reload();
                    } else {
                        alert('Ошибка синхронизации: ' + data.error);
                    }
                });
        }
        
        document.getElementById('searchInput').addEventListener('input', (e) => {
            renderSites(e.target.value);
        });
        
        renderSites();
    </script>
</body>
</html>
'''
    return html

@app.route('/publish', methods=['POST'])
def publish():
    """Публикация сайта"""
    title = request.form.get('title')
    domain = request.form.get('domain')
    description = request.form.get('description', '')
    
    if not title or not domain:
        return jsonify({'success': False, 'error': 'Название и домен обязательны'})
    
    sites = load_sites()
    if domain in sites:
        return jsonify({'success': False, 'error': 'Домен уже занят'})
    
    # Создание директории сайта
    site_dir = os.path.join(SITES_DIR, domain)
    os.makedirs(site_dir, exist_ok=True)
    
    # Обработка ZIP архива
    if 'zip' in request.files:
        zip_file = request.files['zip']
        with zipfile.ZipFile(zip_file, 'r') as zf:
            zf.extractall(site_dir)
    
    # Создание index.html если нет
    index_path = os.path.join(site_dir, 'index.html')
    if not os.path.exists(index_path):
        with open(index_path, 'w', encoding='utf-8') as f:
            f.write(f'<h1>{title}</h1><p>{description}</p>')
    
    # Сохранение в базу
    sites[domain] = {
        'title': title,
        'domain': domain,
        'description': description,
        'created': datetime.now().isoformat()
    }
    save_sites(sites)
    
    # Синхронизация с GitHub
    sync_with_github()
    
    return jsonify({'success': True})

@app.route('/site/<domain>')
def get_site(domain):
    """Получить информацию о сайте"""
    sites = load_sites()
    if domain not in sites:
        return jsonify({'success': False})
    return jsonify({'success': True, 'site': sites[domain]})

@app.route('/view/<domain>')
def view_site(domain):
    """Просмотр сайта"""
    site_dir = os.path.join(SITES_DIR, domain)
    index_path = os.path.join(site_dir, 'index.html')
    
    if not os.path.exists(index_path):
        return '<h1>404 - Сайт не найден</h1>'
    
    with open(index_path, 'r', encoding='utf-8') as f:
        return f.read()

@app.route('/site/<domain>/<path:filename>')
def site_static(domain, filename):
    """Статические файлы сайта"""
    site_dir = os.path.join(SITES_DIR, domain)
    return send_from_directory(site_dir, filename)

@app.route('/chat', methods=['POST'])
def chat():
    """Отправить сообщение в чат"""
    data = request.json
    message = {
        'author': data.get('author', 'Anonymous'),
        'text': data.get('text', ''),
        'time': datetime.now().isoformat()
    }
    
    messages = load_chat()
    messages.append(message)
    save_chat(messages)
    
    socketio.emit('new_message', message)
    
    return jsonify({'success': True})

@socketio.on('connect')
def connect():
    emit('chat_history', load_chat())

@app.route('/admin/login', methods=['POST'])
def admin_login():
    """Вход в админ панель"""
    data = request.json
    if data.get('password') == ADMIN_PASSWORD:
        return jsonify({'success': True})
    return jsonify({'success': False})

@app.route('/admin/site/<domain>')
def admin_get_site(domain):
    """Получить сайт для редактирования"""
    sites = load_sites()
    if domain not in sites:
        return jsonify({'success': False})
    
    site_dir = os.path.join(SITES_DIR, domain)
    index_path = os.path.join(site_dir, 'index.html')
    
    html_content = ''
    if os.path.exists(index_path):
        with open(index_path, 'r', encoding='utf-8') as f:
            html_content = f.read()
    
    return jsonify({'success': True, 'site': {**sites[domain], 'html': html_content}})

@app.route('/admin/edit', methods=['POST'])
def admin_edit():
    """Редактирование сайта"""
    data = request.json
    domain = data.get('domain')
    
    sites = load_sites()
    if domain not in sites:
        return jsonify({'success': False, 'error': 'Сайт не найден'})
    
    # Обновление информации
    sites[domain]['title'] = data.get('title', sites[domain]['title'])
    sites[domain]['description'] = data.get('description', sites[domain]['description'])
    save_sites(sites)
    
    # Обновление HTML
    site_dir = os.path.join(SITES_DIR, domain)
    index_path = os.path.join(site_dir, 'index.html')
    html_content = data.get('html', '')
    
    with open(index_path, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    sync_with_github()
    
    return jsonify({'success': True})

@app.route('/admin/delete', methods=['POST'])
def admin_delete():
    """Удаление сайта"""
    data = request.json
    domain = data.get('domain')
    
    sites = load_sites()
    if domain not in sites:
        return jsonify({'success': False, 'error': 'Сайт не найден'})
    
    # Удаление файлов
    site_dir = os.path.join(SITES_DIR, domain)
    if os.path.exists(site_dir):
        shutil.rmtree(site_dir)
    
    # Удаление из базы
    del sites[domain]
    save_sites(sites)
    
    sync_with_github()
    
    return jsonify({'success': True})

@app.route('/sync', methods=['POST'])
def sync():
    """Синхронизация с GitHub"""
    success = sync_with_github()
    if success:
        return jsonify({'success': True})
    return jsonify({'success': False, 'error': 'Ошибка синхронизации'})

def run_server():
    """Запуск сервера"""
    ensure_dirs()
    sync_with_github()  # Синхронизация при запуске
    socketio.run(app, host=HOST, port=PORT, debug=False, log_output=False)

def run_webview():
    """Запуск WebView"""
    global current_window
    
    api = webview.create_window(
        'MaxNet',
        f'http://localhost:{PORT}',
        width=1200,
        height=800,
        resizable=True
    )
    
    current_window = api
    webview.start()

if __name__ == '__main__':
    print("🚀 Запуск MaxNet...")
    print(f"📁 Хранение: {BASE_DIR}")
    print(f"🌐 GitHub: {GITHUB_USERNAME}/{GITHUB_REPO}")
    print(f"🔑 Admin пароль: {ADMIN_PASSWORD}")
    
    # Запуск сервера в отдельном потоке
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    
    # Небольшая задержка для запуска сервера
    import time
    time.sleep(2)
    
    # Запуск WebView
    run_webview()
