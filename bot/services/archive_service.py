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

    def _collect_paragraph_styles_from_template(self, doc: Document, start_index: int) -> dict:
        """
        Сканирует базовый шаблон и собирает РЕАЛЬНЫЕ стили параграфов

        Сохраняет все параграфы из шаблона для последующего копирования их стилей

        Returns:
            dict с сохранёнными параграфами и их индексами
        """
        import re

        saved_paragraphs = []

        # Сохраняем ВСЕ параграфы из шаблона (они будут удалены, но нам нужны их стили)
        for i, para in enumerate(doc.paragraphs[start_index:]):
            if not para.runs or not para.text.strip():
                continue

            # Создаём копию информации о параграфе
            para_info = {
                'text': para.text.strip(),
                'is_bold': para.runs[0].font.bold if para.runs else False,
                'is_list': re.match(r'^\d+\.', para.text.strip()) is not None,
                'font_name': para.runs[0].font.name if para.runs else None,
                'font_size': para.runs[0].font.size if para.runs else None,
                'left_indent': para.paragraph_format.left_indent,
                'right_indent': para.paragraph_format.right_indent,
                'first_line_indent': para.paragraph_format.first_line_indent,
                'line_spacing': para.paragraph_format.line_spacing,
                'space_before': para.paragraph_format.space_before,
                'space_after': para.paragraph_format.space_after,
                'alignment': para.paragraph_format.alignment,
            }
            saved_paragraphs.append(para_info)

        return {
            'all_paragraphs': saved_paragraphs,
        }

    def _find_matching_style(self, line: str, template_styles: dict):
        """
        Находит подходящий стиль для строки из сохранённых стилей шаблона

        Args:
            line: Строка текста
            template_styles: Сохранённые стили из шаблона

        Returns:
            dict с информацией о стиле или None
        """
        import re

        # Определяем характеристики текущей строки
        has_bold = '**' in line
        is_list = re.match(r'^\d+\.', line.strip()) is not None

        # Ищем наиболее подходящий параграф из шаблона
        for para_info in template_styles['all_paragraphs']:
            # Если это список, ищем параграф-список
            if is_list and para_info['is_list']:
                return para_info
            # Если жирный текст, ищем жирный параграф
            elif has_bold and para_info['is_bold']:
                return para_info
            # Если обычный текст, ищем обычный параграф
            elif not has_bold and not is_list and not para_info['is_bold'] and not para_info['is_list']:
                return para_info

        # Если не нашли точное совпадение, возвращаем первый доступный
        return template_styles['all_paragraphs'][0] if template_styles['all_paragraphs'] else None

    def _apply_paragraph_style(self, target_para, style_info):
        """
        Применяет сохранённый стиль к параграфу

        Args:
            target_para: Целевой параграф
            style_info: dict с информацией о стиле из шаблона
        """
        if not style_info:
            return

        # Копируем ВСЁ форматирование из эталона
        target_para.paragraph_format.left_indent = style_info['left_indent']
        target_para.paragraph_format.right_indent = style_info['right_indent']
        target_para.paragraph_format.first_line_indent = style_info['first_line_indent']
        target_para.paragraph_format.line_spacing = style_info['line_spacing']
        target_para.paragraph_format.space_before = style_info['space_before']
        target_para.paragraph_format.space_after = style_info['space_after']
        target_para.paragraph_format.alignment = style_info['alignment']

    def _replace_document_content(self, doc: Document, new_content: str) -> None:
        """
        Заменяет текстовое содержимое документа, сохраняя форматирование и структуру

        Стратегия:
        1. Находим начало основного содержимого (после логотипа и заголовка)
        2. СОХРАНЯЕМ все стили параграфов из базового шаблона
        3. Удаляем все старые параграфы содержимого
        4. Для каждой новой строки находим подходящий стиль из шаблона и применяем его

        Args:
            doc: Документ Word
            new_content: Новое текстовое содержимое с markdown-разметкой
        """
        # Находим индекс, с которого начинается заменяемое содержимое
        start_index = 0

        for i, para in enumerate(doc.paragraphs):
            text_lower = para.text.lower().strip()
            # Ищем заголовок "Осмотр терапевта на дому" или подобные
            if 'осмотр терапевта' in text_lower or 'консультация терапевта' in text_lower:
                # Начинаем заменять со следующего параграфа
                start_index = i + 1
                break

        # КРИТИЧЕСКИ ВАЖНО: Сохраняем все стили из шаблона ПЕРЕД удалением!
        template_styles = self._collect_paragraph_styles_from_template(doc, start_index)

        # Удаляем все параграфы после заголовка (старое содержимое)
        paragraphs_to_remove = list(doc.paragraphs[start_index:])
        for para in paragraphs_to_remove:
            p = para._element
            p.getparent().remove(p)

        # Парсим и вставляем новое содержимое
        for line in new_content.split('\n'):
            # Создаём новый параграф
            new_para = doc.add_paragraph()

            # Находим подходящий стиль из сохранённых стилей шаблона
            matching_style = self._find_matching_style(line, template_styles)

            # Применяем форматирование параграфа из эталона
            self._apply_paragraph_style(new_para, matching_style)

            # Парсим markdown и вставляем текст с форматированием runs
            segments = self._parse_markdown_formatting(line)

            for segment_text, is_bold in segments:
                run = new_para.add_run(segment_text)

                # Берём шрифт и размер из найденного стиля
                if matching_style:
                    if matching_style['font_name']:
                        run.font.name = matching_style['font_name']
                    if matching_style['font_size']:
                        run.font.size = matching_style['font_size']

                # Жирность берём из markdown
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
