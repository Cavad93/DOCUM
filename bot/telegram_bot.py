import asyncio
import base64
import re
import uuid
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from docx import Document

from bot.models.types import PatientData, ClinicMode, BotState, BotContext, ExaminationTemplate, CurrentTemplate
from bot.services.claude_service import ClaudeService
from bot.services.archive_service import ArchiveService
from bot.services.email_service import EmailService


class MedicalBot:
    """Telegram бот для создания медицинских шаблонов"""

    def __init__(
        self,
        telegram_token: str,
        claude_api_key: str,
        smtp_host: str = "",
        smtp_port: int = 587,
        smtp_user: str = "",
        smtp_password: str = ""
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
        self.user_contexts: Dict[int, BotContext] = {}

        # Email сервис (опциональный)
        if smtp_user and smtp_password:
            self.email_service = EmailService(smtp_host, smtp_port, smtp_user, smtp_password)
            print("✓ Email сервис инициализирован")
        else:
            self.email_service = None
            print("⚠️ Email не настроен (добавьте SMTP_USER и SMTP_PASSWORD в .env)")

        self._setup_handlers()

    def _setup_handlers(self):
        """Настройка обработчиков команд и сообщений"""
        # Команды
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("stats", self.stats_command))

        # Email команды
        self.application.add_handler(CommandHandler("add_email", self.add_email_command))
        self.application.add_handler(CommandHandler("remove_email", self.remove_email_command))
        self.application.add_handler(CommandHandler("list_emails", self.list_emails_command))

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
            ]
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
            "  ЭЛН: 5 дней (необязательно, по умолчанию 3 дня)\n"
            "  ЭЛН: отказ (если пациент отказался от больничного листа)\n"
            "  Дата осмотра: 25.12.2025 (необязательно, по умолчанию сегодня)\n"
            "  Начало болезни: 24.12.2025 (необязательно, рассчитывается автоматически)\n\n"
            "Команды:\n"
            "/stats - вернуться в главное меню\n"
            "/help - помощь\n"
            "/add_email - добавить получателя email для текущей клиники\n"
            "/remove_email - удалить получателя email\n"
            "/list_emails - показать список получателей текущей клиники",
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
            "ЭЛН: 5 дней (или просто: ЭЛН: 5)\n"
            "Дата осмотра: 25.12.2025\n"
            "Начало болезни: 24.12.2025\n\n"
            "Бот автоматически:\n"
            "• Обновит все даты в шаблоне на актуальные\n"
            "• Рассчитает период ЭЛН и дату явки к врачу\n"
            "• Сохранит оформление и структуру шаблона\n\n"
            "Email команды:\n"
            "/add_email адрес@example.com - добавить получателя для текущей клиники\n"
            "/remove_email адрес@example.com - удалить получателя\n"
            "/list_emails - показать список получателей текущей клиники"
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
            ]
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
            "  ЭЛН: 5 дней (необязательно, по умолчанию 3 дня)\n"
            "  ЭЛН: отказ (если пациент отказался от больничного листа)\n"
            "  Дата осмотра: 25.12.2025 (необязательно, по умолчанию сегодня)\n"
            "  Начало болезни: 24.12.2025 (необязательно, рассчитывается автоматически)\n\n"
            "Команды:\n"
            "/stats - вернуться в главное меню\n"
            "/help - помощь\n"
            "/add_email - добавить получателя email для текущей клиники\n"
            "/remove_email - удалить получателя email\n"
            "/list_emails - показать список получателей текущей клиники",
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

    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка нажатий на inline кнопки"""
        query = update.callback_query
        await query.answer()

        user_id = update.effective_user.id
        user_context = self._get_or_create_context(user_id)
        data = query.data

        if data == "clinic_dinastiya":
            user_context.clinic = ClinicMode.DINASTIYA
            await query.message.reply_text("✅ Выбрана клиника \"Династия\"")

        elif data == "clinic_pskp":
            user_context.clinic = ClinicMode.PSKP
            await query.message.reply_text("✅ Выбрана клиника \"ПСКП\"")

        elif data == "confirm_template":
            await self._save_template(query.message, user_context)
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

    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка фото документа"""
        user_id = update.effective_user.id
        user_context = self._get_or_create_context(user_id)

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

            # Распознаем данные
            extracted_data = await self.claude_service.extract_data_from_image(image_base64, "image/jpeg")

            # Сохраняем в контекст
            if user_context.patient_data is None:
                user_context.patient_data = {}

            user_context.patient_data.update(extracted_data)

            message = "✅ Данные распознаны:\n\n"
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

            full_name = parsed_data.get("full_name") or user_context.patient_data.get("full_name", "")
            birth_date = parsed_data.get("birth_date") or user_context.patient_data.get("birth_date", "")
            diagnosis = parsed_data.get("diagnosis", "")
            snils = parsed_data.get("snils") or user_context.patient_data.get("snils")

            # Новые поля для дат
            examination_date = parsed_data.get("examination_date")
            illness_start_date = parsed_data.get("illness_start_date")
            sick_leave_days = parsed_data.get("sick_leave_days")
            eln_refused = parsed_data.get("eln_refused", False)

            # Проверка обязательных полей
            if not full_name or not birth_date or not diagnosis:
                await update.message.reply_text(
                    "❌ Не хватает данных. Убедитесь, что указаны ФИО, дата рождения и диагноз."
                )
                return

            patient_data = PatientData(
                full_name=full_name,
                birth_date=birth_date,
                diagnosis=diagnosis,
                snils=snils,
                examination_date=examination_date,
                illness_start_date=illness_start_date,
                sick_leave_days=sick_leave_days,
                eln_refused=eln_refused,
            )

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

            # Формируем имя файла для отправки
            safe_name = "".join(c if c.isalnum() or c == ' ' else '_' for c in patient_data.full_name)
            filename = f"{safe_name}_осмотр_{datetime.now().strftime('%d.%m.%Y')}.docx"

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
            )

            # Обновляем в контексте
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

            # Создаем .docx файл из исправленного шаблона
            await message.reply_text("📄 Создаю исправленный документ...")
            temp_filepath = self._create_docx_file(
                corrected_template,
                user_context.current_template.patient_data,
                user_context.clinic
            )

            # Формируем имя файла для отправки
            safe_name = "".join(c if c.isalnum() or c == ' ' else '_' for c in user_context.current_template.patient_data.full_name)
            filename = f"{safe_name}_осмотр_исправлен_{datetime.now().strftime('%d.%m.%Y')}.docx"

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
            user_context.state = BotState.AWAITING_CONFIRMATION
        finally:
            # Удаляем временный файл
            if temp_filepath and Path(temp_filepath).exists():
                try:
                    Path(temp_filepath).unlink()
                except Exception as e:
                    print(f"Ошибка удаления временного файла: {e}")

    async def _save_template(self, message, user_context: BotContext):
        """Сохранение шаблона в архив"""
        if not user_context.current_template:
            await message.reply_text("❌ Нет шаблона для сохранения")
            return

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

            # Формируем имя файла для отправки
            safe_name = "".join(c if c.isalnum() or c == ' ' else '_' for c in template.patient_data.full_name)
            filename = f"{safe_name}_ГОТОВЫЙ_ОСМОТР_{datetime.now().strftime('%d.%m.%Y')}.docx"

            # Отправляем готовый документ
            with open(temp_filepath, 'rb') as doc_file:
                await message.reply_document(
                    document=doc_file,
                    filename=filename,
                    caption="📄 ГОТОВЫЙ ОСМОТР - документ сохранён и готов к использованию!",
                )

            # Отправка email (если настроен)
            if self.email_service:
                await message.reply_text("📧 Отправляю документ по email...")
                examination_date = template.patient_data.examination_date or datetime.now().strftime("%d.%m.%Y")
                clinic_name = "Династия" if user_context.clinic == ClinicMode.DINASTIYA else "ПСКП"

                success, error = await self.email_service.send_document(
                    file_path=temp_filepath,
                    patient_name=template.patient_data.full_name,
                    examination_date=examination_date,
                    clinic=user_context.clinic.value,
                    doctor_name="Гаджимурадлы Д.Д"
                )

                if success:
                    recipients = self.email_service.get_recipients(clinic=user_context.clinic.value)
                    await message.reply_text(
                        f"✅ Email отправлен для клиники \"{clinic_name}\"!\n"
                        f"Получатели: {', '.join(recipients)}"
                    )
                else:
                    await message.reply_text(f"⚠️ Ошибка отправки email:\n{error}")

            # Сообщение о готовности к следующему пациенту
            await message.reply_text("✅ Можете отправить данные следующего пациента.")

            # Очищаем контекст
            user_context.state = BotState.IDLE
            user_context.current_template = None
            user_context.patient_data = None
            user_context.corrections_count = 0  # Сбрасываем счетчик правок

        except Exception as e:
            print(f"Ошибка сохранения шаблона: {e}")
            await message.reply_text("❌ Ошибка сохранения шаблона")
        finally:
            # Удаляем временный файл
            if temp_filepath and Path(temp_filepath).exists():
                try:
                    Path(temp_filepath).unlink()
                except Exception as e:
                    print(f"Ошибка удаления временного файла: {e}")

    def _parse_text_data(self, text: str) -> Dict[str, str]:
        """Парсинг текстовых данных пациента"""
        data = {}

        lines = text.split("\n")
        for line in lines:
            line = line.strip()

            if re.match(r"^фио\s*:", line, re.IGNORECASE):
                data["full_name"] = re.sub(r"^фио\s*:\s*", "", line, flags=re.IGNORECASE).strip()
            elif re.match(r"^дата рождения\s*:", line, re.IGNORECASE):
                data["birth_date"] = re.sub(r"^дата рождения\s*:\s*", "", line, flags=re.IGNORECASE).strip()
            elif re.match(r"^снилс\s*:", line, re.IGNORECASE):
                data["snils"] = re.sub(r"^снилс\s*:\s*", "", line, flags=re.IGNORECASE).strip()
            elif re.match(r"^диагноз\s*:", line, re.IGNORECASE):
                data["diagnosis"] = re.sub(r"^диагноз\s*:\s*", "", line, flags=re.IGNORECASE).strip()
            # Новые поля для дат
            elif re.match(r"^дата осмотра\s*:", line, re.IGNORECASE):
                data["examination_date"] = re.sub(r"^дата осмотра\s*:\s*", "", line, flags=re.IGNORECASE).strip()
            elif re.match(r"^начало болезни\s*:", line, re.IGNORECASE):
                data["illness_start_date"] = re.sub(r"^начало болезни\s*:\s*", "", line, flags=re.IGNORECASE).strip()
            elif re.match(r"^элн\s*:", line, re.IGNORECASE):
                # Парсим "ЭЛН: 5 дней" или "ЭЛН: 5" или "ЭЛН: отказ"
                # или "ЭЛН: 910317368797 срок с 26.12 по 30.12.2025"
                eln_text = re.sub(r"^элн\s*:\s*", "", line, flags=re.IGNORECASE).strip().lower()
                print(f"🔍 Парсинг ЭЛН: '{line}' -> '{eln_text}'")

                # Проверяем на отказ
                if "отказ" in eln_text or "нет" in eln_text:
                    data["eln_refused"] = True
                    data["sick_leave_days"] = None
                    print(f"✓ ЭЛН отказ распознан")
                else:
                    # Пытаемся извлечь срок из формата "срок с ДД.ММ по ДД.ММ"
                    date_range_match = re.search(r"срок\s+с\s+(\d{2}\.\d{2}).*?по\s+(\d{2}\.\d{2})", eln_text)
                    if date_range_match:
                        from datetime import datetime
                        try:
                            start_str = date_range_match.group(1)
                            end_str = date_range_match.group(2)
                            # Добавляем текущий год
                            current_year = datetime.now().year
                            start_date = datetime.strptime(f"{start_str}.{current_year}", "%d.%m.%Y")
                            end_date = datetime.strptime(f"{end_str}.{current_year}", "%d.%m.%Y")
                            days_value = (end_date - start_date).days + 1  # Включительно
                            data["sick_leave_days"] = days_value
                            data["eln_refused"] = False
                            print(f"✓ ЭЛН срок: с {start_str} по {end_str} = {days_value} дней")
                        except Exception as e:
                            print(f"⚠️ Ошибка парсинга дат ЭЛН: {e}")
                    # Или ищем "N дней/дня"
                    elif re.search(r"(\d{1,2})\s*(?:день|дня|дней)", eln_text):
                        days_match = re.search(r"(\d{1,2})\s*(?:день|дня|дней)", eln_text)
                        days_value = int(days_match.group(1))
                        data["sick_leave_days"] = days_value
                        data["eln_refused"] = False
                        print(f"✓ ЭЛН дней: {days_value}")
                    # Или просто число (НЕ СНИЛС - не больше 2 цифр)
                    elif re.search(r"\b(\d{1,2})\b", eln_text):
                        match = re.search(r"\b(\d{1,2})\b", eln_text)
                        days_value = int(match.group(1))
                        data["sick_leave_days"] = days_value
                        data["eln_refused"] = False
                        print(f"✓ ЭЛН дней (число): {days_value}")

        return data

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
