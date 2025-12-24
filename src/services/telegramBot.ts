import TelegramBot from 'node-telegram-bot-api';
import axios from 'axios';
import { ClaudeService } from './claudeService';
import { ArchiveService } from './archiveService';
import { PatientData, ClinicMode, BotContext, BotState, ExaminationTemplate } from '../types';
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
        state: BotState.IDLE,
      });

      const keyboard = {
        inline_keyboard: [
          [
            { text: '🏥 Династия', callback_data: 'clinic_dinastiya' },
            { text: '🏥 ПСКП', callback_data: 'clinic_pskp' },
          ],
        ],
      };

      this.bot.sendMessage(
        chatId,
        `Добро пожаловать в медицинский бот! 🏥

Я помогу создать шаблон медицинского осмотра.

Выберите клинику, затем отправьте:
• Фото документа (СНИЛС)
• Или текстовые данные в формате:
  ФИО: Иванов Иван Иванович
  Дата рождения: 01.01.1990
  Диагноз: Описание диагноза

Команды:
/stats - статистика архива
/help - помощь`,
        { reply_markup: keyboard }
      );
    });

    // Команда /help
    this.bot.onText(/\/help/, (msg) => {
      this.bot.sendMessage(
        msg.chat.id,
        `Помощь по использованию бота:

1. Выберите клинику кнопками
2. Отправьте фото документа (СНИЛС) или данные текстом
3. Проверьте созданный шаблон
4. Подтвердите или отправьте комментарии для правок
5. Шаблон будет сохранен в архиве

Формат текстовых данных:
ФИО: Иванов Иван Иванович
Дата рождения: 01.01.1990
СНИЛС: 123-456-789 00 (необязательно)
Диагноз: Описание диагноза`
      );
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

    // Обработка callback кнопок
    this.bot.on('callback_query', async (query) => {
      await this.handleCallbackQuery(query);
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
   * Обработка callback запросов (нажатия на кнопки)
   */
  private async handleCallbackQuery(query: TelegramBot.CallbackQuery): Promise<void> {
    const chatId = query.message?.chat.id;
    if (!chatId) return;

    const data = query.data;
    const context = this.getOrCreateContext(chatId);

    try {
      if (data === 'clinic_dinastiya') {
        context.clinic = ClinicMode.DINASTIYA;
        await this.bot.answerCallbackQuery(query.id);
        await this.bot.sendMessage(chatId, '✅ Выбрана клиника "Династия"');
      } else if (data === 'clinic_pskp') {
        context.clinic = ClinicMode.PSKP;
        await this.bot.answerCallbackQuery(query.id);
        await this.bot.sendMessage(chatId, '✅ Выбрана клиника "ПСКП"');
      } else if (data === 'confirm_template') {
        await this.handleConfirmTemplate(chatId, context);
        await this.bot.answerCallbackQuery(query.id, { text: 'Шаблон сохранен!' });
      } else if (data === 'request_corrections') {
        context.state = BotState.AWAITING_CORRECTIONS;
        await this.bot.answerCallbackQuery(query.id);
        await this.bot.sendMessage(
          chatId,
          '✏️ Отправьте комментарии для исправления шаблона.\n\nНапример:\n"Изменить диагноз на...\nДобавить в анамнез...\nУбрать из назначений..."'
        );
      }
    } catch (error) {
      console.error('Error handling callback:', error);
      await this.bot.answerCallbackQuery(query.id, { text: 'Ошибка обработки' });
    }
  }

  /**
   * Подтверждение и сохранение шаблона
   */
  private async handleConfirmTemplate(chatId: number, context: BotContext): Promise<void> {
    if (!context.currentTemplate) {
      await this.bot.sendMessage(chatId, '❌ Нет шаблона для сохранения');
      return;
    }

    try {
      // Сохраняем в архив
      const template: ExaminationTemplate = {
        id: uuidv4(),
        clinic: context.clinic,
        patientData: context.currentTemplate.patientData,
        content: context.currentTemplate.content,
        createdAt: new Date(),
      };

      await this.archiveService.saveTemplate(template);

      await this.bot.sendMessage(
        chatId,
        `✅ Шаблон успешно сохранен в архив!

Можете отправить данные следующего пациента.`
      );

      // Очищаем контекст
      context.state = BotState.IDLE;
      context.currentTemplate = undefined;
      context.patientData = undefined;
    } catch (error) {
      console.error('Error saving template:', error);
      await this.bot.sendMessage(chatId, '❌ Ошибка сохранения шаблона');
    }
  }

  /**
   * Обработка фото документа
   */
  private async handlePhoto(msg: TelegramBot.Message): Promise<void> {
    const chatId = msg.chat.id;
    const context = this.getOrCreateContext(chatId);

    // Игнорируем фото если ожидаем подтверждения или правок
    if (context.state === BotState.AWAITING_CONFIRMATION || context.state === BotState.AWAITING_CORRECTIONS) {
      return;
    }

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
      // Если ожидаем комментарии для правок
      if (context.state === BotState.AWAITING_CORRECTIONS) {
        await this.handleCorrections(chatId, context, msg.text);
        return;
      }

      // Если ожидаем подтверждения, игнорируем текстовые сообщения
      if (context.state === BotState.AWAITING_CONFIRMATION) {
        await this.bot.sendMessage(chatId, 'Пожалуйста, используйте кнопки для подтверждения или запроса правок.');
        return;
      }

      // Обычная обработка данных пациента
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

      // Генерируем шаблон
      await this.generateAndShowTemplate(chatId, context, patientData);
    } catch (error) {
      console.error('Error handling text message:', error);
      await this.bot.sendMessage(chatId, '❌ Ошибка обработки сообщения. Попробуйте еще раз.');
    }
  }

  /**
   * Генерация и показ шаблона с кнопками подтверждения
   */
  private async generateAndShowTemplate(
    chatId: number,
    context: BotContext,
    patientData: PatientData
  ): Promise<void> {
    try {
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

      // Сохраняем в контекст
      context.currentTemplate = {
        content: templateContent,
        patientData: patientData,
      };
      context.state = BotState.AWAITING_CONFIRMATION;

      // Создаем кнопки для подтверждения
      const keyboard = {
        inline_keyboard: [
          [
            { text: '✅ Всё ОК, сохранить', callback_data: 'confirm_template' },
            { text: '✏️ Нужны правки', callback_data: 'request_corrections' },
          ],
        ],
      };

      // Отправляем шаблон
      await this.bot.sendMessage(
        chatId,
        `✅ Шаблон осмотра создан для клиники "${context.clinic}"!\n\n${templateContent}`,
        { reply_markup: keyboard }
      );

      await this.bot.sendMessage(
        chatId,
        '👆 Проверьте шаблон и выберите действие:',
        { reply_markup: keyboard }
      );
    } catch (error) {
      console.error('Error generating template:', error);
      await this.bot.sendMessage(chatId, '❌ Ошибка создания шаблона. Попробуйте еще раз.');
      context.state = BotState.IDLE;
    }
  }

  /**
   * Обработка комментариев для правок
   */
  private async handleCorrections(chatId: number, context: BotContext, corrections: string): Promise<void> {
    if (!context.currentTemplate) {
      await this.bot.sendMessage(chatId, '❌ Нет шаблона для исправления');
      context.state = BotState.IDLE;
      return;
    }

    try {
      await this.bot.sendMessage(chatId, '🔄 Вношу исправления...');

      // Исправляем шаблон
      const correctedTemplate = await this.claudeService.correctTemplate(
        context.currentTemplate.content,
        context.currentTemplate.patientData,
        corrections
      );

      // Обновляем в контексте
      context.currentTemplate.content = correctedTemplate;
      context.state = BotState.AWAITING_CONFIRMATION;

      // Создаем кнопки для подтверждения
      const keyboard = {
        inline_keyboard: [
          [
            { text: '✅ Всё ОК, сохранить', callback_data: 'confirm_template' },
            { text: '✏️ Еще правки', callback_data: 'request_corrections' },
          ],
        ],
      };

      // Отправляем исправленный шаблон
      await this.bot.sendMessage(chatId, `✅ Шаблон исправлен!\n\n${correctedTemplate}`, {
        reply_markup: keyboard,
      });

      await this.bot.sendMessage(chatId, '👆 Проверьте исправленный шаблон:', { reply_markup: keyboard });
    } catch (error) {
      console.error('Error correcting template:', error);
      await this.bot.sendMessage(chatId, '❌ Ошибка исправления шаблона. Попробуйте еще раз.');
      context.state = BotState.AWAITING_CONFIRMATION;
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
        state: BotState.IDLE,
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
