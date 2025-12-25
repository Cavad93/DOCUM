import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from docx import Document

from bot.models.types import ExaminationTemplate, PatientData, ClinicMode


class ArchiveService:
    """Сервис для работы с архивом шаблонов"""

    def __init__(self, archive_path: str = "./data/archive"):
        """
        Инициализация сервиса

        Args:
            archive_path: Путь к директории архива
        """
        self.archive_path = Path(archive_path)
        self._ensure_archive_exists()

    def _ensure_archive_exists(self) -> None:
        """Создает директорию архива если её нет"""
        self.archive_path.mkdir(parents=True, exist_ok=True)

    async def save_template(self, template: ExaminationTemplate) -> None:
        """
        Сохранение шаблона в архив в формате .docx с сохранением форматирования
        Копирует базовый шаблон клиники и заменяет в нем текст
        Структура: archive/{clinic}/{date}/файл.docx + файл.meta.json

        Args:
            template: Шаблон для сохранения
        """
        try:
            # Создаем структуру папок: clinic/date/
            date_str = template.created_at.strftime("%Y-%m-%d")
            clinic_dir = self.archive_path / template.clinic.value / date_str
            clinic_dir.mkdir(parents=True, exist_ok=True)

            # Генерируем имя файла
            base_filename = self._generate_filename(template)
            docx_filepath = clinic_dir / base_filename
            meta_filepath = clinic_dir / f"{base_filename.replace('.docx', '.meta.json')}"

            # Находим базовый шаблон для клиники
            base_template_path = self._get_base_template_path(template.clinic)

            if base_template_path and base_template_path.exists():
                # Копируем базовый шаблон, сохраняя все форматирование
                shutil.copy2(str(base_template_path), str(docx_filepath))

                # Открываем скопированный документ
                doc = Document(str(docx_filepath))

                # Заменяем содержимое, сохраняя структуру и форматирование
                self._replace_document_content(doc, template.content)

                # Сохраняем изменения
                doc.save(str(docx_filepath))
            else:
                # Если базового шаблона нет, создаем новый документ (резервный вариант)
                doc = Document()
                for line in template.content.split('\n'):
                    doc.add_paragraph(line)
                doc.save(str(docx_filepath))

            # Сохраняем метаданные в отдельный .meta.json файл для поиска
            metadata = {
                "id": template.id,
                "clinic": template.clinic.value,
                "patient_data": {
                    "full_name": template.patient_data.full_name,
                    "birth_date": template.patient_data.birth_date,
                    "diagnosis": template.patient_data.diagnosis,
                    "snils": template.patient_data.snils,
                    "examination_date": template.patient_data.examination_date,
                    "illness_start_date": template.patient_data.illness_start_date,
                    "sick_leave_days": template.patient_data.sick_leave_days,
                },
                "created_at": template.created_at.isoformat(),
                "docx_file": base_filename,
            }

            meta_filepath.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"✓ Шаблон сохранен: {template.clinic.value}/{date_str}/{base_filename}")
        except Exception as e:
            print(f"Ошибка сохранения шаблона: {e}")
            raise

    async def find_templates(
        self,
        patient_data: Optional[Dict[str, str]] = None,
        clinic: Optional[ClinicMode] = None,
    ) -> List[ExaminationTemplate]:
        """
        Поиск шаблонов по параметрам
        Ищет в структуре: archive/{clinic}/{date}/*.meta.json и читает соответствующие .docx

        Args:
            patient_data: Частичные данные пациента для поиска
            clinic: Режим клиники

        Returns:
            Список найденных шаблонов
        """
        try:
            templates = []

            # Ищем все .meta.json файлы (метаданные шаблонов)
            for meta_filepath in self.archive_path.glob("**/*.meta.json"):
                try:
                    # Читаем метаданные
                    metadata = json.loads(meta_filepath.read_text(encoding="utf-8"))

                    # Проверка совпадения параметров
                    matches = True

                    if clinic and metadata.get("clinic") != clinic.value:
                        matches = False

                    if patient_data:
                        if "full_name" in patient_data and metadata["patient_data"]["full_name"] != patient_data[
                            "full_name"
                        ]:
                            matches = False

                        if "diagnosis" in patient_data and metadata["patient_data"]["diagnosis"] != patient_data[
                            "diagnosis"
                        ]:
                            matches = False

                    if matches:
                        # Находим соответствующий .docx файл
                        docx_filepath = meta_filepath.parent / metadata["docx_file"]

                        # Читаем содержимое из .docx
                        content = self._read_docx_content(docx_filepath)

                        # Создаем объект шаблона
                        template = ExaminationTemplate(
                            id=metadata["id"],
                            clinic=ClinicMode(metadata["clinic"]),
                            patient_data=PatientData(
                                full_name=metadata["patient_data"]["full_name"],
                                birth_date=metadata["patient_data"]["birth_date"],
                                diagnosis=metadata["patient_data"]["diagnosis"],
                                snils=metadata["patient_data"].get("snils"),
                                examination_date=metadata["patient_data"].get("examination_date"),
                                illness_start_date=metadata["patient_data"].get("illness_start_date"),
                                sick_leave_days=metadata["patient_data"].get("sick_leave_days"),
                            ),
                            content=content,
                            created_at=datetime.fromisoformat(metadata["created_at"]),
                        )
                        templates.append(template)
                except Exception as e:
                    print(f"Ошибка чтения файла {meta_filepath}: {e}")
                    continue

            return templates
        except Exception as e:
            print(f"Ошибка поиска шаблонов: {e}")
            return []

    def _read_docx_content(self, filepath: Path) -> str:
        """
        Чтение содержимого из .docx файла

        Args:
            filepath: Путь к .docx файлу

        Returns:
            Текстовое содержимое документа
        """
        try:
            doc = Document(str(filepath))
            paragraphs = [paragraph.text for paragraph in doc.paragraphs]
            return '\n'.join(paragraphs)
        except Exception as e:
            print(f"Ошибка чтения .docx файла {filepath}: {e}")
            return ""

    async def get_similar_templates(self, diagnosis: str, clinic: ClinicMode, limit: int = 3) -> List[str]:
        """
        Получение похожих шаблонов для использования в качестве контекста

        Args:
            diagnosis: Диагноз для поиска
            clinic: Режим клиники
            limit: Максимальное количество шаблонов

        Returns:
            Список содержимого похожих шаблонов
        """
        try:
            templates = await self.find_templates(patient_data={"diagnosis": diagnosis}, clinic=clinic)
            return [t.content for t in templates[:limit]]
        except Exception as e:
            print(f"Ошибка получения похожих шаблонов: {e}")
            return []

    async def get_statistics(self) -> Dict[str, any]:
        """
        Получение статистики архива
        Ищет в структуре: archive/{clinic}/{date}/*.docx

        Returns:
            Словарь со статистикой
        """
        try:
            total = 0
            by_clinic: Dict[str, int] = {}

            # Ищем все .docx файлы (готовые осмотры)
            for filepath in self.archive_path.glob("**/*.docx"):
                try:
                    total += 1

                    # Определяем клинику по структуре папок: archive/{clinic}/{date}/file.docx
                    parts = filepath.parts
                    if len(parts) >= 3:
                        clinic = parts[-3]  # Клиника - 3я папка с конца
                        by_clinic[clinic] = by_clinic.get(clinic, 0) + 1
                except Exception as e:
                    print(f"Ошибка обработки файла {filepath}: {e}")
                    continue

            return {"total": total, "by_clinic": by_clinic}
        except Exception as e:
            print(f"Ошибка получения статистики: {e}")
            return {"total": 0, "by_clinic": {}}

    def _get_base_template_path(self, clinic: ClinicMode) -> Optional[Path]:
        """
        Получает путь к базовому .docx шаблону для клиники

        Args:
            clinic: Клиника

        Returns:
            Путь к базовому шаблону или None
        """
        templates_dir = Path(__file__).parent.parent.parent / "data" / "templates"

        if clinic == ClinicMode.DINASTIYA:
            template_path = templates_dir / "dinastiya" / "Артемьева+.docx"
        elif clinic == ClinicMode.PSKP:
            template_path = templates_dir / "pskp" / "Митина Н.А.docx"
        else:
            return None

        return template_path if template_path.exists() else None

    def _replace_document_content(self, doc: Document, new_content: str) -> None:
        """
        Заменяет текстовое содержимое документа, сохраняя форматирование и структуру

        Стратегия:
        1. Удаляем все текстовые параграфы (кроме заголовков и логотипов)
        2. Добавляем новое содержимое с сохранением базовых стилей

        Args:
            doc: Документ Word
            new_content: Новое текстовое содержимое
        """
        # Находим индекс, после которого начинается основное содержимое
        # (пропускаем заголовок документа и логотип)
        start_index = 0
        for i, para in enumerate(doc.paragraphs):
            # Ищем первый параграф с датой консультации или похожим маркером
            if any(marker in para.text.lower() for marker in ['дата консультации', 'дата:', 'фио пациента']):
                start_index = i
                break

        # Удаляем старые параграфы с содержимым, сохраняя заголовок
        paragraphs_to_remove = []
        for i in range(start_index, len(doc.paragraphs)):
            paragraphs_to_remove.append(doc.paragraphs[i])

        for para in paragraphs_to_remove:
            p = para._element
            p.getparent().remove(p)

        # Добавляем новое содержимое
        for line in new_content.split('\n'):
            doc.add_paragraph(line)

    def _generate_filename(self, template: ExaminationTemplate) -> str:
        """
        Генерация имени файла для шаблона в формате .docx
        Clinic и date уже в пути папки, поэтому включаем только имя и время

        Args:
            template: Шаблон

        Returns:
            Имя файла с расширением .docx
        """
        time_str = datetime.now().strftime("%H-%M-%S")
        safe_name = "".join(c if c.isalnum() else "_" for c in template.patient_data.full_name)
        return f"{safe_name}_{time_str}_{template.id[:8]}.docx"
