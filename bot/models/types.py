from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class ClinicMode(str, Enum):
    """Режимы клиник"""
    DINASTIYA = "dinastiya"
    PSKP = "pskp"
    GODOK = "godok"


class BotState(str, Enum):
    """Состояния бота"""
    IDLE = "idle"
    AWAITING_DATA = "awaiting_data"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    AWAITING_CORRECTIONS = "awaiting_corrections"
    AWAITING_PHOTO = "awaiting_photo"  # Ожидание фото осмотра для email
    GODOK_AWAITING_DEAL_CHOICE = "godok_awaiting_deal_choice"  # Выбор из нескольких сделок
    GODOK_AWAITING_CONFIRMATION = "godok_awaiting_confirmation"  # Подтверждение записи в Б24
    GODOK_AWAITING_CORRECTIONS = "godok_awaiting_corrections"  # Ожидание текста правок
    GODOK_AWAITING_PHOTO = "godok_awaiting_photo"  # Ожидание фото для поля «Фото рекомендации»
    GODOK_AWAITING_POSITION = "godok_awaiting_position"  # Ждём должность по сделке из «Больничных листов»


@dataclass
class PatientData:
    """Данные пациента"""
    full_name: str
    birth_date: str
    diagnosis: str
    snils: Optional[str] = None
    examination_date: Optional[str] = None  # Дата осмотра (по умолчанию текущая дата)
    illness_start_date: Optional[str] = None  # Дата начала болезни
    sick_leave_days: Optional[int] = None  # Количество дней ЭЛН (электронный лист нетрудоспособности)
    eln_refused: bool = False  # Отказ от ЭЛН (если True, то не указываем период ЭЛН)
    workplace: Optional[str] = None  # Место работы (или место учёбы для студентов)
    position: Optional[str] = None  # Должность (или курс для студентов)
    eln_start_date: Optional[str] = None  # Дата начала ЭЛН (если указана конкретно)
    eln_end_date: Optional[str] = None  # Дата окончания ЭЛН (если указана конкретно)
    is_student: bool = False  # Студент (Место учёбы/Курс вместо Место работы/Должность)
    student_certificate: Optional[str] = None  # Студенческая справка (например: "129/2025 с 18.12.2025 по 22.12.2025")
    student_cert_end_date: Optional[str] = None  # Дата окончания студенческой справки (для явки к врачу)
    smp_called: bool = False  # «Вызвана бригада СМП» — добавляется в Лечение, ЭЛН ставится отказ


@dataclass
class ExaminationTemplate:
    """Шаблон медицинского осмотра"""
    id: str
    clinic: ClinicMode
    patient_data: PatientData
    content: str
    created_at: datetime


@dataclass
class CurrentTemplate:
    """Текущий редактируемый шаблон"""
    content: str
    patient_data: PatientData


@dataclass
class QueuedDocument:
    """Документ в очереди для пакетной отправки (ПСКП)"""
    file_path: str  # Путь к .docx файлу
    patient_name: str  # ФИО пациента
    examination_date: str  # Дата осмотра
    has_eln: bool  # Есть ли ЭЛН


@dataclass
class BotContext:
    """Контекст пользователя бота"""
    clinic: ClinicMode
    state: BotState
    patient_data: Optional[dict] = None
    current_template: Optional[CurrentTemplate] = None
    corrections_count: int = 0  # Счетчик итераций правок
    saved_document_path: Optional[str] = None  # Путь к сохраненному документу для email
    examination_photos: Optional[list] = None  # Фото осмотра для email вложений
    document_queue: Optional[list] = None  # Очередь документов для пакетной отправки (только для ПСКП)
    # ГОДОК: текущая Б24-сделка и сгенерированные значения полей
    godok_deal_id: Optional[int] = None
    godok_deal_title: Optional[str] = None
    godok_fields: Optional[dict] = None  # {UF_CRM_xxx: value, ...} — готово к crm.deal.update
    godok_candidates: Optional[list] = None  # Найденные сделки при выборе из нескольких
    godok_message_time: Optional[str] = None  # Время отправки сообщения врачом (для "Фактической даты")
    godok_pickup_deal: Optional[dict] = None  # Сделка из «Больничных листов», по которой ждём должность
