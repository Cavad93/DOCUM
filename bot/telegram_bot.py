import asyncio
import base64
import re
import uuid
from datetime import datetime
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

from bot.models.types import PatientData, ClinicMode, BotState, BotContext, ExaminationTemplate, CurrentTemplate
from bot.services.claude_service import ClaudeService
from bot.services.archive_service import ArchiveService


class MedicalBot:
    """Telegram бот для создания медицинских шаблонов"""

    def __init__(self, telegram_token: str, claude_api_key: str):
        """
        Инициализация бота

        Args:
            telegram_token: Токен Telegram бота
            claude_api_key: API ключ Claude
        """
        self.application = Application.builder().token(telegram_token).build()
        self.claude_service = ClaudeService(claude_api_key)
        self.archive_service = ArchiveService()
        self.user_contexts: Dict[int, BotContext] = {}

        self._setup_handlers()

    def _setup_handlers(self):
        """Настройка обработчиков команд и сообщений"""
        # Команды
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("stats", self.stats_command))

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
            "  Дата осмотра: 25.12.2025 (необязательно, по умолчанию сегодня)\n"
            "  Начало болезни: 24.12.2025 (необязательно, рассчитывается автоматически)\n\n"
            "Команды:\n"
            "/stats - статистика архива\n"
            "/help - помощь",
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
            "• Сохранит оформление и структуру шаблона"
        )

    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка команды /stats"""
        try:
            stats = await self.archive_service.get_statistics()
            message = f"📊 Статистика архива:\n\nВсего шаблонов: {stats['total']}\n\n"

            for clinic, count in stats["by_clinic"].items():
                message += f"{clinic}: {count}\n"

            await update.message.reply_text(message)
        except Exception as e:
            await update.message.reply_text("❌ Ошибка получения статистики")

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
            )

            # Генерируем шаблон
            await self._generate_and_show_template(update.message, user_context, patient_data)

        except Exception as e:
            print(f"Ошибка обработки текста: {e}")
            await update.message.reply_text("❌ Ошибка обработки сообщения. Попробуйте еще раз.")

    async def _generate_and_show_template(self, message, user_context: BotContext, patient_data: PatientData):
        """Генерация и показ шаблона с кнопками подтверждения"""
        try:
            # Ищем похожие шаблоны
            await message.reply_text("🔎 Ищу похожие шаблоны в архиве...")
            similar_templates = await self.archive_service.get_similar_templates(
                patient_data.diagnosis, user_context.clinic, 3
            )

            # Генерируем шаблон
            await message.reply_text("✍️ Создаю шаблон осмотра...")
            template_content = await self.claude_service.generate_examination_template(
                patient_data, user_context.clinic, similar_templates
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

            # Отправляем шаблон
            await message.reply_text(
                f"✅ Шаблон осмотра создан для клиники \"{user_context.clinic.value}\"!\n\n{template_content}",
                reply_markup=reply_markup,
            )

            await message.reply_text("👆 Проверьте шаблон и выберите действие:", reply_markup=reply_markup)

        except Exception as e:
            print(f"Ошибка генерации шаблона: {e}")
            await message.reply_text("❌ Ошибка создания шаблона. Попробуйте еще раз.")
            user_context.state = BotState.IDLE

    async def _handle_corrections(self, message, user_context: BotContext, corrections: str):
        """Обработка комментариев для правок"""
        if not user_context.current_template:
            await message.reply_text("❌ Нет шаблона для исправления")
            user_context.state = BotState.IDLE
            return

        try:
            await message.reply_text("🔄 Вношу исправления...")

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

            # Отправляем исправленный шаблон
            await message.reply_text(f"✅ Шаблон исправлен!\n\n{corrected_template}", reply_markup=reply_markup)

            await message.reply_text("👆 Проверьте исправленный шаблон:", reply_markup=reply_markup)

        except Exception as e:
            print(f"Ошибка исправления шаблона: {e}")
            await message.reply_text("❌ Ошибка исправления шаблона. Попробуйте еще раз.")
            user_context.state = BotState.AWAITING_CONFIRMATION

    async def _save_template(self, message, user_context: BotContext):
        """Сохранение шаблона в архив"""
        if not user_context.current_template:
            await message.reply_text("❌ Нет шаблона для сохранения")
            return

        try:
            template = ExaminationTemplate(
                id=str(uuid.uuid4()),
                clinic=user_context.clinic,
                patient_data=user_context.current_template.patient_data,
                content=user_context.current_template.content,
                created_at=datetime.now(),
            )

            await self.archive_service.save_template(template)

            await message.reply_text("✅ Шаблон успешно сохранен в архив!\n\nМожете отправить данные следующего пациента.")

            # Очищаем контекст
            user_context.state = BotState.IDLE
            user_context.current_template = None
            user_context.patient_data = None

        except Exception as e:
            print(f"Ошибка сохранения шаблона: {e}")
            await message.reply_text("❌ Ошибка сохранения шаблона")

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
                # Парсим "ЭЛН: 5 дней" или "ЭЛН: 5"
                eln_text = re.sub(r"^элн\s*:\s*", "", line, flags=re.IGNORECASE).strip()
                # Извлекаем число
                match = re.search(r"(\d+)", eln_text)
                if match:
                    data["sick_leave_days"] = int(match.group(1))

        return data

    def run(self):
        """Запуск бота"""
        print("🤖 Telegram бот запущен!")
        self.application.run_polling()
