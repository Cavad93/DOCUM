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
    # Загрузка переменных окружения. override=True — значение из .env
    # имеет приоритет над уже выставленной системной переменной (важно
    # на Windows, где старая ANTHROPIC_API_KEY могла остаться после setx).
    load_dotenv(override=True)

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

    # Email настройки (опциональные)
    smtp_host = os.getenv("SMTP_HOST", "smtp.yandex.ru")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_password = os.getenv("SMTP_PASSWORD", "")

    # Битрикс24 настройки (опциональные, нужны для режима ГОДОК)
    bitrix_webhook = os.getenv("BITRIX_WEBHOOK_URL", "")

    # Баннер
    print("""
╔═══════════════════════════════════════════════════════════╗
║                                                           ║
║   🏥 Медицинский Telegram Бот запущен!                   ║
║                                                           ║
║   Поддерживаемые клиники:                                ║
║   • Династия                                             ║
║   • ПСКП                                                 ║
║   • ГОДОК (Битрикс24)                                    ║
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
        print("🔧 Инициализация бота...")
        bot = MedicalBot(
            telegram_token=telegram_token,
            claude_api_key=claude_api_key,
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            smtp_user=smtp_user,
            smtp_password=smtp_password,
            bitrix_webhook=bitrix_webhook,
        )
        print("✅ Бот успешно инициализирован")
        print("🚀 Бот работает! Нажмите Ctrl+C для остановки\n")
        bot.run()
    except KeyboardInterrupt:
        print("\n⏹️  Остановка бота...")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Критическая ошибка: {e}")
        print("\n🔍 Для диагностики проверьте:")
        print("  • Файл .env существует и содержит правильные токены")
        print("  • Интернет-соединение работает")
        print("  • Telegram доступен (не заблокирован)")
        sys.exit(1)


if __name__ == "__main__":
    main()
