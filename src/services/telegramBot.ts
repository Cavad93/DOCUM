import TelegramBot from 'node-telegram-bot-api';
import axios from 'axios';
import { ClaudeService } from './claudeService';
import { ArchiveService } from './archiveService';
import { PatientData, ClinicMode, BotContext, ExaminationTemplate } from '../types';
import { v4 as uuidv4 } from 'uuid';

export class MedicalBot {
  private bot: TelegramBot;
  private claudeService: ClaudeService;
  private archiveService: ArchiveService;
  private userContexts: Map<number, BotContext> = new Map();

  constructor(token: string, claudeApiKey: string) {
    this.bot = new TelegramBot(token, { polling: true });
    this.claudeService = new ClaudeService(claudeApiKey);
    this.archiveService = new ArchiveService();

    this.setupHandlers();
  }

  /**
   * Настройка обработчиков команд и сообщений
   */
  private setupHandlers(): void {
    // Команда /start
    this.bot.onText(/\/start/, (msg) => {
      const chatId = msg.chat.id;
      this.userContexts.set(chatId, {
        clinic: ClinicMode.DINASTIYA,
        awaitingData: false,
      });

      this.bot.sendMessage(
        chatId,
        `Добро пожаловать в медицинский бот! 🏥

Я помогу создать шаблон медицинского осмотра.

Доступные команды:
/clinic_dinastiya - переключить на клинику "Династия"
/clinic_pskp - переключить на клинику "ПСКП"
/stats - статистика архива
/help - помощь

Отправьте фото документа (СНИЛС) или введите данные пациента вручную в формате:
ФИО: Иванов Иван Иванович
Дата рождения: 01.01.1990
Диагноз: Описание диагноза`
      );
    });

    // Команда /help
    this.bot.onText(/\/help/, (msg) => {
      this.bot.sendMessage(
        msg.chat.id,
        `Помощь по использованию бота:

1. Выберите клинику командой /clinic_dinastiya или /clinic_pskp
2. Отправьте фото документа (СНИЛС) или введите данные текстом
3. Бот распознает данные и создаст шаблон осмотра
4. Шаблон будет сохранен в архиве

Формат текстовых данных:
ФИО: Иванов Иван Иванович
Дата рождения: 01.01.1990
СНИЛС: 123-456-789 00 (необязательно)
Диагноз: Описание диагноза`
      );
    });

    // Переключение клиники
    this.bot.onText(/\/clinic_dinastiya/, (msg) => {
      const chatId = msg.chat.id;
      const context = this.getOrCreateContext(chatId);
      context.clinic = ClinicMode.DINASTIYA;
      this.bot.sendMessage(chatId, '✅ Выбрана клиника "Династия"');
    });

    this.bot.onText(/\/clinic_pskp/, (msg) => {
      const chatId = msg.chat.id;
      const context = this.getOrCreateContext(chatId);
      context.clinic = ClinicMode.PSKP;
      this.bot.sendMessage(chatId, '✅ Выбрана клиника "ПСКП"');
    });

    // Статистика
    this.bot.onText(/\/stats/, async (msg) => {
      const chatId = msg.chat.id;
      try {
        const stats = await this.archiveService.getStatistics();
        let message = `📊 Статистика архива:\n\nВсего шаблонов: ${stats.total}\n\n`;

        for (const [clinic, count] of Object.entries(stats.byClinic)) {
          message += `${clinic}: ${count}\n`;
        }

        this.bot.sendMessage(chatId, message);
      } catch (error) {
        this.bot.sendMessage(chatId, '❌ Ошибка получения статистики');
      }
    });

    // Обработка фото
    this.bot.on('photo', async (msg) => {
      await this.handlePhoto(msg);
    });

    // Обработка текстовых сообщений
    this.bot.on('message', async (msg) => {
      if (msg.text && !msg.text.startsWith('/') && !msg.photo) {
        await this.handleTextMessage(msg);
      }
    });
  }

  /**
   * Обработка фото документа
   */
  private async handlePhoto(msg: TelegramBot.Message): Promise<void> {
    const chatId = msg.chat.id;
    const context = this.getOrCreateContext(chatId);

    try {
      await this.bot.sendMessage(chatId, '🔍 Обрабатываю фото документа...');

      if (!msg.photo || msg.photo.length === 0) {
        await this.bot.sendMessage(chatId, '❌ Фото не найдено');
        return;
      }

      // Получаем фото наилучшего качества
      const photo = msg.photo[msg.photo.length - 1];
      const fileId = photo.file_id;

      // Скачиваем фото
      const file = await this.bot.getFile(fileId);
      const filePath = file.file_path;

      if (!filePath) {
        await this.bot.sendMessage(chatId, '❌ Не удалось получить файл');
        return;
      }

      const fileUrl = `https://api.telegram.org/file/bot${this.bot.token}/${filePath}`;
      const response = await axios.get(fileUrl, { responseType: 'arraybuffer' });
      const imageBuffer = Buffer.from(response.data);
      const imageBase64 = imageBuffer.toString('base64');

      // Определяем тип медиа
      const mediaType = this.getMediaType(filePath);

      // Распознаем данные с фото
      const extractedData = await this.claudeService.extractDataFromImage(imageBase64, mediaType);

      // Сохраняем частичные данные в контекст
      context.patientData = {
        ...context.patientData,
        ...extractedData,
      };

      let message = '✅ Данные распознаны:\n\n';
      if (extractedData.fullName) message += `ФИО: ${extractedData.fullName}\n`;
      if (extractedData.birthDate) message += `Дата рождения: ${extractedData.birthDate}\n`;
      if (extractedData.snils) message += `СНИЛС: ${extractedData.snils}\n`;

      message += '\nТеперь отправьте диагноз или все данные текстом для создания шаблона.';

      await this.bot.sendMessage(chatId, message);
    } catch (error) {
      console.error('Error handling photo:', error);
      await this.bot.sendMessage(chatId, '❌ Ошибка обработки фото. Попробуйте еще раз.');
    }
  }

  /**
   * Обработка текстового сообщения
   */
  private async handleTextMessage(msg: TelegramBot.Message): Promise<void> {
    const chatId = msg.chat.id;
    const context = this.getOrCreateContext(chatId);

    if (!msg.text) return;

    try {
      await this.bot.sendMessage(chatId, '⚙️ Обрабатываю данные...');

      // Парсим текстовые данные
      const parsedData = this.parseTextData(msg.text);

      // Объединяем с данными из контекста (если были распознаны с фото)
      const patientData: PatientData = {
        fullName: parsedData.fullName || context.patientData?.fullName || '',
        birthDate: parsedData.birthDate || context.patientData?.birthDate || '',
        snils: parsedData.snils || context.patientData?.snils,
        diagnosis: parsedData.diagnosis || '',
      };

      // Проверка обязательных полей
      if (!patientData.fullName || !patientData.birthDate || !patientData.diagnosis) {
        await this.bot.sendMessage(
          chatId,
          '❌ Не хватает данных. Убедитесь, что указаны ФИО, дата рождения и диагноз.'
        );
        return;
      }

      // Ищем похожие шаблоны в архиве
      await this.bot.sendMessage(chatId, '🔎 Ищу похожие шаблоны в архиве...');
      const similarTemplates = await this.archiveService.getSimilarTemplates(
        patientData.diagnosis,
        context.clinic,
        3
      );

      // Генерируем шаблон
      await this.bot.sendMessage(chatId, '✍️ Создаю шаблон осмотра...');
      const templateContent = await this.claudeService.generateExaminationTemplate(
        patientData,
        context.clinic,
        similarTemplates
      );

      // Сохраняем в архив
      const template: ExaminationTemplate = {
        id: uuidv4(),
        clinic: context.clinic,
        patientData,
        content: templateContent,
        createdAt: new Date(),
      };

      await this.archiveService.saveTemplate(template);

      // Отправляем результат
      await this.bot.sendMessage(
        chatId,
        `✅ Шаблон осмотра создан для клиники "${context.clinic}"!\n\n${templateContent}`,
        { parse_mode: 'Markdown' }
      );

      // Очищаем контекст
      context.patientData = undefined;
    } catch (error) {
      console.error('Error handling text message:', error);
      await this.bot.sendMessage(chatId, '❌ Ошибка создания шаблона. Попробуйте еще раз.');
    }
  }

  /**
   * Парсинг текстовых данных пациента
   */
  private parseTextData(text: string): Partial<PatientData> {
    const data: Partial<PatientData> = {};

    const lines = text.split('\n');
    for (const line of lines) {
      const trimmedLine = line.trim();

      if (trimmedLine.match(/^фио:/i)) {
        data.fullName = trimmedLine.replace(/^фио:\s*/i, '').trim();
      } else if (trimmedLine.match(/^дата рождения:/i)) {
        data.birthDate = trimmedLine.replace(/^дата рождения:\s*/i, '').trim();
      } else if (trimmedLine.match(/^снилс:/i)) {
        data.snils = trimmedLine.replace(/^снилс:\s*/i, '').trim();
      } else if (trimmedLine.match(/^диагноз:/i)) {
        data.diagnosis = trimmedLine.replace(/^диагноз:\s*/i, '').trim();
      }
    }

    return data;
  }

  /**
   * Определение типа медиа по расширению файла
   */
  private getMediaType(filePath: string): string {
    if (filePath.endsWith('.jpg') || filePath.endsWith('.jpeg')) {
      return 'image/jpeg';
    } else if (filePath.endsWith('.png')) {
      return 'image/png';
    } else if (filePath.endsWith('.gif')) {
      return 'image/gif';
    } else if (filePath.endsWith('.webp')) {
      return 'image/webp';
    }
    return 'image/jpeg'; // по умолчанию
  }

  /**
   * Получение или создание контекста пользователя
   */
  private getOrCreateContext(chatId: number): BotContext {
    if (!this.userContexts.has(chatId)) {
      this.userContexts.set(chatId, {
        clinic: ClinicMode.DINASTIYA,
        awaitingData: false,
      });
    }
    return this.userContexts.get(chatId)!;
  }

  /**
   * Запуск бота
   */
  start(): void {
    console.log('🤖 Telegram bot started successfully!');
  }

  /**
   * Остановка бота
   */
  stop(): void {
    this.bot.stopPolling();
    console.log('🛑 Telegram bot stopped');
  }
}
