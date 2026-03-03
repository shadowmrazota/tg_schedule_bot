# Webhook Deployment Guide

## Что изменилось

Бот переведён с **polling** модели (бесконечный опрос Telegram) на **webhook** модель (ожидание HTTP POST запросов от Telegram). 

### Преимущества webhook для Render:
- ✓ Бот **может спать** без потери сообщений (Render не убивает приложение)
- ✓ Экономия приватного часа — сервер просыпается только при сообщениях
- ✓ Быстрее — Telegram сразу отправляет обновление на webhook

### Как это работает

```
Пользователь пишет боту
           ↓
Telegram → POST /webhook на ваш сервер
           ↓
Flask обрабатывает и вызывает обработчики (start, save_group)
           ↓
Бот отправляет ответ пользователю
```

## Локальное тестирование (необязательно)

Для тестирования локально используйте polling (временно):

```bash
# замените в конце tg_schedule_bot.py последнюю строку на:
# tg_app.run_polling()

# И запустите просто:
python tg_schedule_bot.py
```

## Развёртывание на Render

### 1. Подготовка репозитория (если ещё не сделали)

```bash
cd "c:\Users\glebs\VSCode projects\tgbot"
git init
git add .
git commit -m "initial: webhook bot"
git remote add origin https://github.com/YOUR_USERNAME/tgbot.git
git branch -M main
git push -u origin main
```

### 2. Проверьте .gitignore

Убедитесь, что `state.json` **не коммитится** (должен быть в .gitignore):

```
state.json
__pycache__/
*.pyc
.env
```

### 3. Создание сервиса на Render

1. Войдите на https://render.com
2. **New** → **Web Service**
3. Подключите свой GitHub репозиторий
4. Заполните:
   - **Name**: `telegram-schedule-bot` (или ваше имя)
   - **Runtime**: Python 3
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python tg_schedule_bot.py`

### 4. Установка переменных окружения

В Render, на странице сервиса:
1. **Environment** (слева)
2. **Add Environment Variable**
   - **Key**: `TG_TOKEN`
   - **Value**: ваш Telegram токен (от BotFather)

После добавления — **Deploy** (если не автоматический)

### 5. Получение webhook URL

После развёртывания (статус "Live"), URL вашего сервиса будет примерно:
```
https://telegram-schedule-bot.onrender.com
```

### 6. Регистрация webhook у Telegram

**На вашей машине** (локально):

```bash
cd "c:\Users\glebs\VSCode projects\tgbot"

# Проверьте текущий webhook (должен быть пустой):
python set_webhook.py YOUR_TOKEN -info

# Установите webhook:
python set_webhook.py YOUR_TOKEN https://telegram-schedule-bot.onrender.com/webhook
```

Ответ должен быть:
```
✓ Webhook успешно установлен: https://telegram-schedule-bot.onrender.com/webhook
```

## Тестирование

1. Откройте Telegram и отправьте боту `/start`
2. Проверьте логи на Render: **Logs** (справа сверху)
3. Если логи есть — webhook работает ✓

## Команды для управления webhook

```bash
# Информация о текущем webhook
python set_webhook.py YOUR_TOKEN -info

# Переключиться обратно на polling (удалить webhook)
python set_webhook.py YOUR_TOKEN -delete
```

## Структура файлов

```
tgbot/
├── tg_schedule_bot.py       # основной бот (теперь с Flask)
├── set_webhook.py           # утилита для регистрации webhook
├── requirements.txt         # зависимости (добавлен flask)
├── state.json              # сохранение пользователей (НЕ коммитить!)
├── .gitignore              # исключить state.json
└── README.md               # этот файл
```

## Если что-то не работает

### Render показывает ошибку при старте
- Проверьте `TG_TOKEN` в Environment переменных
- Посмотрите логи: **Logs** на странице сервиса

### Telegram не отправляет сообщения на webhook
```bash
# Проверьте, что webhook правильно зарегистрирован:
python set_webhook.py YOUR_TOKEN -info

# Должно быть:
#   Статус: Активен
#   URL: https://your-bot.onrender.com/webhook
```

### Сервер спит и бот не отвечает
Это нормально для Render free tier! 
- Первое сообщение может прийти с задержкой (пока сервер просыпается)
- Зато you не платите за uptime 😊

## Дальше

- Добавить функцию отслеживания изменений расписания (в новой версии)
- Покрыть тестами
- Добавить логирование действий пользователей

