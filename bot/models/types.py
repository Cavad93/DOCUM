from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class ClinicMode(str, Enum):
    """Режимы клиник"""
    DINASTIYA = "dinastiya"
    PSKP = "pskp"


class BotState(str, Enum):
    """Состояния бота"""
    IDLE = "idle"
    AWAITING_DATA = "awaiting_data"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    AWAITING_CORRECTIONS = "awaiting_corrections"


@dataclass
class PatientData:
    """Данные пациента"""
    full_name: str
    birth_date: str
    diagnosis: str
    snils: Optional[str] = None


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
class BotContext:
    """Контекст пользователя бота"""
    clinic: ClinicMode
    state: BotState
    patient_data: Optional[dict] = None
    current_template: Optional[CurrentTemplate] = None
