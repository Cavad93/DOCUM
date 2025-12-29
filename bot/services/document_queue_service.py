"""Сервис для управления очередью документов для пакетной отправки"""
import json
import shutil
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime


class DocumentQueueService:
    """Сервис для хранения и управления очередью документов"""

    def __init__(self, queue_dir: str = "./data/queue", metadata_file: str = "./data/queue_metadata.json"):
        """
        Инициализация сервиса

        Args:
            queue_dir: Директория для хранения документов очереди
            metadata_file: Файл с метаданными очереди
        """
        self.queue_dir = Path(queue_dir)
        self.metadata_file = Path(metadata_file)
        self._ensure_directories()

    def _ensure_directories(self) -> None:
        """Создает необходимые директории и файлы"""
        # Создаем директорию для очереди
        self.queue_dir.mkdir(parents=True, exist_ok=True)

        # Создаем файл метаданных если его нет
        if not self.metadata_file.exists():
            self.metadata_file.parent.mkdir(parents=True, exist_ok=True)
            self.metadata_file.write_text(
                json.dumps({}, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )

    def _load_metadata(self) -> Dict:
        """Загружает метаданные очереди из файла"""
        try:
            return json.loads(self.metadata_file.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"Ошибка чтения метаданных очереди: {e}")
            return {}

    def _save_metadata(self, metadata: Dict) -> None:
        """Сохраняет метаданные очереди в файл"""
        try:
            self.metadata_file.write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        except Exception as e:
            print(f"Ошибка сохранения метаданных очереди: {e}")

    def add_document(
        self,
        user_id: int,
        clinic: str,
        temp_filepath: str,
        patient_name: str,
        examination_date: str,
        has_eln: bool
    ) -> bool:
        """
        Добавляет документ в очередь пользователя

        Args:
            user_id: ID пользователя Telegram
            clinic: Клиника (dinastiya/pskp)
            temp_filepath: Путь к временному файлу
            patient_name: ФИО пациента
            examination_date: Дата осмотра
            has_eln: Есть ли ЭЛН

        Returns:
            True если успешно добавлено
        """
        try:
            # Генерируем уникальное имя файла
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            safe_name = "".join(c if c.isalnum() or c == ' ' else '_' for c in patient_name)
            filename = f"{user_id}_{clinic}_{safe_name}_{timestamp}.docx"
            permanent_path = self.queue_dir / filename

            # Копируем файл в постоянную папку
            shutil.copy2(temp_filepath, permanent_path)

            # Загружаем метаданные
            metadata = self._load_metadata()

            # Создаем ключ для пользователя
            user_key = f"{user_id}_{clinic}"
            if user_key not in metadata:
                metadata[user_key] = []

            # Добавляем запись о документе
            metadata[user_key].append({
                "filename": filename,
                "patient_name": patient_name,
                "examination_date": examination_date,
                "has_eln": has_eln,
                "added_at": datetime.now().isoformat()
            })

            # Сохраняем метаданные
            self._save_metadata(metadata)

            print(f"✓ Документ добавлен в очередь: {filename}")
            return True

        except Exception as e:
            print(f"Ошибка добавления документа в очередь: {e}")
            return False

    def get_queue(self, user_id: int, clinic: str, filter_date: Optional[str] = None) -> List[Dict]:
        """
        Получает очередь документов для пользователя

        Args:
            user_id: ID пользователя Telegram
            clinic: Клиника
            filter_date: Фильтр по дате (опционально, формат ДД.ММ.ГГГГ)

        Returns:
            Список документов с их метаданными и путями
        """
        try:
            metadata = self._load_metadata()
            user_key = f"{user_id}_{clinic}"

            if user_key not in metadata:
                return []

            documents = []
            for doc in metadata[user_key]:
                # Фильтруем по дате если указано
                if filter_date and doc["examination_date"] != filter_date:
                    continue

                # Проверяем, что файл существует
                file_path = self.queue_dir / doc["filename"]
                if file_path.exists():
                    documents.append({
                        "file_path": str(file_path),
                        "patient_name": doc["patient_name"],
                        "examination_date": doc["examination_date"],
                        "has_eln": doc["has_eln"]
                    })
                else:
                    print(f"⚠️ Файл не найден в очереди: {doc['filename']}")

            return documents

        except Exception as e:
            print(f"Ошибка получения очереди: {e}")
            return []

    def get_queue_status(self, user_id: int, clinic: str) -> Dict:
        """
        Получает статус очереди (количество документов, группировка по датам)

        Args:
            user_id: ID пользователя
            clinic: Клиника

        Returns:
            Словарь со статистикой очереди
        """
        try:
            metadata = self._load_metadata()
            user_key = f"{user_id}_{clinic}"

            if user_key not in metadata:
                return {
                    "total": 0,
                    "with_eln": 0,
                    "without_eln": 0,
                    "by_date": {}
                }

            documents = metadata[user_key]
            by_date = {}
            with_eln = 0
            without_eln = 0

            for doc in documents:
                # Проверяем существование файла
                file_path = self.queue_dir / doc["filename"]
                if not file_path.exists():
                    continue

                # Подсчитываем статистику
                exam_date = doc["examination_date"]
                if exam_date not in by_date:
                    by_date[exam_date] = {"total": 0, "with_eln": 0, "without_eln": 0}

                by_date[exam_date]["total"] += 1

                if doc["has_eln"]:
                    with_eln += 1
                    by_date[exam_date]["with_eln"] += 1
                else:
                    without_eln += 1
                    by_date[exam_date]["without_eln"] += 1

            return {
                "total": with_eln + without_eln,
                "with_eln": with_eln,
                "without_eln": without_eln,
                "by_date": by_date
            }

        except Exception as e:
            print(f"Ошибка получения статуса очереди: {e}")
            return {"total": 0, "with_eln": 0, "without_eln": 0, "by_date": {}}

    def clear_queue(self, user_id: int, clinic: str, filter_date: Optional[str] = None) -> int:
        """
        Очищает очередь документов

        Args:
            user_id: ID пользователя
            clinic: Клиника
            filter_date: Удалить только документы за конкретную дату (опционально)

        Returns:
            Количество удаленных документов
        """
        try:
            metadata = self._load_metadata()
            user_key = f"{user_id}_{clinic}"

            if user_key not in metadata:
                return 0

            documents = metadata[user_key]
            removed_count = 0

            # Если фильтр по дате не указан - удаляем все
            if not filter_date:
                for doc in documents:
                    file_path = self.queue_dir / doc["filename"]
                    if file_path.exists():
                        file_path.unlink()
                        removed_count += 1

                metadata[user_key] = []

            else:
                # Удаляем только документы за указанную дату
                remaining_docs = []
                for doc in documents:
                    if doc["examination_date"] == filter_date:
                        file_path = self.queue_dir / doc["filename"]
                        if file_path.exists():
                            file_path.unlink()
                            removed_count += 1
                    else:
                        remaining_docs.append(doc)

                metadata[user_key] = remaining_docs

            self._save_metadata(metadata)
            print(f"✓ Удалено {removed_count} документов из очереди")
            return removed_count

        except Exception as e:
            print(f"Ошибка очистки очереди: {e}")
            return 0
