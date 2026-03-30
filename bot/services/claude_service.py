import base64
import json
import os
from pathlib import Path
from typing import Dict, List, Optional

from anthropic import Anthropic
from docx import Document

from bot.models.types import PatientData, ClinicMode
from bot.services.memory_service import MemoryService


class ClaudeService:
    """Сервис для работы с Claude AI API"""

    # Актуальные модели Claude (2025)
    OCR_MODEL = "claude-sonnet-4-5-20250929"  # Sonnet 4.5 для OCR документов
    GENERATION_MODEL = "claude-sonnet-4-5-20250929"  # Sonnet 4.5 для генерации и исправления шаблонов

    def __init__(self, api_key: str):
        """
        Инициализация сервиса

        Args:
            api_key: API ключ Anthropic
        """
        self.client = Anthropic(api_key=api_key)
        self.template_cache: Dict[ClinicMode, str] = {}
        self.memory_service = MemoryService()
        self._load_templates()

    def _load_templates(self) -> None:
        """Загрузка готовых шаблонов из файлов (.docx или .txt)"""
        try:
            templates_dir = Path(__file__).parent.parent.parent / "data" / "templates"
            print(f"📁 Директория шаблонов: {templates_dir}")
            print(f"📁 Существует: {templates_dir.exists()}")

            # Загрузка шаблона Династии (приоритет .docx)
            dinastiya_dir = templates_dir / "dinastiya"
            dinastiya_docx = dinastiya_dir / "Артемьева+.docx"
            dinastiya_txt = dinastiya_dir / "template.txt"

            print(f"📄 Проверка Династия: {dinastiya_docx}")
            print(f"📄 Файл существует: {dinastiya_docx.exists()}")

            if dinastiya_docx.exists():
                content = self._read_docx(dinastiya_docx)
                print(f"📝 Прочитано символов: {len(content)}")
                if content:
                    self.template_cache[ClinicMode.DINASTIYA] = content
                    print(f"✓ Загружен шаблон Династии (Артемьева+.docx)")
                else:
                    print(f"⚠️ Файл прочитан, но содержимое пустое")
            elif dinastiya_txt.exists():
                self.template_cache[ClinicMode.DINASTIYA] = dinastiya_txt.read_text(encoding="utf-8")
                print(f"✓ Загружен шаблон Династии (template.txt)")
            else:
                print(f"❌ Шаблон Династии не найден")

            # Загрузка шаблона ПСКП (приоритет .docx)
            pskp_dir = templates_dir / "pskp"
            pskp_docx = pskp_dir / "Митина Н.А.docx"
            pskp_txt = pskp_dir / "template.txt"

            print(f"📄 Проверка ПСКП: {pskp_docx}")
            print(f"📄 Файл существует: {pskp_docx.exists()}")

            if pskp_docx.exists():
                content = self._read_docx(pskp_docx)
                print(f"📝 Прочитано символов: {len(content)}")
                if content:
                    self.template_cache[ClinicMode.PSKP] = content
                    print(f"✓ Загружен шаблон ПСКП (Митина Н.А.docx)")
                else:
                    print(f"⚠️ Файл прочитан, но содержимое пустое")
            elif pskp_txt.exists():
                self.template_cache[ClinicMode.PSKP] = pskp_txt.read_text(encoding="utf-8")
                print(f"✓ Загружен шаблон ПСКП (template.txt)")
            else:
                print(f"❌ Шаблон ПСКП не найден")

            print(f"✓ Шаблоны успешно загружены ({len(self.template_cache)})")
        except Exception as e:
            print(f"Ошибка загрузки шаблонов: {e}")
            import traceback
            traceback.print_exc()

    def _read_docx(self, path: Path) -> str:
        """
        Чтение текста из .docx файла

        Args:
            path: Путь к .docx файлу

        Returns:
            Текстовое содержимое документа
        """
        try:
            # Конвертируем Path в строку для совместимости с Windows
            doc = Document(str(path))
            # Извлекаем весь текст из параграфов
            full_text = []
            for paragraph in doc.paragraphs:
                full_text.append(paragraph.text)
            return '\n'.join(full_text)
        except Exception as e:
            print(f"Ошибка чтения {path}: {e}")
            return ""

    def _get_base_template(self, clinic: ClinicMode) -> str:
        """
        Получение готового шаблона для клиники

        Args:
            clinic: Режим клиники

        Returns:
            Шаблон осмотра
        """
        return self.template_cache.get(clinic, "")

    async def extract_data_from_image(self, image_base64: str, media_type: str = "image/jpeg") -> Dict[str, str]:
        """
        Распознавание текста с фото документа (OCR)
        Использует Claude Sonnet 4.5 для анализа изображений

        Args:
            image_base64: Изображение в base64
            media_type: Тип медиа (image/jpeg, image/png и т.д.)

        Returns:
            Словарь с распознанными данными
        """
        try:
            message = self.client.messages.create(
                model=self.OCR_MODEL,
                max_tokens=1024,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": image_base64,
                                },
                            },
                            {
                                "type": "text",
                                "text": """Пожалуйста, извлеките из этого документа следующую информацию:
- ФИО (полное имя)
- Дата рождения (в формате ДД.ММ.ГГГГ)
- СНИЛС (если есть)

Ответьте в формате JSON:
{
  "fullName": "...",
  "birthDate": "...",
  "snils": "..."
}

Если какого-то поля нет, оставьте его пустым.""",
                            },
                        ],
                    }
                ],
            )

            response_text = message.content[0].text
            # Извлекаем JSON из ответа
            json_match = response_text[response_text.find("{"):response_text.rfind("}") + 1]
            if json_match:
                data = json.loads(json_match)
                return {
                    "full_name": data.get("fullName", ""),
                    "birth_date": data.get("birthDate", ""),
                    "snils": data.get("snils", ""),
                }

            return {}
        except Exception as e:
            print(f"Ошибка распознавания изображения: {e}")
            raise

    async def extract_medical_document_data(self, image_base64: str, media_type: str = "image/jpeg") -> Dict[str, str]:
        """
        Распознавание медицинских документов (выписки, анализы, осмотры)
        Использует Claude Sonnet 4.5 для извлечения медицинской информации

        Args:
            image_base64: Изображение в base64
            media_type: Тип медиа (image/jpeg, image/png и т.д.)

        Returns:
            Словарь с распознанными медицинскими данными
        """
        try:
            message = self.client.messages.create(
                model=self.OCR_MODEL,
                max_tokens=2048,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": image_base64,
                                },
                            },
                            {
                                "type": "text",
                                "text": """Проанализируйте этот медицинский документ и извлеките всю доступную информацию.

Это может быть:
- Выписка из больницы
- Результаты анализов
- Медицинский осмотр
- Направление
- Другой медицинский документ

Извлеките следующую информацию (если присутствует):
- ФИО пациента
- Дата рождения
- СНИЛС
- Дата осмотра/выписки
- Диагноз (основной и сопутствующие)
- Жалобы пациента
- Анамнез заболевания
- Объективные данные (АД, пульс, температура и т.д.)
- Результаты обследований/анализов
- Рекомендации/назначения
- Место работы
- Должность
- Любая другая важная медицинская информация

Ответьте в формате JSON:
{
  "documentType": "тип документа (выписка/анализы/осмотр/направление/другое)",
  "fullName": "ФИО",
  "birthDate": "дата рождения в формате ДД.ММ.ГГГГ",
  "snils": "СНИЛС",
  "examinationDate": "дата осмотра/выписки в формате ДД.ММ.ГГГГ",
  "diagnosis": "диагноз",
  "complaints": "жалобы",
  "anamnesis": "анамнез заболевания",
  "objective": "объективные данные",
  "examinations": "результаты обследований",
  "recommendations": "рекомендации",
  "workplace": "место работы",
  "position": "должность",
  "additionalInfo": "дополнительная информация"
}

Если какого-то поля нет в документе, оставьте его пустым ("").""",
                            },
                        ],
                    }
                ],
            )

            response_text = message.content[0].text
            # Извлекаем JSON из ответа
            json_match = response_text[response_text.find("{"):response_text.rfind("}") + 1]
            if json_match:
                data = json.loads(json_match)
                return {
                    "document_type": data.get("documentType", ""),
                    "full_name": data.get("fullName", ""),
                    "birth_date": data.get("birthDate", ""),
                    "snils": data.get("snils", ""),
                    "examination_date": data.get("examinationDate", ""),
                    "diagnosis": data.get("diagnosis", ""),
                    "complaints": data.get("complaints", ""),
                    "anamnesis": data.get("anamnesis", ""),
                    "objective": data.get("objective", ""),
                    "examinations": data.get("examinations", ""),
                    "recommendations": data.get("recommendations", ""),
                    "workplace": data.get("workplace", ""),
                    "position": data.get("position", ""),
                    "additional_info": data.get("additionalInfo", ""),
                }

            return {}
        except Exception as e:
            print(f"Ошибка распознавания медицинского документа: {e}")
            raise

    async def select_best_template(
        self,
        patient_data: PatientData,
        clinic: ClinicMode,
        archive_templates: List[str],
    ) -> Optional[str]:
        """
        Интеллектуальный выбор наиболее подходящего шаблона из архива
        Использует Claude Sonnet 4.5 + память последних 100 запросов для анализа и выбора

        Args:
            patient_data: Данные пациента
            clinic: Режим клиники
            archive_templates: Список шаблонов из архива

        Returns:
            Выбранный шаблон или None если нет подходящих
        """
        if not archive_templates:
            return None

        try:
            snils_line = f"- СНИЛС: {patient_data.snils}\n" if patient_data.snils else ""

            # Получаем похожие успешные случаи из памяти
            similar_cases = await self.memory_service.get_similar_cases(
                clinic=clinic,
                diagnosis=patient_data.diagnosis,
                limit=5
            )

            # Формируем контекст из памяти
            memory_context = ""
            if similar_cases:
                memory_context = "\n\nИСТОРИЯ ПОХОЖИХ УСПЕШНЫХ СЛУЧАЕВ:\n"
                for i, case in enumerate(similar_cases, 1):
                    corrections_info = f" (правок: {case['corrections_count']})" if case.get('had_corrections') else " (без правок)"
                    memory_context += f"{i}. Диагноз: {case['diagnosis']}{corrections_info}\n"

            # Формируем список шаблонов с нумерацией
            templates_list = "\n\n".join(
                [f"ШАБЛОН #{i+1}:\n{template[:1000]}..." for i, template in enumerate(archive_templates)]
            )

            prompt = f"""Вы медицинский эксперт с доступом к базе знаний. Ваша задача - выбрать НАИБОЛЕЕ ПОДХОДЯЩИЙ шаблон осмотра.

ДАННЫЕ ТЕКУЩЕГО ПАЦИЕНТА:
- ФИО: {patient_data.full_name}
- Дата рождения: {patient_data.birth_date}
{snils_line}- Диагноз: {patient_data.diagnosis}
- Клиника: {clinic.value}
{memory_context}

ДОСТУПНЫЕ ШАБЛОНЫ ИЗ АРХИВА:
{templates_list}

ИНСТРУКЦИИ:
1. Внимательно проанализируйте диагноз пациента: {patient_data.diagnosis}
2. Используйте историю похожих случаев для понимания паттернов
3. Сравните текущий диагноз с каждым шаблоном из архива
4. Выберите шаблон, который НАИЛУЧШЕ подходит для данного диагноза и случая
5. Учитывайте схожесть диагнозов, структуру обследований и назначений
6. Ответьте ТОЛЬКО номером выбранного шаблона (например: "1" или "2" или "3")

Номер наиболее подходящего шаблона:"""

            message = self.client.messages.create(
                model=self.GENERATION_MODEL,
                max_tokens=10,
                messages=[{"role": "user", "content": prompt}],
            )

            # Извлекаем номер из ответа
            response = message.content[0].text.strip()
            try:
                template_index = int(response) - 1
                if 0 <= template_index < len(archive_templates):
                    print(f"✓ AI выбрал шаблон #{template_index + 1} как наиболее подходящий")
                    return archive_templates[template_index]
            except ValueError:
                print(f"Ошибка парсинга выбора AI: {response}")

            return None
        except Exception as e:
            print(f"Ошибка выбора шаблона: {e}")
            return None

    def _sanitize(self, value: str, max_len: int = 300) -> str:
        """Удаляет управляющие символы и обрезает строку до max_len перед вставкой в промпт."""
        if not value:
            return value
        # Убираем управляющие символы кроме обычного пробела и переноса строки
        cleaned = "".join(ch for ch in value if ch == "\n" or (" " <= ch < "\x7f"))
        return cleaned[:max_len]

    def _get_workplace_instruction(self, patient_data: PatientData) -> str:
        """Возвращает инструкцию для AI по полю 'Место работы / Место учёбы'."""
        workplace = (patient_data.workplace or "").strip() or None
        if patient_data.is_student and workplace:
            return (
                f"КРИТИЧЕСКИ ВАЖНО (СТУДЕНТ): замените поле 'Место работы' на "
                f"'Место учёбы: {workplace}'. НЕ оставляйте 'Место работы' в документе!"
            )
        if patient_data.is_student:
            return (
                "КРИТИЧЕСКИ ВАЖНО (СТУДЕНТ): замените поле 'Место работы' на "
                "'Место учёбы: -'. НЕ оставляйте 'Место работы' в документе!"
            )
        if workplace:
            return f"КРИТИЧЕСКИ ВАЖНО: в поле 'Место работы' поставьте ТОЧНО: {workplace}. Не берите из шаблона!"
        return "КРИТИЧЕСКИ ВАЖНО: место работы НЕ указано — поставьте прочерк: -. НЕ КОПИРУЙТЕ из шаблона!"

    def _get_position_instruction(self, patient_data: PatientData) -> str:
        """Возвращает инструкцию для AI по полю 'Должность / Курс'."""
        position = (patient_data.position or "").strip() or None
        if patient_data.is_student and position:
            return (
                f"КРИТИЧЕСКИ ВАЖНО (СТУДЕНТ): замените поле 'Должность' на "
                f"'Курс: {position}'. НЕ оставляйте 'Должность' в документе!"
            )
        if patient_data.is_student:
            return (
                "КРИТИЧЕСКИ ВАЖНО (СТУДЕНТ): замените поле 'Должность' на "
                "'Курс: -'. НЕ оставляйте 'Должность' в документе!"
            )
        if position:
            return f"КРИТИЧЕСКИ ВАЖНО: в поле 'Должность' поставьте ТОЧНО: {position}. Не берите из шаблона!"
        return "КРИТИЧЕСКИ ВАЖНО: должность НЕ указана — поставьте прочерк: -. НЕ КОПИРУЙТЕ из шаблона!"

    def _get_eln_instructions(self, patient_data: PatientData) -> str:
        """
        Генерирует инструкции по ЭЛН в зависимости от того, отказ или нет

        Args:
            patient_data: Данные пациента

        Returns:
            Текст инструкций для Claude AI
        """
        # Студенческая справка (вместо ЭЛН)
        if patient_data.student_certificate:
            cert_text = patient_data.student_certificate
            follow_up = patient_data.student_cert_end_date or "по необходимости"
            workplace_label = "Место учёбы"
            position_label = "Курс"
            workplace_val = patient_data.workplace or "- (прочерк)"
            position_val = patient_data.position or "- (прочерк)"

            return f"""ВЫДАНА СТУДЕНЧЕСКАЯ СПРАВКА (НЕ ЭЛН!):
- Напишите ТОЧНО: "Нетрудоспособна, выдана студенческая справка: {cert_text}"
- Напишите ТОЧНО: "Явка к врачу: {follow_up}"
- НЕ пишите про ЭЛН! Это СТУДЕНЧЕСКАЯ СПРАВКА, а не электронный лист нетрудоспособности!
- НЕ пишите "Режим: домашний" если это не указано в шаблоне

КРИТИЧЕСКИ ВАЖНО - СТРАХОВОЙ АНАМНЕЗ (СТУДЕНТ):
В разделе "Страховой анамнез" ОБЯЗАТЕЛЬНО замените:
  - "Место работы" → "{workplace_label}: {workplace_val}"
  - "Должность" → "{position_label}: {position_val}"
  - Листы нетрудоспособности за последние 11 месяцев: не было.
НЕ КОПИРУЙТЕ место работы и должность из шаблона! Пациент — студент!"""

        if patient_data.eln_refused:
            # Формируем инструкции по страховому анамнезу при отказе от ЭЛН
            if patient_data.workplace:
                workplace_instr = f'  - Место работы: {patient_data.workplace}'
            else:
                workplace_instr = '  - Место работы: - (прочерк)'
            if patient_data.position:
                position_instr = f'  - Должность: {patient_data.position}'
            else:
                position_instr = '  - Должность: - (прочерк)'

            return f"""ПАЦИЕНТ ОТКАЗАЛСЯ ОТ ЭЛН!
- Напишите: "Нетрудоспособен, ЭЛН - отказ"
- Напишите: "Явка к врачу: по необходимости"
- НЕ указывайте конкретные даты ЭЛН и явки к врачу!
- НЕ пишите "Режим: домашний" если это не указано в шаблоне
- НЕ придумывайте даты больничного листа!

КРИТИЧЕСКИ ВАЖНО - СТРАХОВОЙ АНАМНЕЗ ПРИ ОТКАЗЕ ОТ ЭЛН:
В разделе "Страховой анамнез" ОБЯЗАТЕЛЬНО напишите:
{workplace_instr}
{position_instr}
  - Листы нетрудоспособности за последние 11 месяцев: не было.
НЕ КОПИРУЙТЕ место работы и должность из шаблона! Используйте ТОЛЬКО данные выше."""
        else:
            # Проверяем, указаны ли точные даты ЭЛН
            from datetime import datetime, timedelta

            if patient_data.eln_start_date and patient_data.eln_end_date:
                # ИСПОЛЬЗУЕМ ТОЧНЫЕ ДАТЫ ЭЛН ИЗ ПОЛЬЗОВАТЕЛЬСКОГО ВВОДА
                eln_start_date = patient_data.eln_start_date
                eln_end_date = patient_data.eln_end_date
                follow_up_date = eln_end_date

                return f"""КРИТИЧЕСКИ ВАЖНО - ИСПОЛЬЗУЙТЕ ТОЧНЫЕ ДАТЫ ЭЛН:
- Период ЭЛН (электронный лист нетрудоспособности) → ОБЯЗАТЕЛЬНО с {eln_start_date} по {eln_end_date}
- Дата явки к врачу (повторный прием) → ОБЯЗАТЕЛЬНО {follow_up_date}
- Напишите ТОЧНО: "Нетрудоспособен, ЭЛН с {eln_start_date} по {eln_end_date}"
- Напишите ТОЧНО: "Явка к врачу: {follow_up_date}"
- НЕ МЕНЯЙТЕ эти даты! Они указаны пользователем!"""
            else:
                # Период ЭЛН берётся ТОЛЬКО из данных пользователя
                exam_date = patient_data.examination_date or datetime.now().strftime("%d.%m.%Y")
                sick_days = patient_data.sick_leave_days
                if not sick_days:
                    return """- НЕ указывайте период ЭЛН и дату явки — пользователь не указал срок нетрудоспособности!
- НЕ придумывайте и НЕ рассчитывайте даты ЭЛН самостоятельно!
- Оставьте поля ЭЛН и явки к врачу пустыми или с прочерком."""
                exam_dt = datetime.strptime(exam_date, "%d.%m.%Y")
                eln_end_dt = exam_dt + timedelta(days=sick_days - 1)
                eln_end_date = eln_end_dt.strftime("%d.%m.%Y")
                follow_up_date = eln_end_date

                return f"""- Период ЭЛН (электронный лист нетрудоспособности) → с {exam_date} по {eln_end_date}
- Дата явки к врачу (повторный прием) → {follow_up_date}
- Напишите: "Нетрудоспособен, ЭЛН с {exam_date} по {eln_end_date}"
- Напишите: "Явка к врачу: {follow_up_date}" """

    async def generate_examination_template(
        self,
        patient_data: PatientData,
        clinic: ClinicMode,
        archive_template: Optional[str] = None,
    ) -> str:
        """
        Генерация шаблона осмотра на основе базового или архивного шаблона
        Использует Claude Sonnet 4.5 для заполнения шаблона

        Логика выбора шаблона:
        - Если archive_template предоставлен (найден локальным поиском) - используем его
        - Если archive_template = None (не найден в архиве) - используем базовый шаблон

        Args:
            patient_data: Данные пациента
            clinic: Режим клиники
            archive_template: Шаблон из архива (найден локальным поиском БЕЗ AI) или None

        Returns:
            Заполненный шаблон осмотра
        """
        try:
            from datetime import datetime, timedelta

            # Используем шаблон из архива (если найден локальным поиском) или базовый
            if archive_template:
                selected_template = archive_template
                print(f"✓ Использую шаблон из архива (найден локальным поиском)")
            else:
                selected_template = self._get_base_template(clinic)
                print(f"✓ Использую базовый шаблон клиники {clinic.value}")

            if not selected_template:
                raise ValueError(f"Шаблон для клиники {clinic.value} не найден")

            # Подготовка дат
            # Дата осмотра: ВСЕГДА используем ту, что указал пользователь, или текущую если не указана
            exam_date = patient_data.examination_date or datetime.now().strftime("%d.%m.%Y")

            # Дата вызова врача на дом = дата осмотра (когда врач приехал на дом к пациенту)
            call_date = exam_date

            # Дата начала болезни: указанная пользователем или автоматически за 1 день до осмотра
            if patient_data.illness_start_date:
                illness_date = patient_data.illness_start_date
            else:
                exam_dt = datetime.strptime(exam_date, "%d.%m.%Y")
                illness_dt = exam_dt - timedelta(days=1)
                illness_date = illness_dt.strftime("%d.%m.%Y")

            # Рассчитываем период ЭЛН / студенческую справку
            print(f"🔍 DEBUG: eln_refused={patient_data.eln_refused}, sick_leave_days={patient_data.sick_leave_days}, student_cert={patient_data.student_certificate}")

            if patient_data.student_certificate:
                # Студенческая справка (вместо ЭЛН)
                cert_text = patient_data.student_certificate
                follow_up = patient_data.student_cert_end_date or "по необходимости"
                print(f"✓ Студенческая справка: {cert_text}, явка: {follow_up}")
                eln_line = f"- Студенческая справка: {cert_text}\n"
                follow_up_line = f"- Дата явки к врачу: {follow_up}\n"
            elif patient_data.eln_refused:
                # Отказ от ЭЛН
                print(f"✓ Пациент отказался от ЭЛН")
                eln_line = "- ЭЛН: ОТКАЗ (пациент отказался от электронного листа нетрудоспособности)\n"
                follow_up_line = "- Дата явки к врачу: по необходимости\n"
            else:
                # Проверяем, указаны ли точные даты ЭЛН
                if patient_data.eln_start_date and patient_data.eln_end_date:
                    # Используем точные даты ЭЛН из пользовательского ввода
                    eln_start_date = patient_data.eln_start_date
                    eln_end_date = patient_data.eln_end_date

                    # Вычисляем количество дней
                    start_dt = datetime.strptime(eln_start_date, "%d.%m.%Y")
                    end_dt = datetime.strptime(eln_end_date, "%d.%m.%Y")
                    sick_days = (end_dt - start_dt).days + 1

                    follow_up_date = eln_end_date
                    eln_line = f"- Период ЭЛН: с {eln_start_date} по {eln_end_date} ({sick_days} дней)\n"
                    follow_up_line = f"- Дата явки к врачу (повторный прием): {follow_up_date}\n"
                    print(f"✓ Используем точные даты ЭЛН: с {eln_start_date} по {eln_end_date}")
                else:
                    # Период ЭЛН берётся ТОЛЬКО из данных пользователя
                    sick_days = patient_data.sick_leave_days
                    print(f"🔍 DEBUG: sick_days={sick_days}, type={type(sick_days)}")

                    if not sick_days or not isinstance(sick_days, int) or sick_days <= 0:
                        print(f"ℹ️ Срок ЭЛН не указан пользователем — не рассчитываем даты")
                        eln_line = ""
                        follow_up_line = ""
                    else:
                        exam_dt = datetime.strptime(exam_date, "%d.%m.%Y")
                        eln_end_dt = exam_dt + timedelta(days=sick_days - 1)
                        eln_end_date = eln_end_dt.strftime("%d.%m.%Y")
                        follow_up_date = eln_end_date
                        eln_line = f"- Период ЭЛН: с {exam_date} по {eln_end_date} ({sick_days} дней)\n"
                        follow_up_line = f"- Дата явки к врачу (повторный прием): {follow_up_date}\n"

            snils_line = f"- СНИЛС: {patient_data.snils}\n" if patient_data.snils else ""
            # Для студентов используем "Место учёбы" / "Курс" вместо "Место работы" / "Должность"
            if patient_data.is_student:
                workplace_line = f"- Место учёбы: {patient_data.workplace}\n" if patient_data.workplace else ""
                position_line = f"- Курс: {patient_data.position}\n" if patient_data.position else ""
            else:
                workplace_line = f"- Место работы: {patient_data.workplace}\n" if patient_data.workplace else ""
                position_line = f"- Должность: {patient_data.position}\n" if patient_data.position else ""
            illness_line = f"- Дата начала болезни (когда пациент заболел): {illness_date}\n"
            call_line = f"- Дата вызова врача на дом: {call_date}\n"
            exam_date_line = f"- Дата осмотра/консультации: {exam_date}\n"

            s = self._sanitize
            prompt = f"""Вы медицинский ассистент. Ваша задача - заполнить готовый шаблон медицинского осмотра.

ДАННЫЕ ПАЦИЕНТА:
- ФИО: {s(patient_data.full_name)}
- Дата рождения: {s(patient_data.birth_date)}
{snils_line}{workplace_line}{position_line}- Диагноз: {s(patient_data.diagnosis)}

ВАЖНЫЕ ДАТЫ:
{illness_line}{call_line}{exam_date_line}{eln_line}{follow_up_line}

ГОТОВЫЙ ШАБЛОН ДЛЯ ЗАПОЛНЕНИЯ:
{selected_template}

КРИТИЧЕСКИ ВАЖНЫЕ ИНСТРУКЦИИ ПО ДАТАМ:
1. ОБЯЗАТЕЛЬНО замените ВСЕ старые даты в шаблоне на актуальные!
2. Дата консультации/осмотра (ДАТА, Дата консультации) → {exam_date}
3. В анамнезе заболевания "пациент считает себя больным с..." → {illness_date}
4. В анамнезе заболевания дата вызова врача на дом → {call_date}
5. НЕ оставляйте старые даты из шаблона (типа 10.12.2024 или 15.12.2025)!

КРИТИЧЕСКИ ВАЖНО ПРО ЭЛН И ЯВКУ:
{self._get_eln_instructions(patient_data)}

ИНСТРУКЦИИ ПО СТРУКТУРЕ И ОФОРМЛЕНИЮ:
1. СОХРАНИТЕ структуру всех разделов (Анамнез заболевания, Жалобы, Объективно и т.д.)
2. Заполните все разделы шаблона реалистичными медицинскими данными
3. Вставьте данные пациента (ФИО, дата рождения, СНИЛС{", место учёбы, курс" if patient_data.is_student else (", место работы, должность" if patient_data.workplace or patient_data.position else "")}) в соответствующие места
4. {self._get_workplace_instruction(patient_data)}
5. {self._get_position_instruction(patient_data)}
6. Заполните разделы с учетом указанного диагноза: {patient_data.diagnosis}
7. НЕ добавляйте лишний текст до или после шаблона
8. Верните ТОЛЬКО заполненный шаблон

КРИТИЧЕСКИ ВАЖНО - НЕ ВКЛЮЧАЙТЕ В ОТВЕТ:
1. НЕ включайте шапку документа (логотип, контакты клиники)
2. НЕ включайте главный заголовок ("Осмотр терапевта на дому" или "КОНСУЛЬТАЦИЯ ТЕРАПЕВТА НА ДОМУ" или любой другой)
3. НЕ дублируйте заголовки, которые уже есть в шаблоне
4. Начинайте ответ СРАЗУ с содержимого осмотра (Дата консультации, ФИО пациента и т.д.)
5. Шапка и заголовок уже есть в документе - вы заполняете только СОДЕРЖИМОЕ!

КРИТИЧЕСКИ ВАЖНО - ИНДИВИДУАЛЬНЫЕ ПОКАЗАТЕЛИ ЗДОРОВЬЯ:
НЕ КОПИРУЙТЕ значения из шаблона! Генерируйте реалистичные индивидуальные показатели для КАЖДОГО пациента:

1. **Артериальное давление (АД):**
   - Норма: 110-130 / 70-85 мм.рт.ст
   - При ОРВИ, простуде: немного снижено или норма (105-125 / 65-80)
   - При температуре выше 38°C: может быть повышено (125-140 / 80-90)
   - Варьируйте значения, НЕ используйте одни и те же цифры!

2. **ЧСС (частота сердечных сокращений):**
   - Норма: 60-80 ударов в минуту
   - При лихорадке, ОРВИ: учащённый пульс (85-102 удара)
   - При нормальной температуре: норма (62-78 ударов)
   - Генерируйте разные значения для разных пациентов!

3. **Температура тела:**
   - ОБЯЗАТЕЛЬНО соответствует диагнозу!
   - ОРВИ с лихорадкой: 37.5-38.5°C
   - Лёгкая простуда: 37.0-37.5°C
   - Без лихорадки: 36.5-36.8°C
   - НЕ копируйте из шаблона, адаптируйте под диагноз!

4. **ЧДД (частота дыхательных движений):**
   - Норма: 14-18 в минуту
   - При бронхите, кашле: может быть учащено (18-22 в минуту)
   - Варьируйте в разумных пределах!

ПРАВИЛА ФОРМАТИРОВАНИЯ ТЕКСТА:
1. Используйте **двойные звёздочки** для ЖИРНОГО текста (например: **Жалобы:**, **Диагноз:**, **Анамнез заболевания:**)
2. Обычный текст пишите БЕЗ звёздочек
3. Для нумерованных списков используйте формат: "1. Текст", "2. Текст" и т.д.
4. Заголовки разделов (Жалобы, Анамнез, Объективно, Диагноз и т.д.) ВСЕГДА делайте жирными: **Заголовок:**
5. **КРИТИЧЕСКИ ВАЖНО:** "План лечения" и "План обследования" ВСЕГДА должны быть нумерованными списками! Каждый пункт начинается с номера "1.", "2.", "3." и т.д.

ПРИМЕРЫ ПРАВИЛЬНОГО ФОРМАТИРОВАНИЯ:
**Дата консультации:** 10.12.2024
**ФИО пациента:** Иванов Иван Иванович
**Жалобы:** сухой приступообразный эпизодический кашель

**План лечения:**
1. Обильное тёплое питьё (не менее 30 мл на 1 кг массы тела)
2. Цетрин по 1 таб 1 раз в день - 1 нед, внутрь
3. Тизин (спрей) интраназально 2-3 раза в день при сильной заложенности носа не более 1 нед.

**План обследования (консультации специалистов, ЭКТ, УЗИ, ФГ, ОАМ, ОАК, глюкоза крови, биохимический анализ крови):** КАК+ЛФ+СО2, СРБ

НЕПРАВИЛЬНО (без нумерации):
**План лечения:**
Обильное тёплое питьё
Цетрин по 1 таб

Заполните шаблон:"""

            try:
                message = self.client.messages.create(
                    model=self.GENERATION_MODEL,
                    max_tokens=4096,  # Уменьшено для совместимости
                    messages=[{"role": "user", "content": prompt}],
                )
                return message.content[0].text
            except Exception as api_error:
                print(f"Ошибка API Claude: {api_error}")
                # Попытка с меньшим max_tokens
                print("Повторная попытка с max_tokens=2048...")
                message = self.client.messages.create(
                    model=self.GENERATION_MODEL,
                    max_tokens=2048,
                    messages=[{"role": "user", "content": prompt}],
                )
                return message.content[0].text
        except Exception as e:
            print(f"Ошибка генерации шаблона: {e}")
            import traceback
            traceback.print_exc()
            raise

    async def correct_template(
        self,
        current_template: str,
        patient_data: PatientData,
        corrections: str,
        clinic: ClinicMode = ClinicMode.DINASTIYA,
    ) -> str:
        """
        Исправление шаблона на основе комментариев пользователя
        Автоматически сохраняет исправления в память для обучения

        Args:
            current_template: Текущий шаблон
            patient_data: Данные пациента
            corrections: Комментарии для исправления
            clinic: Клиника (для сохранения в память)

        Returns:
            Исправленный шаблон
        """
        try:
            from datetime import datetime, timedelta

            # Подготовка дат
            # Дата осмотра: используем указанную пользователем или текущую
            exam_date = patient_data.examination_date or datetime.now().strftime("%d.%m.%Y")

            # Дата вызова врача на дом = дата осмотра
            call_date = exam_date

            # Дата начала болезни: указанная пользователем или за 1 день до осмотра
            if patient_data.illness_start_date:
                illness_date = patient_data.illness_start_date
            else:
                exam_dt = datetime.strptime(exam_date, "%d.%m.%Y")
                illness_dt = exam_dt - timedelta(days=1)
                illness_date = illness_dt.strftime("%d.%m.%Y")

            # Рассчитываем период ЭЛН / студенческую справку
            if patient_data.student_certificate:
                # Студенческая справка (вместо ЭЛН)
                cert_text = patient_data.student_certificate
                follow_up = patient_data.student_cert_end_date or "по необходимости"
                eln_line = f"- Студенческая справка: {cert_text}\n"
                follow_up_line = f"- Дата явки к врачу: {follow_up}\n"
            elif patient_data.eln_refused:
                # Отказ от ЭЛН
                eln_line = "- ЭЛН: ОТКАЗ (пациент отказался от электронного листа нетрудоспособности)\n"
                follow_up_line = "- Дата явки к врачу: по необходимости\n"
            else:
                # Проверяем, указаны ли точные даты ЭЛН
                if patient_data.eln_start_date and patient_data.eln_end_date:
                    # Используем точные даты ЭЛН из пользовательского ввода
                    eln_start_date = patient_data.eln_start_date
                    eln_end_date = patient_data.eln_end_date

                    # Вычисляем количество дней
                    start_dt = datetime.strptime(eln_start_date, "%d.%m.%Y")
                    end_dt = datetime.strptime(eln_end_date, "%d.%m.%Y")
                    sick_days = (end_dt - start_dt).days + 1

                    follow_up_date = eln_end_date
                    eln_line = f"- Период ЭЛН: с {eln_start_date} по {eln_end_date} ({sick_days} дней)\n"
                    follow_up_line = f"- Дата явки к врачу (повторный прием): {follow_up_date}\n"
                    print(f"✓ Используем точные даты ЭЛН при исправлении: с {eln_start_date} по {eln_end_date}")
                else:
                    # Период ЭЛН берётся ТОЛЬКО из данных пользователя
                    sick_days = patient_data.sick_leave_days
                    if not sick_days or not isinstance(sick_days, int) or sick_days <= 0:
                        print(f"ℹ️ Срок ЭЛН не указан пользователем — не рассчитываем даты")
                        eln_line = ""
                        follow_up_line = ""
                    else:
                        exam_dt = datetime.strptime(exam_date, "%d.%m.%Y")
                        eln_end_dt = exam_dt + timedelta(days=sick_days - 1)
                        eln_end_date = eln_end_dt.strftime("%d.%m.%Y")
                        follow_up_date = eln_end_date
                        eln_line = f"- Период ЭЛН: с {exam_date} по {eln_end_date} ({sick_days} дней)\n"
                        follow_up_line = f"- Дата явки к врачу: {follow_up_date}\n"

            snils_line = f"- СНИЛС: {patient_data.snils}\n" if patient_data.snils else ""
            # Для студентов используем "Место учёбы" / "Курс"
            if patient_data.is_student:
                workplace_line = f"- Место учёбы: {patient_data.workplace}\n" if patient_data.workplace else ""
                position_line = f"- Курс: {patient_data.position}\n" if patient_data.position else ""
            else:
                workplace_line = f"- Место работы: {patient_data.workplace}\n" if patient_data.workplace else ""
                position_line = f"- Должность: {patient_data.position}\n" if patient_data.position else ""
            illness_line = f"- Дата начала болезни (когда пациент заболел): {illness_date}\n"
            call_line = f"- Дата вызова врача на дом: {call_date}\n"
            exam_date_line = f"- Дата осмотра/консультации: {exam_date}\n"

            s = self._sanitize

            # Правила сохранения — в system-промпт (фон, не конкурируют с задачей)
            eln_dates_note = ""
            if eln_line.strip():
                eln_dates_note = f"\nДаты ЭЛН (не менять): {eln_line.strip()}"
            if follow_up_line.strip():
                eln_dates_note += f"\nЯвка к врачу (не менять): {follow_up_line.strip()}"

            system_prompt = f"""Вы медицинский ассистент, который вносит точечные правки в готовый шаблон осмотра.

АДМИНИСТРАТИВНЫЕ ДАННЫЕ — не менять без явной команды:
- ФИО пациента: {s(patient_data.full_name)}
- Дата рождения: {s(patient_data.birth_date)}
- Основной диагноз (МКБ-код): {s(patient_data.diagnosis)}
- Дата осмотра: {exam_date}
- Дата начала болезни: {illness_date}
- Дата вызова врача: {call_date}{eln_dates_note}
- {self._get_workplace_instruction(patient_data)}
- {self._get_position_instruction(patient_data)}

МЕДИЦИНСКИЙ КОНТЕНТ — МОЖНО и НУЖНО менять по запросу врача:
- Сопутствующие заболевания и перенесённые болезни
- Анамнез жизни (аллергии, наследственность, вредные привычки)
- Жалобы и анамнез заболевания
- Объективный статус и показатели
- Назначения, рекомендации, план лечения
- Любые другие медицинские детали

ПРАВИЛА ОТВЕТА:
- Верните ТОЛЬКО изменённое содержимое осмотра — без шапки, без заголовка клиники
- Форматирование: **жирный** = двойные звёздочки, заголовки разделов всегда жирные
- Нумерованные списки: "1. Текст", "2. Текст"
- "План лечения" и "План обследования" — всегда нумерованные списки"""

            # Задача пользователя — в отдельном user-сообщении
            user_message = f"""ЗАДАЧА: внеси следующие правки в шаблон осмотра.

ПРАВКИ:
{s(corrections, max_len=2000)}

Применить ВСЕ правки без исключений. Не возвращай документ без изменений.

ШАБЛОН ДЛЯ ИЗМЕНЕНИЯ:
{current_template}"""

            message = self.client.messages.create(
                model=self.GENERATION_MODEL,
                max_tokens=8192,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )

            corrected_template = message.content[0].text

            # Если AI вернул идентичный текст — логируем предупреждение
            if corrected_template.strip() == current_template.strip():
                print(f"⚠️ ПРЕДУПРЕЖДЕНИЕ: AI вернул шаблон без изменений!")

            return corrected_template
        except Exception as e:
            print(f"Ошибка исправления шаблона: {e}")
            raise
