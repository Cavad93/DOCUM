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
    OCR_MODEL = "claude-sonnet-4-5-20250929"  # Последняя и наиболее продвинутая модель Sonnet
    GENERATION_MODEL = "claude-opus-4-20250514"  # Opus 4 для генерации шаблонов

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

            # Загрузка шаблона Династии (приоритет .docx)
            dinastiya_dir = templates_dir / "dinastiya"
            dinastiya_docx = dinastiya_dir / "Артемьева+.docx"
            dinastiya_txt = dinastiya_dir / "template.txt"

            if dinastiya_docx.exists():
                self.template_cache[ClinicMode.DINASTIYA] = self._read_docx(dinastiya_docx)
                print(f"✓ Загружен шаблон Династии (Артемьева+.docx)")
            elif dinastiya_txt.exists():
                self.template_cache[ClinicMode.DINASTIYA] = dinastiya_txt.read_text(encoding="utf-8")
                print(f"✓ Загружен шаблон Династии (template.txt)")

            # Загрузка шаблона ПСКП (приоритет .docx)
            pskp_dir = templates_dir / "pskp"
            pskp_docx = pskp_dir / "Митина Н.А.docx"
            pskp_txt = pskp_dir / "template.txt"

            if pskp_docx.exists():
                self.template_cache[ClinicMode.PSKP] = self._read_docx(pskp_docx)
                print(f"✓ Загружен шаблон ПСКП (Митина Н.А.docx)")
            elif pskp_txt.exists():
                self.template_cache[ClinicMode.PSKP] = pskp_txt.read_text(encoding="utf-8")
                print(f"✓ Загружен шаблон ПСКП (template.txt)")

            print(f"✓ Шаблоны успешно загружены ({len(self.template_cache)})")
        except Exception as e:
            print(f"Ошибка загрузки шаблонов: {e}")

    def _read_docx(self, path: Path) -> str:
        """
        Чтение текста из .docx файла

        Args:
            path: Путь к .docx файлу

        Returns:
            Текстовое содержимое документа
        """
        try:
            doc = Document(path)
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

    async def select_best_template(
        self,
        patient_data: PatientData,
        clinic: ClinicMode,
        archive_templates: List[str],
    ) -> Optional[str]:
        """
        Интеллектуальный выбор наиболее подходящего шаблона из архива
        Использует Claude Opus 4 + память последних 100 запросов для анализа и выбора

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

    async def generate_examination_template(
        self,
        patient_data: PatientData,
        clinic: ClinicMode,
        archive_templates: Optional[List[str]] = None,
    ) -> str:
        """
        Генерация шаблона осмотра на основе готового шаблона
        Использует Claude Opus 4 для выбора подходящего шаблона из архива и его заполнения

        Args:
            patient_data: Данные пациента
            clinic: Режим клиники
            archive_templates: Похожие шаблоны из архива

        Returns:
            Заполненный шаблон осмотра
        """
        try:
            from datetime import datetime, timedelta

            # Пытаемся выбрать лучший шаблон из архива
            selected_template = None
            if archive_templates and len(archive_templates) > 0:
                selected_template = await self.select_best_template(
                    patient_data, clinic, archive_templates
                )

            # Если не удалось выбрать из архива, используем базовый шаблон
            if not selected_template:
                selected_template = self._get_base_template(clinic)
                print(f"✓ Использую базовый шаблон клиники {clinic.value}")
            else:
                print(f"✓ Использую выбранный шаблон из архива")

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

            # Рассчитываем период ЭЛН
            sick_days = patient_data.sick_leave_days or 3  # По умолчанию 3 дня
            exam_dt = datetime.strptime(exam_date, "%d.%m.%Y")
            eln_end_dt = exam_dt + timedelta(days=sick_days - 1)
            eln_end_date = eln_end_dt.strftime("%d.%m.%Y")
            follow_up_date = eln_end_date  # Дата повторного визита - последний день ЭЛН

            snils_line = f"- СНИЛС: {patient_data.snils}\n" if patient_data.snils else ""
            illness_line = f"- Дата начала болезни (когда пациент заболел): {illness_date}\n"
            call_line = f"- Дата вызова врача на дом: {call_date}\n"
            exam_date_line = f"- Дата осмотра/консультации: {exam_date}\n"
            eln_line = f"- Период ЭЛН: с {exam_date} по {eln_end_date} ({sick_days} дней)\n"
            follow_up_line = f"- Дата явки к врачу (повторный прием): {follow_up_date}\n"

            prompt = f"""Вы медицинский ассистент. Ваша задача - заполнить готовый шаблон медицинского осмотра.

ДАННЫЕ ПАЦИЕНТА:
- ФИО: {patient_data.full_name}
- Дата рождения: {patient_data.birth_date}
{snils_line}- Диагноз: {patient_data.diagnosis}

ВАЖНЫЕ ДАТЫ:
{illness_line}{call_line}{exam_date_line}{eln_line}{follow_up_line}

ГОТОВЫЙ ШАБЛОН ДЛЯ ЗАПОЛНЕНИЯ:
{selected_template}

КРИТИЧЕСКИ ВАЖНЫЕ ИНСТРУКЦИИ ПО ДАТАМ:
1. ОБЯЗАТЕЛЬНО замените ВСЕ старые даты в шаблоне на актуальные!
2. Дата консультации/осмотра (ДАТА, Дата консультации) → {exam_date}
3. В анамнезе заболевания "пациент считает себя больным с..." → {illness_date}
4. В анамнезе заболевания дата вызова врача на дом → {call_date}
5. Период ЭЛН (электронный лист нетрудоспособности) → с {exam_date} по {eln_end_date}
6. Дата явки к врачу (повторный прием) → {follow_up_date}
7. НЕ оставляйте старые даты из шаблона (типа 10.12.2024 или 15.12.2025)!

ИНСТРУКЦИИ ПО СТРУКТУРЕ И ОФОРМЛЕНИЮ:
1. СОХРАНИТЕ структуру всех разделов (Анамнез заболевания, Жалобы, Объективно и т.д.)
2. Заполните все разделы шаблона реалистичными медицинскими данными
3. Вставьте данные пациента (ФИО, дата рождения, СНИЛС) в соответствующие места
4. Заполните разделы с учетом указанного диагноза: {patient_data.diagnosis}
5. НЕ добавляйте лишний текст до или после шаблона
6. Верните ТОЛЬКО заполненный шаблон

КРИТИЧЕСКИ ВАЖНО - НЕ ВКЛЮЧАЙТЕ В ОТВЕТ:
1. НЕ включайте шапку документа (логотип, контакты клиники)
2. НЕ включайте главный заголовок "Осмотр терапевта на дому"
3. Начинайте ответ СРАЗУ с содержимого осмотра (Дата консультации, ФИО пациента и т.д.)
4. Шапка и заголовок уже есть в документе - вы заполняете только СОДЕРЖИМОЕ!

ПРАВИЛА ФОРМАТИРОВАНИЯ ТЕКСТА:
1. Используйте **двойные звёздочки** для ЖИРНОГО текста (например: **Жалобы:**, **Диагноз:**, **Анамнез заболевания:**)
2. Обычный текст пишите БЕЗ звёздочек
3. Для нумерованных списков используйте формат: "1. Текст", "2. Текст" и т.д.
4. Заголовки разделов (Жалобы, Анамнез, Объективно, Диагноз и т.д.) ВСЕГДА делайте жирными: **Заголовок:**

ПРИМЕРЫ ПРАВИЛЬНОГО ФОРМАТИРОВАНИЯ:
**Дата консультации:** 10.12.2024
**ФИО пациента:** Иванов Иван Иванович
**Жалобы:** сухой приступообразный эпизодический кашель

**План лечения:**
1. Обильное тёплое питьё (не менее 30 мл на 1 кг массы тела)
2. Цетрин по 1 таб 1 раз в день - 1 нед, внутрь

Заполните шаблон:"""

            message = self.client.messages.create(
                model=self.GENERATION_MODEL,
                max_tokens=8192,
                messages=[{"role": "user", "content": prompt}],
            )

            return message.content[0].text
        except Exception as e:
            print(f"Ошибка генерации шаблона: {e}")
            raise

    async def correct_template(
        self,
        current_template: str,
        patient_data: PatientData,
        corrections: str,
    ) -> str:
        """
        Исправление шаблона на основе комментариев пользователя

        Args:
            current_template: Текущий шаблон
            patient_data: Данные пациента
            corrections: Комментарии для исправления

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

            # Период ЭЛН
            sick_days = patient_data.sick_leave_days or 3
            exam_dt = datetime.strptime(exam_date, "%d.%m.%Y")
            eln_end_dt = exam_dt + timedelta(days=sick_days - 1)
            eln_end_date = eln_end_dt.strftime("%d.%m.%Y")
            follow_up_date = eln_end_date

            snils_line = f"- СНИЛС: {patient_data.snils}\n" if patient_data.snils else ""
            illness_line = f"- Дата начала болезни (когда пациент заболел): {illness_date}\n"
            call_line = f"- Дата вызова врача на дом: {call_date}\n"
            exam_date_line = f"- Дата осмотра/консультации: {exam_date}\n"
            eln_line = f"- Период ЭЛН: с {exam_date} по {eln_end_date} ({sick_days} дней)\n"
            follow_up_line = f"- Дата явки к врачу: {follow_up_date}\n"

            prompt = f"""Вы медицинский ассистент. У вас есть готовый шаблон медицинского осмотра, который нужно исправить.

ТЕКУЩИЙ ШАБЛОН:
{current_template}

ДАННЫЕ ПАЦИЕНТА:
- ФИО: {patient_data.full_name}
- Дата рождения: {patient_data.birth_date}
{snils_line}- Диагноз: {patient_data.diagnosis}

ВАЖНЫЕ ДАТЫ:
{illness_line}{call_line}{exam_date_line}{eln_line}{follow_up_line}

КОММЕНТАРИИ ДЛЯ ИСПРАВЛЕНИЯ:
{corrections}

ИНСТРУКЦИИ:
1. Внимательно прочитайте комментарии пользователя
2. Внесите необходимые исправления в шаблон
3. СОХРАНИТЕ актуальные даты:
   - Дата осмотра/консультации: {exam_date}
   - Начало болезни (считает себя больным с...): {illness_date}
   - Дата вызова врача на дом (в анамнезе): {call_date}
   - Период ЭЛН: {exam_date}-{eln_end_date}
   - Дата явки к врачу: {follow_up_date}
4. СОХРАНИТЕ структуру шаблона
5. НЕ добавляйте лишний текст до или после шаблона
6. Верните ТОЛЬКО исправленный шаблон

КРИТИЧЕСКИ ВАЖНО - НЕ ВКЛЮЧАЙТЕ В ОТВЕТ:
1. НЕ включайте шапку документа (логотип, контакты клиники)
2. НЕ включайте главный заголовок "Осмотр терапевта на дому"
3. Возвращайте только СОДЕРЖИМОЕ осмотра (без шапки и заголовка)

ПРАВИЛА ФОРМАТИРОВАНИЯ ТЕКСТА:
1. Используйте **двойные звёздочки** для ЖИРНОГО текста (например: **Жалобы:**, **Диагноз:**, **Анамнез заболевания:**)
2. Обычный текст пишите БЕЗ звёздочек
3. Для нумерованных списков используйте формат: "1. Текст", "2. Текст" и т.д.
4. Заголовки разделов (Жалобы, Анамнез, Объективно, Диагноз и т.д.) ВСЕГДА делайте жирными: **Заголовок:**

Исправленный шаблон:"""

            message = self.client.messages.create(
                model=self.GENERATION_MODEL,
                max_tokens=8192,
                messages=[{"role": "user", "content": prompt}],
            )

            return message.content[0].text
        except Exception as e:
            print(f"Ошибка исправления шаблона: {e}")
            raise
