import base64
import json
import os
from pathlib import Path
from typing import Dict, List, Optional

from anthropic import Anthropic
from docx import Document

from bot.models.types import PatientData, ClinicMode


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

    async def generate_examination_template(
        self,
        patient_data: PatientData,
        clinic: ClinicMode,
        archive_templates: Optional[List[str]] = None,
    ) -> str:
        """
        Генерация шаблона осмотра на основе готового шаблона
        Использует Claude Opus 4 для заполнения готового медицинского шаблона

        Args:
            patient_data: Данные пациента
            clinic: Режим клиники
            archive_templates: Похожие шаблоны из архива для справки

        Returns:
            Заполненный шаблон осмотра
        """
        try:
            base_template = self._get_base_template(clinic)

            if not base_template:
                raise ValueError(f"Шаблон для клиники {clinic.value} не найден")

            archive_context = ""
            if archive_templates:
                archive_context = "\n\nПРИМЕРЫ ПОХОЖИХ ОСМОТРОВ ИЗ АРХИВА (для справки):\n" + "\n---\n".join(
                    archive_templates
                )

            snils_line = f"- СНИЛС: {patient_data.snils}\n" if patient_data.snils else ""

            prompt = f"""Вы медицинский ассистент. Ваша задача - заполнить готовый шаблон медицинского осмотра.

ДАННЫЕ ПАЦИЕНТА:
- ФИО: {patient_data.full_name}
- Дата рождения: {patient_data.birth_date}
{snils_line}- Диагноз: {patient_data.diagnosis}

ГОТОВЫЙ ШАБЛОН КЛИНИКИ:
{base_template}

{archive_context}

ИНСТРУКЦИИ:
1. Используйте ТОЧНО ЭТОТ шаблон, не изменяйте его структуру
2. Заполните все разделы шаблона реалистичными медицинскими данными
3. Вставьте данные пациента (ФИО, дата рождения, СНИЛС) в соответствующие места
4. Заполните разделы с учетом указанного диагноза: {patient_data.diagnosis}
5. Используйте примеры из архива как справочный материал для стиля заполнения
6. НЕ добавляйте лишний текст до или после шаблона
7. Верните ТОЛЬКО заполненный шаблон

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
            snils_line = f"- СНИЛС: {patient_data.snils}\n" if patient_data.snils else ""

            prompt = f"""Вы медицинский ассистент. У вас есть готовый шаблон медицинского осмотра, который нужно исправить.

ТЕКУЩИЙ ШАБЛОН:
{current_template}

ДАННЫЕ ПАЦИЕНТА:
- ФИО: {patient_data.full_name}
- Дата рождения: {patient_data.birth_date}
{snils_line}- Диагноз: {patient_data.diagnosis}

КОММЕНТАРИИ ДЛЯ ИСПРАВЛЕНИЯ:
{corrections}

ИНСТРУКЦИИ:
1. Внимательно прочитайте комментарии пользователя
2. Внесите необходимые исправления в шаблон
3. Сохраните структуру и формат шаблона
4. НЕ добавляйте лишний текст до или после шаблона
5. Верните ТОЛЬКО исправленный шаблон

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
