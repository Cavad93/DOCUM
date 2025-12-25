#!/usr/bin/env python3
"""
Скрипт для проверки подключения к Telegram API
Используйте этот скрипт если бот не запускается
"""
import os
import sys
import asyncio
from dotenv import load_dotenv

async def test_telegram_connection():
    """Тестирование подключения к Telegram API"""
    print("🧪 Тест подключения к Telegram API\n")

    # Загрузка токена
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN")

    if not token:
        print("❌ TELEGRAM_BOT_TOKEN не найден в .env файле")
        return False

    print(f"✓ Токен найден: {token[:10]}...{token[-5:]}")

    # Попытка подключения
    print("\n📡 Попытка подключения к Telegram API...")

    try:
        from telegram import Bot
        from telegram.request import HTTPXRequest

        # Создаем request с увеличенным таймаутом
        request = HTTPXRequest(
            connection_pool_size=8,
            connect_timeout=30.0,
            read_timeout=30.0,
            write_timeout=30.0,
            pool_timeout=30.0,
        )

        bot = Bot(token=token, request=request)

        # Получаем информацию о боте
        print("⏳ Подключение... (может занять до 30 секунд)")
        me = await bot.get_me()

        print("\n✅ Подключение успешно!")
        print(f"🤖 Информация о боте:")
        print(f"   • Имя: @{me.username}")
        print(f"   • ID: {me.id}")
        print(f"   • Имя бота: {me.first_name}")

        return True

    except Exception as e:
        print(f"\n❌ Ошибка подключения: {e}")
        print("\n💡 Возможные причины:")
        print("1. Неправильный токен - проверьте .env файл")
        print("2. Нет доступа к интернету")
        print("3. Telegram заблокирован в вашей сети - нужен VPN")
        print("4. Бот не активирован через @BotFather")

        return False

def main():
    """Запуск теста"""
    try:
        result = asyncio.run(test_telegram_connection())
        sys.exit(0 if result else 1)
    except KeyboardInterrupt:
        print("\n⏹️  Тест прерван")
        sys.exit(0)

if __name__ == "__main__":
    main()
