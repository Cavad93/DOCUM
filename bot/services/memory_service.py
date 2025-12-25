import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from bot.models.types import ClinicMode


class MemoryService:
    """Сервис для хранения истории запросов и обучения бота"""

    def __init__(self, memory_path: str = "./data/memory/context_memory.json"):
        """
        Инициализация сервиса памяти

        Args:
            memory_path: Путь к файлу с памятью
        """
        self.memory_path = Path(memory_path)
        self.max_memory_size = 100  # Максимум 100 запросов на клинику
        self._ensure_memory_exists()

    def _ensure_memory_exists(self) -> None:
        """Создает файл памяти если его нет"""
        self.memory_path.parent.mkdir(parents=True, exist_ok=True)

        if not self.memory_path.exists():
            # Создаем начальную структуру
            initial_memory = {
                "dinastiya": [],
                "pskp": []
            }
            self._save_memory(initial_memory)

    def _load_memory(self) -> Dict[str, List]:
        """Загружает память из файла"""
        try:
            return json.loads(self.memory_path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"Ошибка загрузки памяти: {e}")
            return {"dinastiya": [], "pskp": []}

    def _save_memory(self, memory: Dict[str, List]) -> None:
        """Сохраняет память в файл"""
        try:
            self.memory_path.write_text(
                json.dumps(memory, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        except Exception as e:
            print(f"Ошибка сохранения памяти: {e}")

    async def add_request(
        self,
        clinic: ClinicMode,
        diagnosis: str,
        patient_name: str,
        template_content: str,
        had_corrections: bool = False,
        corrections_count: int = 0
    ) -> None:
        """
        Добавляет новый запрос в память

        Args:
            clinic: Клиника
            diagnosis: Диагноз пациента
            patient_name: Имя пациента
            template_content: Содержимое созданного шаблона
            had_corrections: Были ли правки
            corrections_count: Количество итераций правок
        """
        try:
            memory = self._load_memory()
            clinic_key = clinic.value

            # Создаем запись о запросе
            request_entry = {
                "timestamp": datetime.now().isoformat(),
                "diagnosis": diagnosis,
                "patient_name": patient_name,
                "template_preview": template_content[:500],  # Первые 500 символов для экономии места
                "had_corrections": had_corrections,
                "corrections_count": corrections_count,
                "success": corrections_count <= 2  # Успешно если <= 2 правок
            }

            # Добавляем в начало списка (новые записи первыми)
            memory[clinic_key].insert(0, request_entry)

            # Ограничиваем размер до max_memory_size
            if len(memory[clinic_key]) > self.max_memory_size:
                memory[clinic_key] = memory[clinic_key][:self.max_memory_size]

            self._save_memory(memory)
            print(f"✓ Запрос добавлен в память {clinic_key}: {len(memory[clinic_key])} записей")

        except Exception as e:
            print(f"Ошибка добавления запроса в память: {e}")

    async def get_similar_cases(
        self,
        clinic: ClinicMode,
        diagnosis: str,
        limit: int = 10
    ) -> List[Dict]:
        """
        Получает похожие случаи из памяти

        Args:
            clinic: Клиника
            diagnosis: Диагноз для поиска
            limit: Максимальное количество случаев

        Returns:
            Список похожих случаев
        """
        try:
            memory = self._load_memory()
            clinic_key = clinic.value

            # Ищем случаи с похожим диагнозом
            similar_cases = []
            diagnosis_lower = diagnosis.lower()

            for entry in memory[clinic_key]:
                entry_diagnosis = entry["diagnosis"].lower()

                # Простое сравнение по вхождению подстрок
                if diagnosis_lower in entry_diagnosis or entry_diagnosis in diagnosis_lower:
                    similar_cases.append(entry)

                if len(similar_cases) >= limit:
                    break

            return similar_cases

        except Exception as e:
            print(f"Ошибка получения похожих случаев: {e}")
            return []

    async def get_successful_cases(
        self,
        clinic: ClinicMode,
        limit: int = 20
    ) -> List[Dict]:
        """
        Получает успешные случаи (без правок или минимум правок)

        Args:
            clinic: Клиника
            limit: Максимальное количество случаев

        Returns:
            Список успешных случаев
        """
        try:
            memory = self._load_memory()
            clinic_key = clinic.value

            # Фильтруем успешные случаи (corrections_count <= 1)
            successful = [
                entry for entry in memory[clinic_key]
                if entry.get("corrections_count", 0) <= 1
            ]

            return successful[:limit]

        except Exception as e:
            print(f"Ошибка получения успешных случаев: {e}")
            return []

    async def get_memory_stats(self, clinic: ClinicMode) -> Dict:
        """
        Получает статистику памяти для клиники

        Args:
            clinic: Клиника

        Returns:
            Статистика памяти
        """
        try:
            memory = self._load_memory()
            clinic_key = clinic.value
            entries = memory[clinic_key]

            total = len(entries)
            successful = len([e for e in entries if e.get("success", False)])
            with_corrections = len([e for e in entries if e.get("had_corrections", False)])

            return {
                "total": total,
                "successful": successful,
                "with_corrections": with_corrections,
                "success_rate": (successful / total * 100) if total > 0 else 0
            }

        except Exception as e:
            print(f"Ошибка получения статистики: {e}")
            return {"total": 0, "successful": 0, "with_corrections": 0, "success_rate": 0}
