
# Dedication Bot

Личный Telegram-бот для дисциплины: цели → привычки → ежедневная отметка + вечернее напоминание.
Работает на Windows, Linux Mint и macOS. Зависимости и Python ставит [uv](https://docs.astral.sh/uv/).

## 1. Подготовка

1. Создай бота у [@BotFather](https://t.me/BotFather) → получи токен.
2. Узнай свой user id у [@userinfobot](https://t.me/userinfobot).
3. Скопируй `.env.example` в `.env` и заполни.

## 2. Установка uv

**Linux Mint / macOS**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

На macOS можно и через Homebrew: `brew install uv`.

**Windows (PowerShell)**

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

## 3. Запуск (одинаково на всех ОС)

```bash
uv run bot.py
```

При первом запуске uv сам скачает нужный Python, создаст `.venv` и поставит зависимости из `uv.lock`.
Остановка: `Ctrl+C`. Данные лежат в `dedication.db` рядом с `bot.py`.

## 4. Автозапуск на сервере (по желанию)

На macOS бот удобно запускать вручную для проверки (`uv run bot.py`). Постоянно он работает на Mint или Windows.

> Один токен — один запущенный бот. Если бот уже работает на сервере, для проверки на Mac
> создай второго тестового бота у @BotFather и пропиши его токен в `.env` на Mac.
> Иначе Telegram вернёт ошибку `Conflict: terminated by other getUpdates request`.
> База `dedication.db` у каждой машины своя.

Сначала один раз выполни `uv sync`, чтобы появился `.venv`.

**Linux Mint — systemd user service.** Создай `~/.config/systemd/user/dedication.service`
(замени путь):

```ini
[Unit]
Description=Dedication bot
After=network-online.target

[Service]
WorkingDirectory=/home/USER/dedication_project
ExecStart=/home/USER/dedication_project/.venv/bin/python bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now dedication
loginctl enable-linger $USER   # работать даже без входа в систему
journalctl --user -u dedication -f   # логи
```

**Windows — Планировщик заданий.** Создать задачу → триггер «При входе в систему» →
действие «Запуск программы»:
- Программа: `C:\path\to\dedication_project\.venv\Scripts\pythonw.exe`
- Аргументы: `bot.py`
- Рабочая папка: `C:\path\to\dedication_project`

В настройках задачи включи «Перезапускать при сбое».

## Команды

| Команда | Что делает |
|---|---|
| `/today` | Привычки на сегодня, нажатие ✅/⬜ переключает |
| `/goal <название>` | Новая цель |
| `/habit` | Новая привычка: цель → название → дни недели |
| `/goals` | Цели, привычки и % выполнения за 7 дней |
| `/archive` | Убрать цель или привычку в архив |

## Тесты

```bash
uv run python -m unittest -v
```
