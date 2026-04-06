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
