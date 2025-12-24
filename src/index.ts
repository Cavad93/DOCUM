import * as dotenv from 'dotenv';
import { MedicalBot } from './services/telegramBot';

// Загрузка переменных окружения
dotenv.config();

// Проверка наличия необходимых переменных окружения
const TELEGRAM_BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN;
const ANTHROPIC_API_KEY = process.env.ANTHROPIC_API_KEY;

if (!TELEGRAM_BOT_TOKEN) {
  console.error('❌ TELEGRAM_BOT_TOKEN не найден в переменных окружения');
  console.error('Создайте файл .env и добавьте TELEGRAM_BOT_TOKEN=your_token');
  process.exit(1);
}

if (!ANTHROPIC_API_KEY) {
  console.error('❌ ANTHROPIC_API_KEY не найден в переменных окружения');
  console.error('Создайте файл .env и добавьте ANTHROPIC_API_KEY=your_key');
  process.exit(1);
}

// Создание и запуск бота
const bot = new MedicalBot(TELEGRAM_BOT_TOKEN, ANTHROPIC_API_KEY);
bot.start();

console.log(`
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
║   • Архивирование и поиск шаблонов                      ║
║                                                           ║
╚═══════════════════════════════════════════════════════════╝
`);

// Обработка сигналов завершения
process.on('SIGINT', () => {
  console.log('\n⏹️  Остановка бота...');
  bot.stop();
  process.exit(0);
});

process.on('SIGTERM', () => {
  console.log('\n⏹️  Остановка бота...');
  bot.stop();
  process.exit(0);
});
