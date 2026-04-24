import asyncio
import base64
import re
import uuid
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from docx import Document

from bot.models.types import PatientData, ClinicMode, BotState, BotContext, ExaminationTemplate, CurrentTemplate, QueuedDocument
from bot.services.claude_service import ClaudeService
from bot.services.archive_service import ArchiveService
from bot.services.email_service import EmailService
from bot.services.document_queue_service import DocumentQueueService
from bot.services.bitrix_service import BitrixService, BitrixError


class MedicalBot:
    """Telegram бот для создания медицинских шаблонов"""

    def __init__(
        self,
        telegram_token: str,
        claude_api_key: str,
        smtp_host: str = "",
        smtp_port: int = 587,
        smtp_user: str = "",
        smtp_password: str = "",
        bitrix_webhook: str = ""
    ):
        """
        Инициализация бота

        Args:
            telegram_token: Токен Telegram бота
            claude_api_key: API ключ Claude
            smtp_host: SMTP сервер для email
            smtp_port: Порт SMTP
            smtp_user: Email отправителя
            smtp_password: Пароль от email
            bitrix_webhook: URL входящего вебхука Битрикс24 (для ГОДОК)
        """
        # Создание приложения с увеличенными таймаутами и retry логикой
        self.application = (
            Application.builder()
            .token(telegram_token)
            .connect_timeout(30.0)  # Таймаут подключения 30 секунд
            .read_timeout(30.0)  # Таймаут чтения 30 секунд
            .write_timeout(30.0)  # Таймаут записи 30 секунд
            .pool_timeout(30.0)  # Таймаут пула 30 секунд
            .build()
        )
        self.claude_service = ClaudeService(claude_api_key)
        self.archive_service = ArchiveService()
        self.document_queue_service = DocumentQueueService()
        self.user_contexts: Dict[int, BotContext] = {}

        # Email сервис (опциональный)
        if smtp_user and smtp_password:
            self.email_service = EmailService(smtp_host, smtp_port, smtp_user, smtp_password)
            print("✓ Email сервис инициализирован")
        else:
            self.email_service = None
            print("⚠️ Email не настроен (добавьте SMTP_USER и SMTP_PASSWORD в .env)")

        # Битрикс24 сервис (опциональный — нужен для режима ГОДОК)
        if bitrix_webhook:
            try:
                self.bitrix_service = BitrixService(bitrix_webhook)
                print("✓ Битрикс24 сервис инициализирован")
            except Exception as e:
                print(f"⚠️ Битрикс24 не инициализирован: {e}")
                self.bitrix_service = None
        else:
            self.bitrix_service = None
            print("⚠️ Битрикс24 не настроен (добавьте BITRIX_WEBHOOK_URL в .env для режима ГОДОК)")

        self._setup_handlers()

    def _setup_handlers(self):
        """Настройка обработчиков команд и сообщений"""
        # Команды
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("stats", self.stats_command))
        self.application.add_handler(CommandHandler("cancel", self.cancel_command))

        # Email команды
        self.application.add_handler(CommandHandler("add_email", self.add_email_command))
        self.application.add_handler(CommandHandler("remove_email", self.remove_email_command))
        self.application.add_handler(CommandHandler("list_emails", self.list_emails_command))

        # ЭЛН Email команды
        self.application.add_handler(CommandHandler("add_eln_email", self.add_eln_email_command))
        self.application.add_handler(CommandHandler("remove_eln_email", self.remove_eln_email_command))
        self.application.add_handler(CommandHandler("list_eln_emails", self.list_eln_emails_command))

        # Пакетная отправка для ПСКП
        self.application.add_handler(CommandHandler("send_batch", self.send_batch_command))
        self.application.add_handler(CommandHandler("queue_status", self.queue_status_command))
        self.application.add_handler(CommandHandler("clear_queue", self.clear_queue_command))

        # Callback кнопки
        self.application.add_handler(CallbackQueryHandler(self.button_callback))

        # Фото
        self.application.add_handler(MessageHandler(filters.PHOTO, self.handle_photo))

        # Текстовые сообщения
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text))

    def _get_or_create_context(self, user_id: int) -> BotContext:
        """
        Получение или создание контекста пользователя

        Args:
            user_id: ID пользователя

        Returns:
            Контекст пользователя
        """
        if user_id not in self.user_contexts:
            self.user_contexts[user_id] = BotContext(
                clinic=ClinicMode.DINASTIYA,
                state=BotState.IDLE,
            )
        return self.user_contexts[user_id]

    def _create_docx_file(self, template_content: str, patient_data: PatientData, clinic: ClinicMode) -> str:
        """
        Создание .docx файла из текстового содержимого шаблона

        Args:
            template_content: Текстовое содержимое шаблона с markdown разметкой
            patient_data: Данные пациента
            clinic: Режим клиники

        Returns:
            Путь к созданному временному .docx файлу
        """
        # Получаем путь к базовому шаблону клиники
        templates_dir = Path(__file__).parent.parent / "data" / "templates"

        if clinic == ClinicMode.DINASTIYA:
            base_template_path = templates_dir / "dinastiya" / "Артемьева+.docx"
        elif clinic == ClinicMode.PSKP:
            base_template_path = templates_dir / "pskp" / "Митина Н.А.docx"
        else:
            raise ValueError(f"Неизвестная клиника: {clinic}")

        if not base_template_path.exists():
            raise FileNotFoundError(f"Базовый шаблон не найден: {base_template_path}")

        # Создаем временный файл
        temp_file = tempfile.NamedTemporaryFile(mode='w+b', suffix='.docx', delete=False)
        temp_filepath = temp_file.name
        temp_file.close()

        # Копируем базовый шаблон
        import shutil
        shutil.copy2(str(base_template_path), temp_filepath)

        # Открываем скопированный документ
        doc = Document(temp_filepath)

        # Используем метод archive_service для замены содержимого
        self.archive_service._replace_document_content(doc, template_content)

        # Сохраняем
        doc.save(temp_filepath)

        return temp_filepath

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка команды /start"""
        user_id = update.effective_user.id
        self.user_contexts[user_id] = BotContext(
            clinic=ClinicMode.DINASTIYA,
            state=BotState.IDLE,
        )

        keyboard = [
            [
                InlineKeyboardButton("🏥 Династия", callback_data="clinic_dinastiya"),
                InlineKeyboardButton("🏥 ПСКП", callback_data="clinic_pskp"),
            ],
            [
                InlineKeyboardButton("🏥 ГОДОК (Б24)", callback_data="clinic_godok"),
            ],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            "Добро пожаловать в медицинский бот! 🏥\n\n"
            "Я помогу создать шаблон медицинского осмотра с актуальными датами и ЭЛН.\n\n"
            "Выберите клинику, затем отправьте:\n"
            "• Фото документа (СНИЛС)\n"
            "• Или текстовые данные в формате:\n"
            "  ФИО: Иванов Иван Иванович\n"
            "  Дата рождения: 01.01.1990\n"
            "  Диагноз: Острый бронхит\n"
            "  Место работы: АО Т-БАНК (необязательно)\n"
            "  Должность: эксперт (необязательно)\n"
            "  ЭЛН: 27.12.2025 по 31.12.2025 (точные даты, необязательно)\n"
            "  ЭЛН: 5 дней (или количество дней, по умолчанию 3)\n"
            "  ЭЛН: отказ (если пациент отказался)\n"
            "  Дата осмотра: 25.12.2025 (необязательно)\n"
            "  Начало болезни: 24.12.2025 (необязательно)\n\n"
            "Основные команды:\n"
            "/cancel - отменить текущий процесс\n"
            "/stats - вернуться в главное меню\n"
            "/help - полная помощь по всем командам\n\n"
            "Email (все осмотры):\n"
            "/add_email адрес - добавить получателя\n"
            "/remove_email адрес - удалить получателя\n"
            "/list_emails - показать список\n\n"
            "Email (только с ЭЛН):\n"
            "/add_eln_email адрес - добавить ЭЛН-получателя\n"
            "/remove_eln_email адрес - удалить ЭЛН-получателя\n"
            "/list_eln_emails - показать список\n\n"
            "Пакетная отправка (только ПСКП):\n"
            "/send_batch - отправить все документы\n"
            "/send_batch 27.12.2025 - за конкретную дату\n"
            "/queue_status - статус очереди\n"
            "/clear_queue - очистить очередь",
            reply_markup=reply_markup,
        )

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка команды /help"""
        await update.message.reply_text(
            "Помощь по использованию бота:\n\n"
            "1. Выберите клинику кнопками\n"
            "2. Отправьте фото документа (СНИЛС) или данные текстом\n"
            "3. Проверьте созданный шаблон\n"
            "4. Подтвердите или отправьте комментарии для правок\n"
            "5. Шаблон будет сохранен в архиве\n\n"
            "Формат текстовых данных:\n"
            "ФИО: Иванов Иван Иванович\n"
            "Дата рождения: 01.01.1990\n"
            "Диагноз: Острый бронхит\n\n"
            "Необязательные поля:\n"
            "СНИЛС: 123-456-789 00\n"
            "Место работы: АО Т-БАНК\n"
            "Должность: эксперт\n"
            "ЭЛН: 27.12.2025 по 31.12.2025 (точные даты)\n"
            "ЭЛН: 5 дней (или просто: ЭЛН: 5)\n"
            "Дата осмотра: 25.12.2025\n"
            "Начало болезни: 24.12.2025\n\n"
            "Бот автоматически:\n"
            "• Обновит все даты в шаблоне на актуальные\n"
            "• Рассчитает период ЭЛН и дату явки к врачу\n"
            "• Сохранит оформление и структуру шаблона\n\n"
            "Основные команды:\n"
            "/cancel - отменить текущий процесс и вернуться к началу\n"
            "/stats - вернуться в главное меню\n"
            "/help - показать эту справку\n\n"
            "Email команды (все осмотры):\n"
            "/add_email адрес@example.com - добавить получателя\n"
            "/remove_email адрес@example.com - удалить получателя\n"
            "/list_emails - показать список получателей\n\n"
            "Email команды (только осмотры с ЭЛН):\n"
            "/add_eln_email адрес@example.com - добавить ЭЛН-получателя\n"
            "/remove_eln_email адрес@example.com - удалить ЭЛН-получателя\n"
            "/list_eln_emails - показать список ЭЛН-получателей\n\n"
            "Пакетная отправка (только для ПСКП):\n"
            "/queue_status - статус очереди документов\n"
            "/send_batch - отправить все документы одним письмом\n"
            "/send_batch 27.12.2025 - отправить документы за конкретную дату\n"
            "/clear_queue - очистить очередь документов"
        )

    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка команды /stats - возврат к начальному экрану"""
        user_id = update.effective_user.id
        # Сбрасываем контекст пользователя
        self.user_contexts[user_id] = BotContext(
            clinic=ClinicMode.DINASTIYA,
            state=BotState.IDLE,
        )

        # Создаем кнопки выбора клиники
        keyboard = [
            [
                InlineKeyboardButton("🏥 Династия", callback_data="clinic_dinastiya"),
                InlineKeyboardButton("🏥 ПСКП", callback_data="clinic_pskp"),
            ],
            [
                InlineKeyboardButton("🏥 ГОДОК (Б24)", callback_data="clinic_godok"),
            ],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Отправляем приветственное сообщение
        await update.message.reply_text(
            "Добро пожаловать в медицинский бот! 🏥\n\n"
            "Я помогу создать шаблон медицинского осмотра с актуальными датами и ЭЛН.\n\n"
            "Выберите клинику, затем отправьте:\n"
            "• Фото документа (СНИЛС)\n"
            "• Или текстовые данные в формате:\n"
            "  ФИО: Иванов Иван Иванович\n"
            "  Дата рождения: 01.01.1990\n"
            "  Диагноз: Острый бронхит\n"
            "  Место работы: АО Т-БАНК (необязательно)\n"
            "  Должность: эксперт (необязательно)\n"
            "  ЭЛН: 27.12.2025 по 31.12.2025 (точные даты, необязательно)\n"
            "  ЭЛН: 5 дней (или количество дней, по умолчанию 3)\n"
            "  ЭЛН: отказ (если пациент отказался)\n"
            "  Дата осмотра: 25.12.2025 (необязательно)\n"
            "  Начало болезни: 24.12.2025 (необязательно)\n\n"
            "Основные команды:\n"
            "/cancel - отменить текущий процесс\n"
            "/stats - вернуться в главное меню\n"
            "/help - полная помощь по всем командам\n\n"
            "Email (все осмотры):\n"
            "/add_email адрес - добавить получателя\n"
            "/remove_email адрес - удалить получателя\n"
            "/list_emails - показать список\n\n"
            "Email (только с ЭЛН):\n"
            "/add_eln_email адрес - добавить ЭЛН-получателя\n"
            "/remove_eln_email адрес - удалить ЭЛН-получателя\n"
            "/list_eln_emails - показать список\n\n"
            "Пакетная отправка (только ПСКП):\n"
            "/send_batch - отправить все документы\n"
            "/send_batch 27.12.2025 - за конкретную дату\n"
            "/queue_status - статус очереди\n"
            "/clear_queue - очистить очередь",
            reply_markup=reply_markup,
        )

    async def cancel_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка команды /cancel - отмена текущего процесса и возврат к началу"""
        user_id = update.effective_user.id

        # Получаем текущий контекст для проверки состояния
        current_context = self.user_contexts.get(user_id)

        # Удаляем временный файл документа, если он есть
        if current_context and current_context.saved_document_path:
            try:
                doc_path = Path(current_context.saved_document_path)
                if doc_path.exists():
                    doc_path.unlink()
            except Exception as e:
                print(f"Ошибка удаления файла при отмене: {e}")

        # Сбрасываем контекст пользователя
        self.user_contexts[user_id] = BotContext(
            clinic=ClinicMode.DINASTIYA,
            state=BotState.IDLE,
        )

        # Создаем кнопки выбора клиники
        keyboard = [
            [
                InlineKeyboardButton("🏥 Династия", callback_data="clinic_dinastiya"),
                InlineKeyboardButton("🏥 ПСКП", callback_data="clinic_pskp"),
            ],
            [
                InlineKeyboardButton("🏥 ГОДОК (Б24)", callback_data="clinic_godok"),
            ],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Сообщение об отмене
        cancel_message = "❌ Текущий процесс отменен.\n\n"

        # Если были какие-то данные в процессе, уведомляем об их очистке
        if current_context and (
            current_context.patient_data or
            current_context.current_template or
            current_context.examination_photos
        ):
            cancel_message += "Все несохраненные данные очищены.\n\n"

        await update.message.reply_text(
            cancel_message +
            "Выберите клинику для начала работы:\n\n"
            "🏥 Династия\n"
            "🏥 ПСКП\n"
            "🏥 ГОДОК (Битрикс24)",
            reply_markup=reply_markup,
        )

    async def add_email_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка команды /add_email - добавление email получателя"""
        if not self.email_service:
            await update.message.reply_text(
                "❌ Email сервис не настроен.\n"
                "Добавьте SMTP_USER и SMTP_PASSWORD в файл .env"
            )
            return

        # Проверяем аргументы команды
        if not context.args or len(context.args) == 0:
            await update.message.reply_text(
                "📧 Использование: /add_email адрес@example.com\n\n"
                "Пример:\n"
                "/add_email doctor@clinic.ru"
            )
            return

        email = context.args[0].strip()

        # Простая валидация email
        if '@' not in email or '.' not in email:
            await update.message.reply_text("❌ Некорректный email адрес")
            return

        # Получаем контекст пользователя
        user_id = update.effective_user.id
        user_context = self.user_contexts[user_id]

        # Добавляем email для текущей клиники
        clinic_name = "Династия" if user_context.clinic == ClinicMode.DINASTIYA else "ПСКП"
        if self.email_service.add_recipient(email, clinic=user_context.clinic.value):
            await update.message.reply_text(
                f"✅ Email {email} добавлен в список получателей для клиники \"{clinic_name}\""
            )
        else:
            await update.message.reply_text(f"⚠️ Email {email} уже есть в списке для клиники \"{clinic_name}\"")

    async def remove_email_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка команды /remove_email - удаление email получателя"""
        if not self.email_service:
            await update.message.reply_text(
                "❌ Email сервис не настроен.\n"
                "Добавьте SMTP_USER и SMTP_PASSWORD в файл .env"
            )
            return

        # Проверяем аргументы команды
        if not context.args or len(context.args) == 0:
            await update.message.reply_text(
                "📧 Использование: /remove_email адрес@example.com\n\n"
                "Пример:\n"
                "/remove_email doctor@clinic.ru"
            )
            return

        email = context.args[0].strip()

        # Получаем контекст пользователя
        user_id = update.effective_user.id
        user_context = self.user_contexts[user_id]

        # Удаляем email для текущей клиники
        clinic_name = "Династия" if user_context.clinic == ClinicMode.DINASTIYA else "ПСКП"
        if self.email_service.remove_recipient(email, clinic=user_context.clinic.value):
            await update.message.reply_text(
                f"✅ Email {email} удалён из списка получателей для клиники \"{clinic_name}\""
            )
        else:
            await update.message.reply_text(
                f"⚠️ Email {email} не найден в списке для клиники \"{clinic_name}\""
            )

    async def list_emails_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка команды /list_emails - список email получателей"""
        if not self.email_service:
            await update.message.reply_text(
                "❌ Email сервис не настроен.\n"
                "Добавьте SMTP_USER и SMTP_PASSWORD в файл .env"
            )
            return

        # Получаем контекст пользователя
        user_id = update.effective_user.id
        user_context = self.user_contexts[user_id]

        # Получаем список для текущей клиники
        clinic_name = "Династия" if user_context.clinic == ClinicMode.DINASTIYA else "ПСКП"
        recipients = self.email_service.get_recipients(clinic=user_context.clinic.value)

        if not recipients:
            await update.message.reply_text(
                f"📧 Список получателей для клиники \"{clinic_name}\" пуст\n\n"
                "Добавьте email командой:\n"
                "/add_email адрес@example.com"
            )
        else:
            emails_list = "\n".join([f"  • {email}" for email in recipients])
            await update.message.reply_text(
                f"📧 Список получателей для клиники \"{clinic_name}\" ({len(recipients)}):\n\n"
                f"{emails_list}\n\n"
                f"Управление:\n"
                f"/add_email адрес@example.com - добавить\n"
                f"/remove_email адрес@example.com - удалить"
            )

    async def add_eln_email_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка команды /add_eln_email - добавление email получателя для осмотров с ЭЛН"""
        if not self.email_service:
            await update.message.reply_text(
                "❌ Email сервис не настроен.\n"
                "Добавьте SMTP_USER и SMTP_PASSWORD в файл .env"
            )
            return

        # Проверяем аргументы команды
        if not context.args or len(context.args) == 0:
            await update.message.reply_text(
                "📧 Использование: /add_eln_email адрес@example.com\n\n"
                "Этот email будет получать ТОЛЬКО осмотры с ЭЛН (исключая отказы от ЭЛН)\n\n"
                "Пример:\n"
                "/add_eln_email accounting@clinic.ru"
            )
            return

        email = context.args[0].strip()

        # Простая валидация email
        if '@' not in email or '.' not in email:
            await update.message.reply_text("❌ Некорректный email адрес")
            return

        # Получаем контекст пользователя
        user_id = update.effective_user.id
        user_context = self.user_contexts[user_id]

        # Добавляем email для текущей клиники (тип: только ЭЛН)
        clinic_name = "Династия" if user_context.clinic == ClinicMode.DINASTIYA else "ПСКП"
        if self.email_service.add_recipient(email, clinic=user_context.clinic.value, recipient_type="eln_only"):
            await update.message.reply_text(
                f"✅ Email {email} добавлен в список получателей осмотров с ЭЛН для клиники \"{clinic_name}\"\n\n"
                f"⚠️ На этот адрес будут отправляться ТОЛЬКО осмотры с ЭЛН (исключая отказы)"
            )
        else:
            await update.message.reply_text(f"⚠️ Email {email} уже есть в списке ЭЛН-получателей для клиники \"{clinic_name}\"")

    async def remove_eln_email_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка команды /remove_eln_email - удаление email получателя для осмотров с ЭЛН"""
        if not self.email_service:
            await update.message.reply_text(
                "❌ Email сервис не настроен.\n"
                "Добавьте SMTP_USER и SMTP_PASSWORD в файл .env"
            )
            return

        # Проверяем аргументы команды
        if not context.args or len(context.args) == 0:
            await update.message.reply_text(
                "📧 Использование: /remove_eln_email адрес@example.com\n\n"
                "Пример:\n"
                "/remove_eln_email accounting@clinic.ru"
            )
            return

        email = context.args[0].strip()

        # Получаем контекст пользователя
        user_id = update.effective_user.id
        user_context = self.user_contexts[user_id]

        # Удаляем email для текущей клиники (тип: только ЭЛН)
        clinic_name = "Династия" if user_context.clinic == ClinicMode.DINASTIYA else "ПСКП"
        if self.email_service.remove_recipient(email, clinic=user_context.clinic.value, recipient_type="eln_only"):
            await update.message.reply_text(
                f"✅ Email {email} удалён из списка ЭЛН-получателей для клиники \"{clinic_name}\""
            )
        else:
            await update.message.reply_text(
                f"⚠️ Email {email} не найден в списке ЭЛН-получателей для клиники \"{clinic_name}\""
            )

    async def list_eln_emails_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка команды /list_eln_emails - список email получателей для осмотров с ЭЛН"""
        if not self.email_service:
            await update.message.reply_text(
                "❌ Email сервис не настроен.\n"
                "Добавьте SMTP_USER и SMTP_PASSWORD в файл .env"
            )
            return

        # Получаем контекст пользователя
        user_id = update.effective_user.id
        user_context = self.user_contexts[user_id]

        # Получаем список для текущей клиники (тип: только ЭЛН)
        clinic_name = "Династия" if user_context.clinic == ClinicMode.DINASTIYA else "ПСКП"
        recipients = self.email_service.get_recipients(clinic=user_context.clinic.value, recipient_type="eln_only")

        if not recipients:
            await update.message.reply_text(
                f"📧 Список ЭЛН-получателей для клиники \"{clinic_name}\" пуст\n\n"
                "ЭЛН-получатели - это email адреса, которые получают ТОЛЬКО осмотры с ЭЛН (исключая отказы)\n\n"
                "Добавьте email командой:\n"
                "/add_eln_email адрес@example.com"
            )
        else:
            emails_list = "\n".join([f"  • {email}" for email in recipients])
            await update.message.reply_text(
                f"📧 Список ЭЛН-получателей для клиники \"{clinic_name}\" ({len(recipients)}):\n\n"
                f"{emails_list}\n\n"
                f"⚠️ Эти адреса получают ТОЛЬКО осмотры с ЭЛН (исключая отказы)\n\n"
                f"Управление:\n"
                f"/add_eln_email адрес@example.com - добавить\n"
                f"/remove_eln_email адрес@example.com - удалить"
            )

    async def send_batch_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка команды /send_batch [дата] - пакетная отправка накопленных документов (только для ПСКП)"""
        if not self.email_service:
            await update.message.reply_text(
                "❌ Email сервис не настроен.\n"
                "Добавьте SMTP_USER и SMTP_PASSWORD в файл .env"
            )
            return

        user_id = update.effective_user.id
        user_context = self._get_or_create_context(user_id)

        # Проверяем, что это клиника ПСКП
        if user_context.clinic != ClinicMode.PSKP:
            await update.message.reply_text(
                "⚠️ Пакетная отправка доступна только для клиники ПСКП\n\n"
                "Для клиники Династия документы отправляются сразу после подтверждения."
            )
            return

        # Парсим дату из аргументов команды (опционально)
        filter_date = None
        if context.args and len(context.args) > 0:
            date_str = context.args[0].strip()
            # Проверяем формат даты ДД.ММ.ГГГГ
            if re.match(r"^\d{2}\.\d{2}\.\d{4}$", date_str):
                filter_date = date_str
            else:
                await update.message.reply_text(
                    "❌ Неверный формат даты\n\n"
                    "Используйте формат: /send_batch ДД.ММ.ГГГГ\n"
                    "Пример: /send_batch 27.12.2025"
                )
                return

        try:
            # Получаем документы из постоянной очереди
            docs_to_send = self.document_queue_service.get_queue(user_id, user_context.clinic.value, filter_date)

            if not docs_to_send:
                if filter_date:
                    await update.message.reply_text(
                        f"📭 Нет документов за {filter_date}\n\n"
                        "Используйте /queue_status чтобы увидеть все даты."
                    )
                else:
                    await update.message.reply_text(
                        "📭 Очередь документов пуста\n\n"
                        "Создайте несколько осмотров, они будут автоматически добавлены в очередь."
                    )
                return

            await update.message.reply_text(
                f"📧 Отправляю {len(docs_to_send)} документ(ов) одним письмом..."
            )

            # Разделяем документы на две группы: с ЭЛН и без ЭЛН
            docs_with_eln = []
            docs_without_eln = []
            for doc in docs_to_send:
                doc_data = {
                    "file_path": doc["file_path"],
                    "patient_name": doc["patient_name"],
                    "examination_date": doc["examination_date"]
                }
                if doc["has_eln"]:
                    docs_with_eln.append(doc_data)
                else:
                    docs_without_eln.append(doc_data)

            # Определяем дату для темы письма
            if filter_date:
                batch_date = filter_date
            else:
                # Используем дату первого документа или текущую
                batch_date = docs_to_send[0]["examination_date"] if docs_to_send else datetime.now().strftime("%d.%m.%Y")

            # Отправляем документы с правильной фильтрацией получателей
            success, error = await self.email_service.send_batch_documents(
                documents_with_eln=docs_with_eln,
                documents_without_eln=docs_without_eln,
                clinic=user_context.clinic.value,
                doctor_name="Гаджимурадлы Д.Д",
                batch_date=batch_date
            )

            if success:
                # Получаем информацию о получателях для отчета
                all_recipients = self.email_service.get_recipients(clinic=user_context.clinic.value, recipient_type="all")
                eln_recipients = self.email_service.get_recipients(clinic=user_context.clinic.value, recipient_type="eln_only")

                total_docs = len(docs_with_eln) + len(docs_without_eln)
                msg = f"✅ Пакет из {total_docs} документ(ов) отправлен!\n\n"

                if docs_with_eln and docs_without_eln:
                    msg += f"Все документы ({total_docs} шт.) → {', '.join(all_recipients)}\n"
                    if eln_recipients:
                        msg += f"Документы с ЭЛН ({len(docs_with_eln)} шт.) → {', '.join(eln_recipients)}"
                elif docs_with_eln:
                    total_recipients = list(set(all_recipients + eln_recipients))
                    msg += f"Получатели: {', '.join(total_recipients)}"
                else:
                    msg += f"Получатели: {', '.join(all_recipients)}"

                await update.message.reply_text(msg)

                # Удаляем отправленные документы из очереди
                self.document_queue_service.clear_queue(user_id, user_context.clinic.value, filter_date)
            else:
                await update.message.reply_text(f"⚠️ Ошибка отправки email:\n{error}")

        except Exception as e:
            print(f"Ошибка пакетной отправки: {e}")
            import traceback
            traceback.print_exc()
            await update.message.reply_text(f"❌ Ошибка при отправке: {str(e)}")

    async def queue_status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка команды /queue_status - показать статус очереди документов"""
        user_id = update.effective_user.id
        user_context = self._get_or_create_context(user_id)

        # Проверяем, что это клиника ПСКП
        if user_context.clinic != ClinicMode.PSKP:
            await update.message.reply_text(
                "⚠️ Пакетная отправка доступна только для клиники ПСКП"
            )
            return

        # Получаем статус очереди из постоянного хранилища
        queue_status = self.document_queue_service.get_queue_status(user_id, user_context.clinic.value)

        if queue_status['total'] == 0:
            await update.message.reply_text(
                "📭 Очередь документов пуста\n\n"
                "Создайте несколько осмотров, они будут автоматически добавлены в очередь."
            )
            return

        # Формируем детальную информацию по датам
        date_info = []
        for date, stats in sorted(queue_status['by_date'].items()):
            date_info.append(
                f"📅 {date}: {stats['total']} док. (✅ {stats['with_eln']} с ЭЛН, ❌ {stats['without_eln']} без ЭЛН)"
            )

        dates_text = "\n".join(date_info) if date_info else ""

        await update.message.reply_text(
            f"📋 Очередь документов ПСКП\n\n"
            f"Всего: {queue_status['total']} документ(ов)\n"
            f"• С ЭЛН: {queue_status['with_eln']}\n"
            f"• Без ЭЛН: {queue_status['without_eln']}\n\n"
            f"{dates_text}\n\n"
            f"Управление:\n"
            f"/send_batch - отправить все одним письмом\n"
            f"/send_batch 28.12.2025 - отправить за конкретную дату\n"
            f"/clear_queue - очистить очередь"
        )

    async def clear_queue_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка команды /clear_queue - очистить очередь документов"""
        user_id = update.effective_user.id
        user_context = self._get_or_create_context(user_id)

        # Проверяем, что это клиника ПСКП
        if user_context.clinic != ClinicMode.PSKP:
            await update.message.reply_text(
                "⚠️ Пакетная отправка доступна только для клиники ПСКП"
            )
            return

        # Очищаем очередь через сервис
        deleted_count = self.document_queue_service.clear_queue(user_id, user_context.clinic.value)

        if deleted_count == 0:
            await update.message.reply_text("📭 Очередь уже пуста")
            return

        await update.message.reply_text(
            f"🗑️ Очередь очищена\n"
            f"Удалено документов: {deleted_count}"
        )

    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка нажатий на inline кнопки"""
        query = update.callback_query

        # Пытаемся ответить на callback query (может быть устаревшим)
        try:
            await query.answer()
        except BadRequest as e:
            # Игнорируем ошибку если query слишком старый
            if "query is too old" not in str(e).lower():
                raise

        user_id = update.effective_user.id
        user_context = self._get_or_create_context(user_id)
        data = query.data

        if data == "clinic_dinastiya":
            user_context.clinic = ClinicMode.DINASTIYA
            await query.message.reply_text("✅ Выбрана клиника \"Династия\"")

        elif data == "clinic_pskp":
            user_context.clinic = ClinicMode.PSKP
            await query.message.reply_text("✅ Выбрана клиника \"ПСКП\"")

        elif data == "clinic_godok":
            if not self.bitrix_service:
                await query.message.reply_text(
                    "❌ Режим ГОДОК недоступен: не настроен Битрикс24.\n"
                    "Добавьте BITRIX_WEBHOOK_URL в файл .env и перезапустите бота."
                )
            else:
                user_context.clinic = ClinicMode.GODOK
                await query.message.reply_text(
                    "✅ Выбран режим \"ГОДОК\" (Битрикс24)\n\n"
                    "Отправьте данные пациента текстом:\n"
                    "ФИО: Иванов Иван Иванович\n"
                    "Дата рождения: 01.01.1990\n"
                    "Диагноз: Острый бронхит (J20.9)\n"
                    "Место работы: АО Т-БАНК (необязательно)\n"
                    "Должность: эксперт (необязательно)\n"
                    "ЭЛН: 27.12.2025 по 31.12.2025 (необязательно)\n"
                    "Дата осмотра: 25.12.2025 (необязательно, по умолчанию — сегодня)\n\n"
                    "Бот найдёт сделку в воронке «ПНД ПОМОЩЬ НА ДОМУ», "
                    "сгенерирует все поля карточки и покажет предпросмотр перед записью."
                )

        elif data.startswith("godok_pick_deal_"):
            await self._handle_godok_deal_choice(query.message, user_context, data)

        elif data == "godok_confirm_write":
            await self._handle_godok_confirm(query.message, user_context)

        elif data == "godok_request_corrections":
            user_context.state = BotState.GODOK_AWAITING_CORRECTIONS
            await query.message.reply_text(
                "✏️ Отправьте текстом, что нужно изменить в карточке.\n\n"
                "Например:\n"
                "• Поставить температуру 38.5\n"
                "• Добавить в жалобы кашель с мокротой\n"
                "• Изменить диагноз на J06.9\n"
                "• В назначениях: добавить Нурофен 400мг 3 р/д 5 дней"
            )

        elif data == "godok_cancel":
            user_context.state = BotState.IDLE
            user_context.godok_deal_id = None
            user_context.godok_deal_title = None
            user_context.godok_fields = None
            user_context.godok_candidates = None
            user_context.godok_message_time = None
            await query.message.reply_text("❌ Запись в Битрикс24 отменена.")

        elif data == "confirm_template":
            success = await self._save_template(query.message, user_context)
            if success:
                await query.message.reply_text("✓ Шаблон сохранен!")

        elif data == "request_corrections":
            user_context.state = BotState.AWAITING_CORRECTIONS
            await query.message.reply_text(
                "✏️ Отправьте комментарии для исправления шаблона.\n\n"
                "Например:\n"
                "\"Изменить диагноз на...\n"
                "Добавить в анамнез...\n"
                "Убрать из назначений...\""
            )

        elif data == "add_photo":
            # Пользователь хочет добавить фото осмотра
            await query.message.reply_text(
                "📷 Отправьте фото осмотра (можно несколько фотографий).\n\n"
                "После отправки всех фото нажмите кнопку ниже:",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Готово, отправить email", callback_data="send_with_photos")
                ]])
            )

        elif data == "send_without_photo":
            # Отправка email без фото
            await self._send_email_without_photos(query.message, user_context)

        elif data == "send_with_photos":
            # Отправка email с фото
            await self._send_email_with_photos(query.message, user_context)

    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка фото документа"""
        user_id = update.effective_user.id
        user_context = self._get_or_create_context(user_id)

        # Если ожидаем фото осмотра для email
        if user_context.state == BotState.AWAITING_PHOTO:
            try:
                # Получаем фото наилучшего качества
                photo = update.message.photo[-1]
                file = await context.bot.get_file(photo.file_id)

                # Скачиваем фото
                photo_bytes = await file.download_as_bytearray()

                # Сохраняем фото в список
                if user_context.examination_photos is None:
                    user_context.examination_photos = []
                user_context.examination_photos.append(photo_bytes)

                await update.message.reply_text(
                    f"✅ Фото {len(user_context.examination_photos)} добавлено!\n\n"
                    "Можете отправить ещё фото или нажмите кнопку \"Готово\"."
                )
                return
            except Exception as e:
                print(f"Ошибка сохранения фото осмотра: {e}")
                await update.message.reply_text("❌ Ошибка сохранения фото. Попробуйте ещё раз.")
                return

        # Игнорируем фото если ожидаем подтверждения или правок
        if user_context.state in [BotState.AWAITING_CONFIRMATION, BotState.AWAITING_CORRECTIONS]:
            return

        try:
            await update.message.reply_text("🔍 Обрабатываю фото документа...")

            # Получаем фото наилучшего качества
            photo = update.message.photo[-1]
            file = await context.bot.get_file(photo.file_id)

            # Скачиваем и конвертируем в base64
            photo_bytes = await file.download_as_bytearray()
            image_base64 = base64.b64encode(photo_bytes).decode("utf-8")

            # Сначала пробуем распознать как медицинский документ (выписка, анализы)
            try:
                medical_data = await self.claude_service.extract_medical_document_data(image_base64, "image/jpeg")

                # Проверяем, является ли это медицинским документом с диагнозом
                if medical_data.get("diagnosis") or medical_data.get("document_type"):
                    # Это медицинский документ (выписка, анализы, осмотр)
                    if user_context.patient_data is None:
                        user_context.patient_data = {}

                    # Автоматически заполняем все распознанные поля
                    if medical_data.get("full_name"):
                        user_context.patient_data["full_name"] = medical_data["full_name"]
                    if medical_data.get("birth_date"):
                        user_context.patient_data["birth_date"] = medical_data["birth_date"]
                    if medical_data.get("snils"):
                        user_context.patient_data["snils"] = medical_data["snils"]
                    if medical_data.get("examination_date"):
                        user_context.patient_data["examination_date"] = medical_data["examination_date"]
                    if medical_data.get("diagnosis"):
                        user_context.patient_data["diagnosis"] = medical_data["diagnosis"]
                    if medical_data.get("workplace"):
                        user_context.patient_data["workplace"] = medical_data["workplace"]
                    if medical_data.get("position"):
                        user_context.patient_data["position"] = medical_data["position"]

                    # Формируем сообщение с распознанными данными
                    doc_type = medical_data.get("document_type", "медицинский документ")
                    message = f"✅ Распознан {doc_type}!\n\n"
                    message += "📋 Извлеченные данные:\n\n"

                    if medical_data.get("full_name"):
                        message += f"👤 ФИО: {medical_data['full_name']}\n"
                    if medical_data.get("birth_date"):
                        message += f"📅 Дата рождения: {medical_data['birth_date']}\n"
                    if medical_data.get("snils"):
                        message += f"🔢 СНИЛС: {medical_data['snils']}\n"
                    if medical_data.get("workplace"):
                        message += f"🏢 Место работы: {medical_data['workplace']}\n"
                    if medical_data.get("position"):
                        message += f"💼 Должность: {medical_data['position']}\n"
                    if medical_data.get("examination_date"):
                        message += f"📆 Дата осмотра: {medical_data['examination_date']}\n"
                    if medical_data.get("diagnosis"):
                        message += f"🏥 Диагноз: {medical_data['diagnosis']}\n"

                    # Добавляем дополнительную информацию если есть
                    if medical_data.get("complaints"):
                        message += f"\n💬 Жалобы:\n{medical_data['complaints'][:200]}...\n" if len(medical_data['complaints']) > 200 else f"\n💬 Жалобы: {medical_data['complaints']}\n"
                    if medical_data.get("anamnesis"):
                        message += f"\n📖 Анамнез:\n{medical_data['anamnesis'][:200]}...\n" if len(medical_data['anamnesis']) > 200 else f"\n📖 Анамнез: {medical_data['anamnesis']}\n"
                    if medical_data.get("objective"):
                        message += f"\n🔬 Объективно:\n{medical_data['objective'][:200]}...\n" if len(medical_data['objective']) > 200 else f"\n🔬 Объективно: {medical_data['objective']}\n"
                    if medical_data.get("examinations"):
                        message += f"\n🧪 Обследования:\n{medical_data['examinations'][:200]}...\n" if len(medical_data['examinations']) > 200 else f"\n🧪 Обследования: {medical_data['examinations']}\n"
                    if medical_data.get("recommendations"):
                        message += f"\n💊 Рекомендации:\n{medical_data['recommendations'][:200]}...\n" if len(medical_data['recommendations']) > 200 else f"\n💊 Рекомендации: {medical_data['recommendations']}\n"

                    message += "\n✏️ Добавьте недостающие данные текстом (ЭЛН, дополнительная информация) или отправьте команду для создания осмотра."

                    await update.message.reply_text(message)
                    return

            except Exception as med_error:
                print(f"Не удалось распознать как медицинский документ: {med_error}")

            # Если это не медицинский документ, пробуем распознать как СНИЛС
            extracted_data = await self.claude_service.extract_data_from_image(image_base64, "image/jpeg")

            # Если ничего не распознано — сообщаем пользователю
            if not any(extracted_data.get(f) for f in ("full_name", "birth_date", "snils")):
                await update.message.reply_text(
                    "⚠️ Не удалось распознать данные с фото.\n"
                    "Убедитесь, что изображение чёткое и содержит СНИЛС или медицинский документ.\n"
                    "Попробуйте отправить фото ещё раз или введите данные вручную."
                )
                return

            # Сохраняем в контекст
            if user_context.patient_data is None:
                user_context.patient_data = {}

            user_context.patient_data.update(extracted_data)

            message = "✅ Данные распознаны (СНИЛС):\n\n"
            if extracted_data.get("full_name"):
                message += f"ФИО: {extracted_data['full_name']}\n"
            if extracted_data.get("birth_date"):
                message += f"Дата рождения: {extracted_data['birth_date']}\n"
            if extracted_data.get("snils"):
                message += f"СНИЛС: {extracted_data['snils']}\n"

            message += "\nТеперь отправьте диагноз или все данные текстом для создания шаблона."

            await update.message.reply_text(message)

        except Exception as e:
            print(f"Ошибка обработки фото: {e}")
            await update.message.reply_text("❌ Ошибка обработки фото. Попробуйте еще раз.")

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка текстовых сообщений"""
        user_id = update.effective_user.id
        user_context = self._get_or_create_context(user_id)
        text = update.message.text

        try:
            # ГОДОК: правки к сгенерированной карточке
            if user_context.state == BotState.GODOK_AWAITING_CORRECTIONS:
                await self._handle_godok_corrections(update.message, user_context, text)
                return

            # ГОДОК: ожидаем кнопку подтверждения/правок/выбор сделки
            if user_context.state in (
                BotState.GODOK_AWAITING_CONFIRMATION,
                BotState.GODOK_AWAITING_DEAL_CHOICE,
            ):
                await update.message.reply_text(
                    "Пожалуйста, используйте кнопки для выбора действия."
                )
                return

            # Если ожидаем комментарии для правок
            if user_context.state == BotState.AWAITING_CORRECTIONS:
                await self._handle_corrections(update.message, user_context, text)
                return

            # Если ожидаем подтверждения
            if user_context.state == BotState.AWAITING_CONFIRMATION:
                await update.message.reply_text(
                    "Пожалуйста, используйте кнопки для подтверждения или запроса правок."
                )
                return

            # Обычная обработка данных пациента
            await update.message.reply_text("⚙️ Обрабатываю данные...")

            # Парсим текстовые данные
            parsed_data = self._parse_text_data(text)

            # Объединяем с данными из контекста
            if user_context.patient_data is None:
                user_context.patient_data = {}

            # Если даты студенческой справки некорректны — предупреждаем и останавливаемся
            if parsed_data.pop("_cert_date_error", False):
                await update.message.reply_text(
                    "⚠️ Некорректные даты студенческой справки.\n"
                    "Укажите в формате: Студенческая справка: 129/2025 с 18.12.2025 по 22.12.2025\n"
                    "(дата начала должна быть раньше даты окончания)"
                )
                return

            # Если дата рождения невалидна — предупреждаем пользователя и останавливаемся
            bad_bd = parsed_data.pop("_birth_date_error", None)
            if bad_bd:
                await update.message.reply_text(
                    f"⚠️ Неверный формат даты рождения: «{bad_bd}»\n"
                    "Укажите в формате ДД.ММ.ГГГГ, например: 15.03.1985"
                )
                return

            # Если количество дней ЭЛН вне допустимого диапазона — предупреждаем и останавливаемся
            bad_days = parsed_data.pop("_eln_days_error", None)
            if bad_days is not None:
                await update.message.reply_text(
                    f"⚠️ Недопустимое количество дней ЭЛН: {bad_days}.\n"
                    "Укажите от 1 до 60 дней или задайте точные даты:\n"
                    "ЭЛН: с 25.12.2025 по 30.12.2025"
                )
                return

            # Если даты ЭЛН не удалось распознать — предупреждаем пользователя и останавливаемся
            if parsed_data.pop("_eln_parse_error", False):
                await update.message.reply_text(
                    "⚠️ Не удалось распознать даты ЭЛН. Укажите в формате:\n"
                    "ЭЛН: с 25.12.2025 по 30.12.2025\n"
                    "или\n"
                    "ЭЛН: 5 дней"
                )
                return

            # Обновляем контекст новыми данными (для накопления данных между сообщениями)
            user_context.patient_data.update({k: v for k, v in parsed_data.items() if v is not None})

            full_name = parsed_data.get("full_name") or user_context.patient_data.get("full_name", "")
            birth_date = parsed_data.get("birth_date") or user_context.patient_data.get("birth_date", "")
            diagnosis = parsed_data.get("diagnosis") or user_context.patient_data.get("diagnosis", "")
            snils = parsed_data.get("snils") or user_context.patient_data.get("snils")

            # Новые поля для дат
            examination_date = parsed_data.get("examination_date") or user_context.patient_data.get("examination_date")
            illness_start_date = parsed_data.get("illness_start_date") or user_context.patient_data.get("illness_start_date")
            sick_leave_days = parsed_data.get("sick_leave_days") or user_context.patient_data.get("sick_leave_days")
            eln_refused = parsed_data.get("eln_refused", user_context.patient_data.get("eln_refused", False))

            # Новые поля для места работы и должности
            workplace = parsed_data.get("workplace") or user_context.patient_data.get("workplace")
            position = parsed_data.get("position") or user_context.patient_data.get("position")
            eln_start_date = parsed_data.get("eln_start_date") or user_context.patient_data.get("eln_start_date")
            eln_end_date = parsed_data.get("eln_end_date") or user_context.patient_data.get("eln_end_date")

            # Нормализуем строковые поля — убираем лишние пробелы
            full_name = full_name.strip()
            birth_date = birth_date.strip()
            diagnosis = diagnosis.strip()

            # Проверка обязательных полей
            if not full_name or not birth_date or not diagnosis:
                missing = []
                if not full_name:
                    missing.append("ФИО")
                if not birth_date:
                    missing.append("дата рождения")
                if not diagnosis:
                    missing.append("диагноз")
                await update.message.reply_text(
                    f"❌ Не хватает данных: {', '.join(missing)}.\n"
                    "Укажите недостающие поля и отправьте повторно."
                )
                return

            is_student = parsed_data.get("is_student") or user_context.patient_data.get("is_student", False)
            student_certificate = parsed_data.get("student_certificate") or user_context.patient_data.get("student_certificate")
            student_cert_end_date = parsed_data.get("student_cert_end_date") or user_context.patient_data.get("student_cert_end_date")

            patient_data = PatientData(
                full_name=full_name,
                birth_date=birth_date,
                diagnosis=diagnosis,
                snils=snils,
                examination_date=examination_date,
                illness_start_date=illness_start_date,
                sick_leave_days=sick_leave_days,
                eln_refused=eln_refused,
                workplace=workplace,
                position=position,
                eln_start_date=eln_start_date,
                eln_end_date=eln_end_date,
                is_student=bool(is_student),
                student_certificate=student_certificate,
                student_cert_end_date=student_cert_end_date,
            )

            # ГОДОК: вместо генерации .docx — поиск сделки в Б24 и заполнение карточки
            if user_context.clinic == ClinicMode.GODOK:
                await self._start_godok_flow(update.message, user_context, patient_data)
                return

            # Генерируем шаблон
            await self._generate_and_show_template(update.message, user_context, patient_data)

        except Exception as e:
            print(f"Ошибка обработки текста: {e}")
            await update.message.reply_text("❌ Ошибка обработки сообщения. Попробуйте еще раз.")

    async def _generate_and_show_template(self, message, user_context: BotContext, patient_data: PatientData):
        """Генерация и показ шаблона с кнопками подтверждения"""
        temp_filepath = None
        try:
            # ЛОКАЛЬНЫЙ поиск подходящего шаблона в архиве БЕЗ использования AI
            # Если найден - используем его как образец
            # Если не найден - используем базовый шаблон
            matched_template = await self.archive_service.find_template_by_diagnosis(
                patient_data.diagnosis, user_context.clinic
            )

            # Генерируем шаблон на основе найденного или базового шаблона
            await message.reply_text("✍️ Создаю шаблон осмотра...")
            template_content = await self.claude_service.generate_examination_template(
                patient_data, user_context.clinic, archive_template=matched_template
            )

            # Сохраняем в контекст
            user_context.current_template = CurrentTemplate(content=template_content, patient_data=patient_data)
            user_context.state = BotState.AWAITING_CONFIRMATION

            # Создаем кнопки
            keyboard = [
                [
                    InlineKeyboardButton("✅ Всё ОК, сохранить", callback_data="confirm_template"),
                    InlineKeyboardButton("✏️ Нужны правки", callback_data="request_corrections"),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            # Создаем .docx файл из шаблона
            await message.reply_text("📄 Создаю документ...")
            temp_filepath = self._create_docx_file(template_content, patient_data, user_context.clinic)

            # Формируем имя файла для отправки (дата осмотра, а не дата создания файла)
            safe_name = "".join(c if c.isalnum() or c == ' ' else '_' for c in patient_data.full_name)
            exam_date_str = patient_data.examination_date or datetime.now().strftime('%d.%m.%Y')
            filename = f"{safe_name}_осмотр_{exam_date_str}.docx"

            # Отправляем файл
            with open(temp_filepath, 'rb') as doc_file:
                await message.reply_document(
                    document=doc_file,
                    filename=filename,
                    caption=f"✅ Шаблон осмотра создан для клиники \"{user_context.clinic.value}\"!\n\nПроверьте документ и выберите действие:",
                    reply_markup=reply_markup,
                )

        except Exception as e:
            print(f"Ошибка генерации шаблона: {e}")
            await message.reply_text("❌ Ошибка создания шаблона. Попробуйте еще раз.")
            user_context.state = BotState.IDLE
        finally:
            # Удаляем временный файл
            if temp_filepath and Path(temp_filepath).exists():
                try:
                    Path(temp_filepath).unlink()
                except Exception as e:
                    print(f"Ошибка удаления временного файла: {e}")

    async def _handle_corrections(self, message, user_context: BotContext, corrections: str):
        """Обработка комментариев для правок"""
        if not user_context.current_template:
            await message.reply_text("❌ Нет шаблона для исправления")
            user_context.state = BotState.IDLE
            return

        temp_filepath = None
        try:
            await message.reply_text("🔄 Вношу исправления...")

            # Увеличиваем счетчик правок
            user_context.corrections_count += 1

            # Исправляем шаблон
            corrected_template = await self.claude_service.correct_template(
                user_context.current_template.content,
                user_context.current_template.patient_data,
                corrections,
                clinic=user_context.clinic,
            )

            # Предупреждаем если AI вернул документ без изменений
            original = user_context.current_template.content
            if corrected_template.strip() == original.strip():
                await message.reply_text(
                    "⚠️ Документ не изменился — ИИ не применил правки.\n"
                    "Попробуйте сформулировать точнее, например:\n"
                    "«В раздел Сопутствующие заболевания добавить: ревматоидный артрит»"
                )
                user_context.state = BotState.AWAITING_CORRECTIONS
                user_context.corrections_count = max(0, user_context.corrections_count - 1)
                return

            # Создаем .docx файл из исправленного шаблона
            await message.reply_text("📄 Создаю исправленный документ...")
            temp_filepath = self._create_docx_file(
                corrected_template,
                user_context.current_template.patient_data,
                user_context.clinic
            )

            # Обновляем контент и переводим состояние только после успешного создания файла
            user_context.current_template.content = corrected_template
            user_context.state = BotState.AWAITING_CONFIRMATION

            # Создаем кнопки
            keyboard = [
                [
                    InlineKeyboardButton("✅ Всё ОК, сохранить", callback_data="confirm_template"),
                    InlineKeyboardButton("✏️ Еще правки", callback_data="request_corrections"),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            # Формируем имя файла для отправки (дата осмотра, а не дата создания файла)
            safe_name = "".join(c if c.isalnum() or c == ' ' else '_' for c in user_context.current_template.patient_data.full_name)
            exam_date_str = user_context.current_template.patient_data.examination_date or datetime.now().strftime('%d.%m.%Y')
            filename = f"{safe_name}_осмотр_исправлен_{exam_date_str}.docx"

            # Отправляем файл
            with open(temp_filepath, 'rb') as doc_file:
                await message.reply_document(
                    document=doc_file,
                    filename=filename,
                    caption=f"✅ Шаблон исправлен (правка #{user_context.corrections_count})!\n\nПроверьте исправленный документ:",
                    reply_markup=reply_markup,
                )

        except Exception as e:
            print(f"Ошибка исправления шаблона: {e}")
            await message.reply_text("❌ Ошибка исправления шаблона. Попробуйте еще раз.")
            # Возвращаем в режим правок, чтобы пользователь мог повторить попытку
            user_context.state = BotState.AWAITING_CORRECTIONS
            user_context.corrections_count = max(0, user_context.corrections_count - 1)
        finally:
            # Удаляем временный файл
            if temp_filepath and Path(temp_filepath).exists():
                try:
                    Path(temp_filepath).unlink()
                except Exception as e:
                    print(f"Ошибка удаления временного файла: {e}")

    async def _save_template(self, message, user_context: BotContext) -> bool:
        """Сохранение шаблона в архив. Возвращает True при успехе."""
        if not user_context.current_template:
            await message.reply_text("❌ Нет шаблона для сохранения")
            return False

        temp_filepath = None
        try:
            template = ExaminationTemplate(
                id=str(uuid.uuid4()),
                clinic=user_context.clinic,
                patient_data=user_context.current_template.patient_data,
                content=user_context.current_template.content,
                created_at=datetime.now(),
            )

            await self.archive_service.save_template(template)

            # Сохраняем в память для обучения бота
            await self.claude_service.memory_service.add_request(
                clinic=user_context.clinic,
                diagnosis=template.patient_data.diagnosis,
                patient_name=template.patient_data.full_name,
                template_content=template.content,
                had_corrections=user_context.corrections_count > 0,
                corrections_count=user_context.corrections_count
            )

            # Отправляем заголовок
            await message.reply_text("✅ Шаблон успешно сохранен в архив!")

            # Создаем .docx файл для финального осмотра
            await message.reply_text("📄 Создаю финальный документ...")
            temp_filepath = self._create_docx_file(
                template.content,
                template.patient_data,
                user_context.clinic
            )

            # Формируем имя файла для отправки (дата осмотра, а не дата создания файла)
            safe_name = "".join(c if c.isalnum() or c == ' ' else '_' for c in template.patient_data.full_name)
            exam_date_str = template.patient_data.examination_date or datetime.now().strftime('%d.%m.%Y')
            filename = f"{safe_name}_ГОТОВЫЙ_ОСМОТР_{exam_date_str}.docx"

            # Отправляем готовый документ
            with open(temp_filepath, 'rb') as doc_file:
                await message.reply_document(
                    document=doc_file,
                    filename=filename,
                    caption="📄 ГОТОВЫЙ ОСМОТР - документ сохранён и готов к использованию!",
                )

            # Отправка email (если настроен)
            if self.email_service:
                # Для Династии спрашиваем о фото осмотра
                if user_context.clinic == ClinicMode.DINASTIYA:
                    # Сохраняем путь к документу для последующей отправки
                    user_context.saved_document_path = temp_filepath
                    user_context.examination_photos = []
                    user_context.state = BotState.AWAITING_PHOTO

                    # Создаем кнопки выбора
                    keyboard = [
                        [
                            InlineKeyboardButton("📷 Добавить фото осмотра", callback_data="add_photo"),
                            InlineKeyboardButton("📧 Отправить без фото", callback_data="send_without_photo"),
                        ]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)

                    await message.reply_text(
                        "📧 Отправка по email\n\n"
                        "Хотите добавить фото осмотра к письму?",
                        reply_markup=reply_markup
                    )
                    # НЕ удаляем temp_filepath - он нужен для отправки email
                    return True
                else:
                    # Для ПСКП добавляем документ в очередь для пакетной отправки
                    examination_date = template.patient_data.examination_date or datetime.now().strftime("%d.%m.%Y")
                    has_eln = not template.patient_data.eln_refused

                    # Добавляем документ в постоянную очередь
                    user_id = message.chat.id
                    success = self.document_queue_service.add_document(
                        user_id=user_id,
                        clinic=user_context.clinic.value,
                        temp_filepath=temp_filepath,
                        patient_name=template.patient_data.full_name,
                        examination_date=examination_date,
                        has_eln=has_eln
                    )

                    if success:
                        # Получаем статус очереди
                        queue_status = self.document_queue_service.get_queue_status(user_id, user_context.clinic.value)
                        eln_status = "с ЭЛН" if has_eln else "БЕЗ ЭЛН (отказ)"

                        await message.reply_text(
                            f"✅ Документ добавлен в очередь ({eln_status})\n\n"
                            f"📋 В очереди: {queue_status['total']} документ(ов)\n"
                            f"• С ЭЛН: {queue_status['with_eln']}\n"
                            f"• Без ЭЛН: {queue_status['without_eln']}\n\n"
                            f"Управление очередью:\n"
                            f"/queue_status - статус очереди\n"
                            f"/send_batch - отправить все документы одним письмом\n"
                            f"/send_batch 28.12.2025 - отправить за конкретную дату\n"
                            f"/clear_queue - очистить очередь"
                        )
                    else:
                        await message.reply_text("⚠️ Не удалось добавить документ в очередь")

            # Сообщение о готовности к следующему пациенту
            await message.reply_text("✅ Можете отправить данные следующего пациента.")

            # Очищаем контекст
            user_context.state = BotState.IDLE
            user_context.current_template = None
            user_context.patient_data = None
            user_context.corrections_count = 0
            user_context.examination_photos = None
            return True

        except Exception as e:
            print(f"Ошибка сохранения шаблона: {e}")
            await message.reply_text("❌ Ошибка сохранения шаблона")
            return False
        finally:
            # Удаляем временный файл только если НЕ ждем фото
            # (если ждем фото, файл будет удален после отправки email)
            if user_context.state != BotState.AWAITING_PHOTO:
                if temp_filepath and Path(temp_filepath).exists():
                    try:
                        Path(temp_filepath).unlink()
                    except Exception as e:
                        print(f"Ошибка удаления временного файла: {e}")

    # ──────────── ГОДОК (Битрикс24) ────────────

    @staticmethod
    def _date_ru_to_iso(date_ru: str) -> str:
        """'25.12.2025' → '2025-12-25'. Пустая строка — сегодняшняя дата."""
        if not date_ru:
            return datetime.now().strftime("%Y-%m-%d")
        try:
            return datetime.strptime(date_ru.strip(), "%d.%m.%Y").strftime("%Y-%m-%d")
        except ValueError:
            return datetime.now().strftime("%Y-%m-%d")

    def _build_godok_context_values(self, patient_data: PatientData, message_time_iso: str) -> Dict[str, str]:
        """Собирает значения context-полей карточки (дата вызова, врач, ЭЛН, работа)."""
        ctx: Dict[str, str] = {}
        ctx["UF_CRM_1601396238"] = message_time_iso  # Фактическая дата и время выполнения вызова
        ctx["UF_CRM_1601396897"] = self.bitrix_service.assigned_doctor()  # Назначенный врач
        if patient_data.workplace:
            ctx["UF_CRM_1601396599"] = patient_data.workplace
        if patient_data.position:
            ctx["UF_CRM_1601396588"] = patient_data.position
        if patient_data.eln_start_date and patient_data.eln_end_date and not patient_data.eln_refused:
            ctx["UF_CRM_1601396409"] = self._date_ru_to_iso(patient_data.eln_start_date)
            ctx["UF_CRM_1601396467"] = self._date_ru_to_iso(patient_data.eln_end_date)
            # Явка в поликлинику — на следующий день после окончания ЭЛН
            try:
                end_dt = datetime.strptime(patient_data.eln_end_date, "%d.%m.%Y")
                from datetime import timedelta
                ctx["UF_CRM_1601397346"] = (end_dt + timedelta(days=1)).strftime("%Y-%m-%d")
            except ValueError:
                pass
        return ctx

    async def _start_godok_flow(self, message, user_context: BotContext, patient_data: PatientData):
        """Ищет сделку в Б24 и запускает генерацию полей карточки."""
        if not self.bitrix_service:
            await message.reply_text("❌ Битрикс24 не настроен. Переключитесь на другую клинику.")
            user_context.state = BotState.IDLE
            return

        exam_date_ru = patient_data.examination_date or datetime.now().strftime("%d.%m.%Y")
        exam_date_iso = self._date_ru_to_iso(exam_date_ru)

        # Запоминаем дату сообщения (время врача) — для "Фактической даты выполнения"
        user_context.godok_message_time = datetime.now().strftime("%Y-%m-%dT%H:%M:%S+03:00")
        user_context.patient_data = {
            "full_name": patient_data.full_name,
            "birth_date": patient_data.birth_date,
            "diagnosis": patient_data.diagnosis,
            "examination_date": exam_date_ru,
            "workplace": patient_data.workplace,
            "position": patient_data.position,
            "eln_start_date": patient_data.eln_start_date,
            "eln_end_date": patient_data.eln_end_date,
            "eln_refused": patient_data.eln_refused,
            "is_student": patient_data.is_student,
        }

        await message.reply_text(f"🔎 Ищу сделку в Битрикс24 (ГОДОК, {exam_date_ru})...")
        try:
            deals = await asyncio.to_thread(
                self.bitrix_service.find_deals_by_patient,
                patient_data.full_name,
                exam_date_iso,
            )
        except BitrixError as e:
            await message.reply_text(f"❌ Ошибка Битрикс24: {e}")
            user_context.state = BotState.IDLE
            return

        if not deals:
            await message.reply_text(
                f"⚠️ Сделка не найдена.\n\n"
                f"ФИО: {patient_data.full_name}\n"
                f"Дата вызова: {exam_date_ru}\n"
                f"Воронка: «ПНД ПОМОЩЬ НА ДОМУ» (CATEGORY_ID=10)\n\n"
                "Проверьте, что вызов создан в Битрикс24 на эту дату, и попробуйте ещё раз."
            )
            user_context.state = BotState.IDLE
            return

        if len(deals) == 1:
            deal = deals[0]
            user_context.godok_deal_id = int(deal["ID"])
            user_context.godok_deal_title = deal.get("TITLE", "")
            await self._generate_godok_preview(message, user_context, patient_data)
            return

        # Несколько сделок — просим выбрать
        user_context.godok_candidates = [
            {"id": int(d["ID"]), "title": d.get("TITLE", ""), "begin": d.get("BEGINDATE", "")}
            for d in deals[:10]
        ]
        user_context.state = BotState.GODOK_AWAITING_DEAL_CHOICE

        buttons = []
        for i, d in enumerate(user_context.godok_candidates):
            label = f"#{d['id']}: {d['title'][:40]}"
            buttons.append([InlineKeyboardButton(label, callback_data=f"godok_pick_deal_{i}")])
        buttons.append([InlineKeyboardButton("❌ Отменить", callback_data="godok_cancel")])
        await message.reply_text(
            f"Найдено сделок: {len(deals)}. Выберите нужную:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    async def _handle_godok_deal_choice(self, message, user_context: BotContext, callback_data: str):
        """Обработка выбора одной из нескольких найденных сделок."""
        if not user_context.godok_candidates:
            await message.reply_text("❌ Список сделок устарел, начните заново.")
            user_context.state = BotState.IDLE
            return
        try:
            idx = int(callback_data.rsplit("_", 1)[-1])
            deal = user_context.godok_candidates[idx]
        except (ValueError, IndexError):
            await message.reply_text("❌ Некорректный выбор сделки.")
            return

        user_context.godok_deal_id = deal["id"]
        user_context.godok_deal_title = deal["title"]
        user_context.godok_candidates = None

        # Восстанавливаем PatientData из сохранённого словаря
        pd = user_context.patient_data or {}
        patient_data = PatientData(
            full_name=pd.get("full_name", ""),
            birth_date=pd.get("birth_date", ""),
            diagnosis=pd.get("diagnosis", ""),
            examination_date=pd.get("examination_date"),
            workplace=pd.get("workplace"),
            position=pd.get("position"),
            eln_start_date=pd.get("eln_start_date"),
            eln_end_date=pd.get("eln_end_date"),
            eln_refused=pd.get("eln_refused", False),
            is_student=pd.get("is_student", False),
        )
        await self._generate_godok_preview(message, user_context, patient_data)

    async def _generate_godok_preview(
        self,
        message,
        user_context: BotContext,
        patient_data: PatientData,
        corrections: str = "",
    ):
        """Генерирует значения полей через Claude и показывает предпросмотр."""
        try:
            await message.reply_text("🤖 Заполняю карточку осмотра (Claude + Битрикс24)...")
            ai_specs = self.bitrix_service.ai_field_specs()
            ai_values = await self.claude_service.generate_godok_fields(
                patient_data=patient_data,
                ai_field_specs=ai_specs,
                current_values=user_context.godok_fields if corrections else None,
                corrections=corrections or None,
            )
        except Exception as e:
            print(f"Ошибка генерации полей ГОДОК: {e}")
            await message.reply_text(f"❌ Не удалось сгенерировать поля: {e}")
            user_context.state = BotState.IDLE
            return

        context_values = self._build_godok_context_values(patient_data, user_context.godok_message_time)
        payload = self.bitrix_service.build_update_payload(ai_values, context_values)
        user_context.godok_fields = payload
        user_context.state = BotState.GODOK_AWAITING_CONFIRMATION

        preview = self._format_godok_preview(payload, ai_specs, patient_data, user_context.godok_deal_title)
        buttons = [
            [
                InlineKeyboardButton("✅ Записать в Б24", callback_data="godok_confirm_write"),
                InlineKeyboardButton("✏️ Правки", callback_data="godok_request_corrections"),
            ],
            [InlineKeyboardButton("❌ Отменить", callback_data="godok_cancel")],
        ]
        # Telegram лимит — 4096 символов на сообщение; карточка обычно укладывается.
        if len(preview) > 3900:
            preview = preview[:3900] + "\n…(обрезано)"
        await message.reply_text(preview, reply_markup=InlineKeyboardMarkup(buttons))

    def _format_godok_preview(
        self,
        payload: Dict[str, object],
        ai_specs: Dict[str, dict],
        patient_data: PatientData,
        deal_title: str,
    ) -> str:
        """Форматирует словарь полей для предпросмотра в Telegram."""
        lines = [
            f"📋 Карточка ГОДОК — сделка «{deal_title}»",
            f"Пациент: {patient_data.full_name} ({patient_data.birth_date})",
            f"Диагноз: {patient_data.diagnosis}",
            "",
        ]
        for code, value in payload.items():
            spec = ai_specs.get(code)
            if spec:
                label = spec["label"]
                if spec["type"] == "enumeration":
                    opt = next((o["value"] for o in spec["options"] if o["id"] == value), str(value))
                    lines.append(f"• {label}: {opt}")
                else:
                    text = str(value)
                    if len(text) > 180:
                        text = text[:180] + "…"
                    lines.append(f"• {label}: {text}")
            else:
                # context-поле
                lines.append(f"• [ctx] {code}: {value}")
        lines.append("")
        lines.append("Нажмите «Записать в Б24», чтобы обновить сделку.")
        return "\n".join(lines)

    async def _handle_godok_corrections(self, message, user_context: BotContext, corrections: str):
        """Применяет правки врача и перегенерирует карточку."""
        if not user_context.godok_fields or not user_context.patient_data:
            await message.reply_text("❌ Нет активной карточки для правок.")
            user_context.state = BotState.IDLE
            return
        pd = user_context.patient_data
        patient_data = PatientData(
            full_name=pd.get("full_name", ""),
            birth_date=pd.get("birth_date", ""),
            diagnosis=pd.get("diagnosis", ""),
            examination_date=pd.get("examination_date"),
            workplace=pd.get("workplace"),
            position=pd.get("position"),
            eln_start_date=pd.get("eln_start_date"),
            eln_end_date=pd.get("eln_end_date"),
            eln_refused=pd.get("eln_refused", False),
            is_student=pd.get("is_student", False),
        )
        # Готовим current_values только для AI-полей (чтобы Claude видел, что менять)
        ai_codes = set(self.bitrix_service.ai_field_specs().keys())
        current_ai_values = {
            code: val for code, val in (user_context.godok_fields or {}).items() if code in ai_codes
        }
        # Временно подменим, чтобы _generate_godok_preview передал current_values
        user_context.godok_fields = current_ai_values
        await self._generate_godok_preview(message, user_context, patient_data, corrections=corrections)

    async def _handle_godok_confirm(self, message, user_context: BotContext):
        """Записывает карточку в Битрикс24."""
        if not user_context.godok_deal_id or not user_context.godok_fields:
            await message.reply_text("❌ Нет данных для записи.")
            user_context.state = BotState.IDLE
            return
        try:
            await message.reply_text("💾 Обновляю сделку в Битрикс24...")
            ok = await asyncio.to_thread(
                self.bitrix_service.update_deal,
                user_context.godok_deal_id,
                user_context.godok_fields,
            )
        except BitrixError as e:
            await message.reply_text(f"❌ Битрикс24 отклонил обновление: {e}")
            return

        if ok:
            await message.reply_text(
                f"✅ Сделка #{user_context.godok_deal_id} обновлена.\n"
                f"«{user_context.godok_deal_title}»"
            )
        else:
            await message.reply_text("⚠️ Битрикс24 вернул неуспешный ответ. Проверьте сделку вручную.")

        # Сброс контекста
        user_context.state = BotState.IDLE
        user_context.godok_deal_id = None
        user_context.godok_deal_title = None
        user_context.godok_fields = None
        user_context.godok_candidates = None
        user_context.godok_message_time = None
        user_context.patient_data = None

    def _parse_text_data(self, text: str) -> Dict[str, str]:
        """Парсинг текстовых данных пациента"""
        data = {}

        lines = text.split("\n")
        for line in lines:
            line = line.strip()

            if re.match(r"^фио\s*:", line, re.IGNORECASE):
                data["full_name"] = re.sub(r"^фио\s*:\s*", "", line, flags=re.IGNORECASE).strip()
            elif re.match(r"^дата рождения\s*:", line, re.IGNORECASE):
                raw_bd = re.sub(r"^дата рождения\s*:\s*", "", line, flags=re.IGNORECASE).strip()
                from datetime import datetime as _dt_bd
                try:
                    _dt_bd.strptime(raw_bd, "%d.%m.%Y")
                    data["birth_date"] = raw_bd
                except ValueError:
                    data["_birth_date_error"] = raw_bd
            elif re.match(r"^снилс\s*:", line, re.IGNORECASE):
                data["snils"] = re.sub(r"^снилс\s*:\s*", "", line, flags=re.IGNORECASE).strip()
            elif re.match(r"^диагноз\s*:", line, re.IGNORECASE):
                data["diagnosis"] = re.sub(r"^диагноз\s*:\s*", "", line, flags=re.IGNORECASE).strip()
            # Поля для работы / учёбы
            elif re.match(r"^место работы\s*:", line, re.IGNORECASE):
                data["workplace"] = re.sub(r"^место работы\s*:\s*", "", line, flags=re.IGNORECASE).strip()
                print(f"✓ Место работы: {data['workplace']}")
            elif re.match(r"^место уч[её]бы\s*:", line, re.IGNORECASE):
                data["workplace"] = re.sub(r"^место уч[её]бы\s*:\s*", "", line, flags=re.IGNORECASE).strip()
                data["is_student"] = True
                print(f"✓ Место учёбы (студент): {data['workplace']}")
            elif re.match(r"^должность\s*:", line, re.IGNORECASE):
                data["position"] = re.sub(r"^должность\s*:\s*", "", line, flags=re.IGNORECASE).strip()
                print(f"✓ Должность: {data['position']}")
            elif re.match(r"^курс\s*:", line, re.IGNORECASE):
                data["position"] = re.sub(r"^курс\s*:\s*", "", line, flags=re.IGNORECASE).strip()
                data["is_student"] = True
                print(f"✓ Курс (студент): {data['position']}")
            # Студенческая справка (вместо ЭЛН)
            # Форматы: "Выдана студенческая справка:", "Выдать студенческая справка:",
            #          "Студенческая справка:", и т.д.
            elif re.match(r"^(?:выда\w+\s+)?студенческ\w+\s+справк\w*\s*:", line, re.IGNORECASE):
                cert_text = re.sub(r"^(?:выда\w+\s+)?студенческ\w+\s+справк\w*\s*:\s*", "", line, flags=re.IGNORECASE).strip()
                data["student_certificate"] = cert_text
                data["is_student"] = True
                data["eln_refused"] = True  # Студ. справка = без ЭЛН для email-роутинга
                print(f"✓ Студенческая справка: {cert_text}")
                # Извлекаем конечную дату из справки для явки к врачу
                from datetime import datetime as _dt
                date_match = re.search(
                    r"(?:с\s+)?(\d{1,2}\.\d{2}(?:\.\d{4})?)\s*(?:по|-)\s*(\d{1,2}\.\d{2}(?:\.\d{4})?)",
                    cert_text
                )
                if date_match:
                    try:
                        start_str = date_match.group(1)
                        end_str = date_match.group(2)
                        start_parts = start_str.split('.')
                        end_parts = end_str.split('.')
                        if len(end_parts) == 3:
                            year = end_parts[2]
                        elif len(start_parts) == 3:
                            year = start_parts[2]
                        else:
                            year = str(_dt.now().year)
                        start_full = start_str if len(start_parts) == 3 else f"{start_str}.{year}"
                        end_full = end_str if len(end_parts) == 3 else f"{end_str}.{year}"
                        start_date_cert = _dt.strptime(start_full, "%d.%m.%Y")
                        end_date_cert = _dt.strptime(end_full, "%d.%m.%Y")
                        # Если год не указан и конец раньше начала — конец в следующем году
                        if len(end_parts) == 2 and len(start_parts) != 3 and end_date_cert < start_date_cert:
                            end_date_cert = end_date_cert.replace(year=end_date_cert.year + 1)
                        if end_date_cert < start_date_cert:
                            print(f"⚠️ Дата окончания справки раньше начала, пропускаем")
                            data["_cert_date_error"] = True
                        else:
                            data["student_cert_end_date"] = end_date_cert.strftime("%d.%m.%Y")
                            print(f"✓ Дата окончания справки (явка): {data['student_cert_end_date']}")
                    except Exception as e:
                        print(f"⚠️ Ошибка парсинга даты справки: {e}")
                        data["_cert_date_error"] = True
            # Новые поля для дат
            elif re.match(r"^дата осмотра\s*:", line, re.IGNORECASE):
                data["examination_date"] = re.sub(r"^дата осмотра\s*:\s*", "", line, flags=re.IGNORECASE).strip()
            elif re.match(r"^начало болезни\s*:", line, re.IGNORECASE):
                data["illness_start_date"] = re.sub(r"^начало болезни\s*:\s*", "", line, flags=re.IGNORECASE).strip()
            elif re.match(r"^элн\b", line, re.IGNORECASE):
                # Парсим "ЭЛН: 5 дней" / "ЭЛН: отказ" / "ЭЛН открыт с 26.03 по 31.03.2026"
                # Убираем "ЭЛН" + необязательные двоеточие/слово-связку
                eln_text = re.sub(r"^элн\s*(?:открыт\s*)?:?\s*", "", line, flags=re.IGNORECASE).strip().lower()
                print(f"🔍 Парсинг ЭЛН: '{line}' -> '{eln_text}'")

                # Проверяем на отказ
                if "отказ" in eln_text or "нет" in eln_text:
                    data["eln_refused"] = True
                    data["sick_leave_days"] = None
                    print(f"✓ ЭЛН отказ распознан")
                else:
                    # Пытаемся извлечь срок из различных форматов дат:
                    # "с ДД.ММ по ДД.ММ.ГГГГ", "ДД.ММ.ГГГГ по ДД.ММ.ГГГГ",
                    # "срок с ДД.ММ по ДД.ММ", "с ДД.ММ по ДД.ММ" и т.д.
                    # Универсальный паттерн: опциональные "срок"/"с", дата (ДД.ММ или ДД.ММ.ГГГГ), "по"/-", дата
                    date_range_match = re.search(
                        r"(?:срок\s+)?(?:с\s+)?(\d{1,2}\.\d{2}(?:\.\d{4})?)\s*(?:по|-)\s*(\d{1,2}\.\d{2}(?:\.\d{4})?)",
                        eln_text
                    )
                    if date_range_match:
                        from datetime import datetime
                        try:
                            start_str = date_range_match.group(1)
                            end_str = date_range_match.group(2)

                            start_parts = start_str.split('.')
                            end_parts = end_str.split('.')

                            # Определяем год: берём из той даты, где он указан, или текущий
                            if len(end_parts) == 3:
                                year = end_parts[2]
                            elif len(start_parts) == 3:
                                year = start_parts[2]
                            else:
                                year = str(datetime.now().year)

                            # Формируем полные даты с годом
                            start_str_full = start_str if len(start_parts) == 3 else f"{start_str}.{year}"
                            end_str_full = end_str if len(end_parts) == 3 else f"{end_str}.{year}"

                            start_date = datetime.strptime(start_str_full, "%d.%m.%Y")
                            end_date = datetime.strptime(end_str_full, "%d.%m.%Y")

                            # Если год не был явно указан и конец раньше начала — конец в следующем году
                            if len(end_parts) == 2 and len(start_parts) != 3 and end_date < start_date:
                                end_date = end_date.replace(year=end_date.year + 1)

                            # Если конец всё равно раньше начала — ошибка в данных пользователя
                            if end_date < start_date:
                                print(f"⚠️ Дата окончания ЭЛН раньше начала")
                                data["_eln_parse_error"] = True
                            else:
                                days_value = (end_date - start_date).days + 1  # Включительно
                                data["sick_leave_days"] = days_value
                                data["eln_refused"] = False
                                # Сохраняем точные даты ЭЛН
                                data["eln_start_date"] = start_date.strftime("%d.%m.%Y")
                                data["eln_end_date"] = end_date.strftime("%d.%m.%Y")
                                print(f"✓ ЭЛН срок: с {start_date.strftime('%d.%m.%Y')} по {end_date.strftime('%d.%m.%Y')} = {days_value} дней")
                        except Exception as e:
                            print(f"⚠️ Ошибка парсинга дат ЭЛН: {e}")
                            data["_eln_parse_error"] = True
                    # Или ищем "N дней/дня"
                    elif re.search(r"(\d{1,3})\s*(?:день|дня|дней)", eln_text):
                        days_match = re.search(r"(\d{1,3})\s*(?:день|дня|дней)", eln_text)
                        days_value = int(days_match.group(1))
                        if 1 <= days_value <= 60:
                            data["sick_leave_days"] = days_value
                            data["eln_refused"] = False
                            print(f"✓ ЭЛН дней: {days_value}")
                        else:
                            data["_eln_days_error"] = days_value
                    # Или просто число (1–60 дней)
                    elif re.search(r"\b(\d{1,2})\b", eln_text):
                        match = re.search(r"\b(\d{1,2})\b", eln_text)
                        days_value = int(match.group(1))
                        if 1 <= days_value <= 60:
                            data["sick_leave_days"] = days_value
                            data["eln_refused"] = False
                            print(f"✓ ЭЛН дней (число): {days_value}")
                        else:
                            data["_eln_days_error"] = days_value

        return data

    async def _send_email_without_photos(self, message, user_context: BotContext):
        """Отправка email без фото осмотра"""
        try:
            if not user_context.current_template:
                await message.reply_text("❌ Ошибка: шаблон не найден")
                return

            await message.reply_text("📧 Отправляю документ по email...")

            template = user_context.current_template
            examination_date = template.patient_data.examination_date or datetime.now().strftime("%d.%m.%Y")

            # Проверяем, есть ли ЭЛН (False если отказ)
            has_eln = not template.patient_data.eln_refused

            success, error = await self.email_service.send_document(
                file_path=user_context.saved_document_path,
                patient_name=template.patient_data.full_name,
                examination_date=examination_date,
                clinic=user_context.clinic.value,
                doctor_name="Гаджимурадлы Д.Д",
                has_eln=has_eln
            )

            if success:
                # Получаем список всех получателей
                all_recipients = self.email_service.get_recipients(clinic=user_context.clinic.value, recipient_type="all")
                eln_recipients = self.email_service.get_recipients(clinic=user_context.clinic.value, recipient_type="eln_only") if has_eln else []
                total_recipients = list(set(all_recipients + eln_recipients))

                eln_status = "с ЭЛН" if has_eln else "БЕЗ ЭЛН (отказ)"
                await message.reply_text(
                    f"✅ Email отправлен для клиники \"Династия\" ({eln_status})!\n"
                    f"Получатели: {', '.join(total_recipients)}"
                )
            else:
                await message.reply_text(f"⚠️ Ошибка отправки email:\n{error}")

            # Очищаем контекст и удаляем временный файл
            await self._cleanup_after_email(user_context)

            await message.reply_text("✅ Можете отправить данные следующего пациента.")

        except Exception as e:
            print(f"Ошибка отправки email: {e}")
            await message.reply_text("❌ Ошибка отправки email")
            await self._cleanup_after_email(user_context)

    async def _send_email_with_photos(self, message, user_context: BotContext):
        """Отправка email с фото осмотра"""
        try:
            if not user_context.current_template:
                await message.reply_text("❌ Ошибка: шаблон не найден")
                return

            if not user_context.examination_photos or len(user_context.examination_photos) == 0:
                await message.reply_text("⚠️ Вы не добавили ни одного фото. Отправляю без фото...")
                await self._send_email_without_photos(message, user_context)
                return

            await message.reply_text(
                f"📧 Отправляю документ с {len(user_context.examination_photos)} фото по email..."
            )

            template = user_context.current_template
            examination_date = template.patient_data.examination_date or datetime.now().strftime("%d.%m.%Y")

            # Проверяем, есть ли ЭЛН (False если отказ)
            has_eln = not template.patient_data.eln_refused

            success, error = await self.email_service.send_document(
                file_path=user_context.saved_document_path,
                patient_name=template.patient_data.full_name,
                examination_date=examination_date,
                clinic=user_context.clinic.value,
                doctor_name="Гаджимурадлы Д.Д",
                photos=user_context.examination_photos,
                has_eln=has_eln
            )

            if success:
                # Получаем список всех получателей
                all_recipients = self.email_service.get_recipients(clinic=user_context.clinic.value, recipient_type="all")
                eln_recipients = self.email_service.get_recipients(clinic=user_context.clinic.value, recipient_type="eln_only") if has_eln else []
                total_recipients = list(set(all_recipients + eln_recipients))

                eln_status = "с ЭЛН" if has_eln else "БЕЗ ЭЛН (отказ)"
                await message.reply_text(
                    f"✅ Email отправлен для клиники \"Династия\" ({eln_status})!\n"
                    f"Получатели: {', '.join(total_recipients)}\n"
                    f"Прикреплено: документ + {len(user_context.examination_photos)} фото"
                )
            else:
                await message.reply_text(f"⚠️ Ошибка отправки email:\n{error}")

            # Очищаем контекст и удаляем временный файл
            await self._cleanup_after_email(user_context)

            await message.reply_text("✅ Можете отправить данные следующего пациента.")

        except Exception as e:
            print(f"Ошибка отправки email с фото: {e}")
            await message.reply_text("❌ Ошибка отправки email")
            await self._cleanup_after_email(user_context)

    async def _cleanup_after_email(self, user_context: BotContext):
        """Очистка контекста и удаление временных файлов после отправки email"""
        # Удаляем временный файл документа
        if user_context.saved_document_path and Path(user_context.saved_document_path).exists():
            try:
                Path(user_context.saved_document_path).unlink()
            except Exception as e:
                print(f"Ошибка удаления временного файла: {e}")

        # Очищаем контекст
        user_context.state = BotState.IDLE
        user_context.current_template = None
        user_context.patient_data = None
        user_context.corrections_count = 0
        user_context.saved_document_path = None
        user_context.examination_photos = None

    def run(self):
        """Запуск бота с обработкой ошибок подключения"""
        print("🤖 Запуск Telegram бота...")
        print("📡 Подключение к Telegram API...")

        try:
            # Запуск с параметрами для retry логики
            self.application.run_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True,  # Игнорировать старые обновления
                close_loop=False,
            )
        except Exception as e:
            print(f"\n❌ Ошибка подключения к Telegram: {e}")
            print("\n💡 Возможные решения:")
            print("1. Проверьте подключение к интернету")
            print("2. Убедитесь, что токен бота правильный (файл .env)")
            print("3. Telegram может быть заблокирован в вашей сети - попробуйте VPN")
            print("4. Проверьте, что бот активирован через @BotFather")
            raise
