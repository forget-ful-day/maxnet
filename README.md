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

## Настройка GitHub в коде (через интерфейс)

1. Запустите MaxNet.
2. Откройте `Настройки GitHub` (`/settings/git`).
3. Заполните:
   - `github_repo` — формат `owner/repo`
   - `github_token` — PAT с правами `repo`
   - `github_branch` — обычно `main`
   - `github_root` — папка в репозитории, например `maxnet/sites`
4. Сохраните форму.

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
