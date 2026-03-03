#!/usr/bin/env python3
"""
Скрипт для регистрации webhook URL у Telegram.

Используйте после развёртывания бота на Render (или другом хостинге):
    python set_webhook.py <TELEGRAM_TOKEN> <WEBHOOK_URL>

Пример:
    python set_webhook.py 123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11 https://my-bot.onrender.com/webhook
"""

import sys
import requests

def set_webhook(token, webhook_url):
    """Register webhook URL with Telegram Bot API."""
    url = f"https://api.telegram.org/bot{token}/setWebhook"
    payload = {"url": webhook_url}
    
    response = requests.post(url, json=payload)
    data = response.json()
    
    if data.get("ok"):
        print(f"✓ Webhook успешно установлен: {webhook_url}")
        print(f"  Описание: {data.get('description')}")
        return True
    else:
        print(f"✗ Ошибка при установке webhook: {data.get('description')}")
        return False

def delete_webhook(token):
    """Delete webhook (go back to polling mode)."""
    url = f"https://api.telegram.org/bot{token}/deleteWebhook"
    response = requests.post(url)
    data = response.json()
    
    if data.get("ok"):
        print("✓ Webhook удалён.")
        return True
    else:
        print(f"✗ Ошибка при удалении webhook: {data.get('description')}")
        return False

def get_webhook_info(token):
    """Get current webhook info."""
    url = f"https://api.telegram.org/bot{token}/getWebhookInfo"
    response = requests.get(url)
    data = response.json()
    
    if data.get("ok"):
        info = data.get("result", {})
        print("Информация о webhook:")
        print(f"  URL: {info.get('url', 'Не установлен')}")
        print(f"  Статус: {'Активен' if info.get('url') else 'Неактивен'}")
        print(f"  Обновлений ожидается: {info.get('pending_update_count', 0)}")
        return True
    else:
        print(f"✗ Ошибка: {data.get('description')}")
        return False

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        print("\nДоступные команды:")
        print("  python set_webhook.py <TOKEN> <WEBHOOK_URL>  - Установить webhook")
        print("  python set_webhook.py <TOKEN> -delete         - Удалить webhook")
        print("  python set_webhook.py <TOKEN> -info           - Информация о webhook")
        sys.exit(1)
    
    token = sys.argv[1]
    
    if len(sys.argv) == 2:
        get_webhook_info(token)
    elif sys.argv[2] == "-delete":
        delete_webhook(token)
    elif sys.argv[2] == "-info":
        get_webhook_info(token)
    else:
        webhook_url = sys.argv[2]
        set_webhook(token, webhook_url)
