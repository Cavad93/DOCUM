"""Сервис для запоминания и применения частых исправлений пользователя"""
import json
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional
from difflib import SequenceMatcher


class CorrectionsMemoryService:
    """Сервис для хранения истории исправлений и обучения на их основе"""

    def __init__(self, corrections_file: str = "./data/corrections_memory.json"):
        """
        Инициализация сервиса

        Args:
            corrections_file: Путь к файлу с историей исправлений
        """
        self.corrections_file = Path(corrections_file)
        self._ensure_corrections_file()

    def _ensure_corrections_file(self) -> None:
        """Создаёт файл с историей если его нет"""
        if not self.corrections_file.exists():
            self.corrections_file.parent.mkdir(parents=True, exist_ok=True)
            self.corrections_file.write_text(
                json.dumps([], ensure_ascii=False, indent=2),
                encoding="utf-8"
            )

    def add_correction(
        self,
        diagnosis: str,
        patient_age: Optional[int],
        correction_text: str,
        original_template: str,
        corrected_template: str,
        clinic: str
    ) -> None:
        """
        Сохранить исправление в историю

        Args:
            diagnosis: Диагноз пациента
            patient_age: Возраст пациента
            correction_text: Текст правки от пользователя
            original_template: Исходный шаблон
            corrected_template: Исправленный шаблон
            clinic: Клиника (dinastiya/pskp)
        """
        try:
            corrections = self._load_corrections()

            correction_entry = {
                "timestamp": datetime.now().isoformat(),
                "diagnosis": diagnosis.lower().strip(),
                "patient_age": patient_age,
                "correction_text": correction_text,
                "original_template": original_template,
                "corrected_template": corrected_template,
                "clinic": clinic,
            }

            corrections.append(correction_entry)

            # Сохраняем только последние 200 исправлений
            if len(corrections) > 200:
                corrections = corrections[-200:]

            self.corrections_file.write_text(
                json.dumps(corrections, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )

            print(f"✓ Исправление сохранено в память (всего: {len(corrections)})")

        except Exception as e:
            print(f"Ошибка сохранения исправления: {e}")

    def get_similar_corrections(
        self,
        diagnosis: str,
        patient_age: Optional[int],
        clinic: str,
        limit: int = 5
    ) -> List[Dict]:
        """
        Получить похожие исправления из истории

        Args:
            diagnosis: Диагноз пациента
            patient_age: Возраст пациента
            clinic: Клиника
            limit: Максимальное количество исправлений

        Returns:
            Список похожих исправлений с оценкой релевантности
        """
        try:
            corrections = self._load_corrections()
            diagnosis_lower = diagnosis.lower().strip()

            # Фильтруем по клинике
            clinic_corrections = [c for c in corrections if c.get("clinic") == clinic]

            if not clinic_corrections:
                return []

            # Оцениваем релевантность каждого исправления
            scored_corrections = []
            for correction in clinic_corrections:
                score = self._calculate_relevance_score(
                    diagnosis_lower,
                    patient_age,
                    correction
                )
                if score > 0.3:  # Порог релевантности
                    scored_corrections.append({
                        "correction": correction,
                        "relevance_score": score
                    })

            # Сортируем по релевантности
            scored_corrections.sort(key=lambda x: x["relevance_score"], reverse=True)

            # Возвращаем топ N
            return scored_corrections[:limit]

        except Exception as e:
            print(f"Ошибка получения похожих исправлений: {e}")
            return []

    def _calculate_relevance_score(
        self,
        diagnosis: str,
        patient_age: Optional[int],
        correction: Dict
    ) -> float:
        """
        Рассчитать оценку релевантности исправления

        Args:
            diagnosis: Диагноз текущего пациента
            patient_age: Возраст текущего пациента
            correction: Запись исправления из истории

        Returns:
            Оценка от 0 до 1
        """
        score = 0.0

        # Сравнение диагнозов (основной фактор)
        correction_diagnosis = correction.get("diagnosis", "").lower()
        diagnosis_similarity = SequenceMatcher(
            None,
            diagnosis,
            correction_diagnosis
        ).ratio()

        # Проверка на ключевые слова в диагнозе
        diagnosis_words = set(diagnosis.split())
        correction_words = set(correction_diagnosis.split())
        common_words = diagnosis_words & correction_words

        if common_words:
            diagnosis_similarity = max(diagnosis_similarity, 0.7)

        score += diagnosis_similarity * 0.7  # 70% веса на диагноз

        # Сравнение возраста (если указан)
        if patient_age is not None and correction.get("patient_age") is not None:
            age_diff = abs(patient_age - correction["patient_age"])

            # Возрастная близость (чем меньше разница, тем лучше)
            if age_diff == 0:
                age_score = 1.0
            elif age_diff <= 5:
                age_score = 0.8
            elif age_diff <= 10:
                age_score = 0.6
            elif age_diff <= 20:
                age_score = 0.4
            else:
                age_score = 0.2

            score += age_score * 0.3  # 30% веса на возраст
        else:
            # Если возраст не указан, используем только диагноз
            score = diagnosis_similarity

        return score

    def _load_corrections(self) -> List[Dict]:
        """Загрузить все исправления из файла"""
        try:
            return json.loads(self.corrections_file.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"Ошибка чтения истории исправлений: {e}")
            return []

    def get_statistics(self, clinic: str) -> Dict:
        """
        Получить статистику по исправлениям

        Args:
            clinic: Клиника

        Returns:
            Словарь со статистикой
        """
        try:
            corrections = self._load_corrections()
            clinic_corrections = [c for c in corrections if c.get("clinic") == clinic]

            if not clinic_corrections:
                return {
                    "total": 0,
                    "most_corrected_diagnoses": []
                }

            # Подсчёт исправлений по диагнозам
            diagnosis_counts = {}
            for correction in clinic_corrections:
                diagnosis = correction.get("diagnosis", "неизвестно")
                diagnosis_counts[diagnosis] = diagnosis_counts.get(diagnosis, 0) + 1

            # Сортируем по количеству
            most_corrected = sorted(
                diagnosis_counts.items(),
                key=lambda x: x[1],
                reverse=True
            )[:5]

            return {
                "total": len(clinic_corrections),
                "most_corrected_diagnoses": [
                    {"diagnosis": d, "count": c} for d, c in most_corrected
                ]
            }

        except Exception as e:
            print(f"Ошибка получения статистики: {e}")
            return {"total": 0, "most_corrected_diagnoses": []}
