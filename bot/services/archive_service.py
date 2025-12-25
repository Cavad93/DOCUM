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

    def _parse_markdown_formatting(self, text: str) -> list:
        """
        Парсит markdown-разметку и возвращает список сегментов с форматированием

        Поддерживает:
        - **текст** = жирный шрифт
        - обычный текст = обычный шрифт

        Returns:
            List[(text, is_bold)]
        """
        import re
        segments = []

        # Регулярное выражение для поиска **текст**
        pattern = r'\*\*(.+?)\*\*'
        last_end = 0

        for match in re.finditer(pattern, text):
            # Добавляем обычный текст до жирного
            if match.start() > last_end:
                normal_text = text[last_end:match.start()]
                if normal_text:
                    segments.append((normal_text, False))

            # Добавляем жирный текст
            bold_text = match.group(1)
            segments.append((bold_text, True))
            last_end = match.end()

        # Добавляем оставшийся обычный текст
        if last_end < len(text):
            remaining = text[last_end:]
            if remaining:
                segments.append((remaining, False))

        # Если ничего не нашли, весь текст обычный
        if not segments:
            segments.append((text, False))

        return segments

    def _collect_template_styles(self, doc: Document, start_index: int) -> dict:
        """
        Собирает стили из разных параграфов базового шаблона

        Returns:
            dict с эталонными параграфами для разных типов
        """
        import re

        styles = {
            'normal': None,      # Обычный текст
            'bold': None,        # Жирный текст (заголовки)
            'list': None,        # Элементы списка
        }

        # Сканируем параграфы после заголовка для поиска разных стилей
        for para in doc.paragraphs[start_index:]:
            if not para.runs:
                continue

            text = para.text.strip()
            if not text:
                continue

            # Определяем тип параграфа
            is_bold = para.runs[0].font.bold if para.runs else False
            is_list = re.match(r'^\d+\.', text) is not None

            # Сохраняем эталоны
            if is_list and styles['list'] is None:
                styles['list'] = para
            elif is_bold and styles['bold'] is None:
                styles['bold'] = para
            elif not is_bold and styles['normal'] is None:
                styles['normal'] = para

            # Если нашли все типы, можно выходить
            if all(styles.values()):
                break

        # Если какого-то типа нет, используем первый доступный
        fallback = doc.paragraphs[start_index] if start_index < len(doc.paragraphs) else None
        for key in styles:
            if styles[key] is None:
                styles[key] = fallback

        return styles

    def _copy_paragraph_format(self, target_para, source_para):
        """
        Копирует форматирование параграфа из источника в целевой параграф

        Args:
            target_para: Целевой параграф
            source_para: Исходный параграф (эталон)
        """
        if not source_para or not source_para.runs:
            return

        # Копируем форматирование параграфа
        target_para.paragraph_format.left_indent = source_para.paragraph_format.left_indent
        target_para.paragraph_format.right_indent = source_para.paragraph_format.right_indent
        target_para.paragraph_format.first_line_indent = source_para.paragraph_format.first_line_indent
        target_para.paragraph_format.line_spacing = source_para.paragraph_format.line_spacing
        target_para.paragraph_format.space_before = source_para.paragraph_format.space_before
        target_para.paragraph_format.space_after = source_para.paragraph_format.space_after
        target_para.paragraph_format.alignment = source_para.paragraph_format.alignment

    def _replace_document_content(self, doc: Document, new_content: str) -> None:
        """
        Заменяет текстовое содержимое документа, сохраняя форматирование и структуру

        Стратегия:
        1. Находим начало основного содержимого (после логотипа и заголовка)
        2. Сканируем шаблон и собираем эталоны разных стилей
        3. Удаляем все старые параграфы содержимого
        4. Вставляем новый текст, применяя подходящий стиль к каждой строке

        Args:
            doc: Документ Word
            new_content: Новое текстовое содержимое с markdown-разметкой
        """
        import re

        # Находим индекс, с которого начинается заменяемое содержимое
        start_index = 0

        for i, para in enumerate(doc.paragraphs):
            text_lower = para.text.lower().strip()
            # Ищем заголовок "Осмотр терапевта на дому" или подобные
            if 'осмотр терапевта' in text_lower or 'консультация терапевта' in text_lower:
                # Начинаем заменять со следующего параграфа
                start_index = i + 1
                break

        # Собираем эталонные стили из шаблона ПЕРЕД удалением
        template_styles = self._collect_template_styles(doc, start_index)

        # Удаляем все параграфы после заголовка (старое содержимое)
        paragraphs_to_remove = list(doc.paragraphs[start_index:])
        for para in paragraphs_to_remove:
            p = para._element
            p.getparent().remove(p)

        # Получаем базовый шрифт
        base_font_name = None
        base_font_size = None
        if template_styles['normal'] and template_styles['normal'].runs:
            ref_run = template_styles['normal'].runs[0]
            base_font_name = ref_run.font.name
            base_font_size = ref_run.font.size

        # Парсим и вставляем новое содержимое
        for line in new_content.split('\n'):
            # Создаём новый параграф
            new_para = doc.add_paragraph()

            # Определяем тип строки
            is_list_item = re.match(r'^\d+\.', line.strip()) is not None
            has_bold = '**' in line

            # Выбираем подходящий эталон стиля
            if is_list_item:
                style_source = template_styles['list']
            elif has_bold:
                style_source = template_styles['bold']
            else:
                style_source = template_styles['normal']

            # Копируем форматирование параграфа
            self._copy_paragraph_format(new_para, style_source)

            # Парсим markdown и вставляем текст с форматированием
            segments = self._parse_markdown_formatting(line)

            for segment_text, is_bold in segments:
                run = new_para.add_run(segment_text)
                if base_font_name:
                    run.font.name = base_font_name
                if base_font_size:
                    run.font.size = base_font_size
                run.font.bold = is_bold

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
