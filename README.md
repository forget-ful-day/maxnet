# MaxNet

Локальная мини-поисковая система на Python.

## Установка библиотек

```bash
pip install Flask requests pywebview Werkzeug
```

## Запуск

```bash
python main.py
```

Фоновый режим (без GUI):

```bash
python main.py --daemon
```

## Как настроить GitHub синхронизацию

1. Создайте репозиторий на GitHub.
2. Создайте токен GitHub (Personal Access Token) с правами `repo`.
3. Откройте файл `~/Documents/MaxNet/config.json`.
4. Заполните поля:

```json
{
  "github_repo": "your_login/your_repo",
  "github_token": "ghp_xxx",
  "github_branch": "main",
  "bundle_path": "maxnet/sites_bundle.zip"
}
```

После публикации сайта (`/publish/simple` или `/publish/zip`) MaxNet автоматически отправляет bundle в GitHub и периодически подтягивает изменения с другого устройства.


## Если у вас ошибка `No module named PyQt6.QtWebEngineWidgets`

Вы запускаете старую версию файла (например, `15.py`) с зависимостью от PyQt6 WebEngine.
Текущая версия MaxNet работает через `pywebview` и запускается так:

```bash
python main.py
```

Убедитесь, что установлены зависимости именно для новой версии:

```bash
pip install Flask requests pywebview Werkzeug
```

`PyQt6-WebEngine` для этой версии **не нужен**.
