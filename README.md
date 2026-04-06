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

## Настройка GitHub через переменные в коде

Откройте `main.py` и заполните переменные вверху файла:

- `GITHUB_REPO` — формат `owner/repo`
- `GITHUB_TOKEN` — PAT с правами `repo`
- `GITHUB_BRANCH` — обычно `main`
- `GITHUB_ROOT` — папка в репозитории, например `maxnet/sites`

## Режим синхронизации (только добавление)

MaxNet синхронизирует данные без удаления:

- Из GitHub скачивает **только отсутствующие локально** файлы.
- В GitHub загружает **только отсутствующие в репозитории** файлы.
- Существующие файлы не перезаписывает и не удаляет.

Это сделано, чтобы MaxNet не мог убирать файлы из репозитория, а только добавлял новые.

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
