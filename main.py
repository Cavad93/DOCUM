#!/usr/bin/env python3
"""
Telegram Medical Bot
Бот для автоматического создания медицинских шаблонов осмотра
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Добавляем путь к модулям
sys.path.insert(0, str(Path(__file__).parent))

from bot.telegram_bot import MedicalBot


def main():
    """Главная функция запуска бота"""
    # Загрузка переменных окружения
    load_dotenv()

    # Проверка наличия необходимых переменных
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    claude_api_key = os.getenv("ANTHROPIC_API_KEY")

    if not telegram_token:
        print("❌ TELEGRAM_BOT_TOKEN не найден в переменных окружения")
        print("Создайте файл .env и добавьте TELEGRAM_BOT_TOKEN=your_token")
        sys.exit(1)

    if not claude_api_key:
        print("❌ ANTHROPIC_API_KEY не найден в переменных окружения")
        print("Создайте файл .env и добавьте ANTHROPIC_API_KEY=your_key")
        sys.exit(1)

    # Баннер
    print("""
╔═══════════════════════════════════════════════════════════╗
║                                                           ║
║   🏥 Медицинский Telegram Бот запущен!                   ║
║                                                           ║
║   Поддерживаемые клиники:                                ║
║   • Династия                                             ║
║   • ПСКП                                                 ║
║                                                           ║
║   Возможности:                                           ║
║   • Распознавание фото документов (OCR)                  ║
║   • Создание шаблонов медицинского осмотра              ║
║   • Система подтверждения и правок                      ║
║   • Архивирование и поиск шаблонов                      ║
║                                                           ║
║   Модели Claude AI:                                      ║
║   • OCR: Claude Sonnet 4.5 (latest)                      ║
║   • Generation: Claude Opus 4                            ║
║                                                           ║
╚═══════════════════════════════════════════════════════════╝
    """)

    # Создание и запуск бота
    try:
        bot = MedicalBot(telegram_token, claude_api_key)
        bot.run()
    except KeyboardInterrupt:
        print("\n⏹️  Остановка бота...")
        sys.exit(0)
    except Exception as e:
        print(f"❌ Ошибка запуска бота: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
