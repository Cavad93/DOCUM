"""Сервис для отправки email через Yandex SMTP"""
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from pathlib import Path
from typing import List, Optional
from datetime import datetime


class EmailService:
    """Сервис для отправки документов по email"""

    def __init__(self, smtp_host: str, smtp_port: int, smtp_user: str, smtp_password: str):
        """
        Инициализация сервиса

        Args:
            smtp_host: SMTP сервер (например, smtp.yandex.ru)
            smtp_port: Порт SMTP (обычно 587 для TLS)
            smtp_user: Email отправителя
            smtp_password: Пароль от email
        """
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_user = smtp_user
        self.smtp_password = smtp_password
        self.recipients_file = Path("./data/email_recipients.json")
        self._ensure_recipients_file()

    def _ensure_recipients_file(self) -> None:
        """Создаёт файл с получателями если его нет"""
        if not self.recipients_file.exists():
            self.recipients_file.parent.mkdir(parents=True, exist_ok=True)
            self.recipients_file.write_text(json.dumps([], ensure_ascii=False, indent=2), encoding="utf-8")

    def get_recipients(self) -> List[str]:
        """
        Получить список email получателей

        Returns:
            Список email адресов
        """
        try:
            data = json.loads(self.recipients_file.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception as e:
            print(f"Ошибка чтения списка получателей: {e}")
            return []

    def add_recipient(self, email: str) -> bool:
        """
        Добавить email получателя

        Args:
            email: Email адрес

        Returns:
            True если успешно добавлен, False если уже существует
        """
        try:
            recipients = self.get_recipients()
            email = email.strip().lower()

            if email in recipients:
                return False

            recipients.append(email)
            self.recipients_file.write_text(json.dumps(recipients, ensure_ascii=False, indent=2), encoding="utf-8")
            return True
        except Exception as e:
            print(f"Ошибка добавления получателя: {e}")
            return False

    def remove_recipient(self, email: str) -> bool:
        """
        Удалить email получателя

        Args:
            email: Email адрес

        Returns:
            True если успешно удалён, False если не найден
        """
        try:
            recipients = self.get_recipients()
            email = email.strip().lower()

            if email not in recipients:
                return False

            recipients.remove(email)
            self.recipients_file.write_text(json.dumps(recipients, ensure_ascii=False, indent=2), encoding="utf-8")
            return True
        except Exception as e:
            print(f"Ошибка удаления получателя: {e}")
            return False

    async def send_document(
        self,
        file_path: str,
        patient_name: str,
        examination_date: str,
        doctor_name: str = "Гаджимурадлы Д.Д"
    ) -> tuple[bool, str]:
        """
        Отправить документ по email всем получателям

        Args:
            file_path: Путь к .docx файлу
            patient_name: ФИО пациента (будет сокращено)
            examination_date: Дата осмотра
            doctor_name: ФИО врача (уже сокращённое)

        Returns:
            Tuple (успех, сообщение об ошибке или None)
        """
        recipients = self.get_recipients()

        if not recipients:
            return False, "Нет настроенных получателей. Используйте /add_email"

        if not Path(file_path).exists():
            return False, f"Файл не найден: {file_path}"

        try:
            # Сокращаем ФИО пациента (Иванов Иван Иванович -> Иванов И.И.)
            short_patient_name = self._shorten_name(patient_name)

            # Формируем тему письма
            subject = f"{short_patient_name} {examination_date} {doctor_name}"

            # Создаём письмо
            msg = MIMEMultipart()
            msg['From'] = self.smtp_user
            msg['To'] = ', '.join(recipients)
            msg['Subject'] = subject

            # Текст письма
            body = f"""Медицинский осмотр

Пациент: {patient_name}
Дата осмотра: {examination_date}
Врач: {doctor_name}

Документ во вложении.
"""
            msg.attach(MIMEText(body, 'plain', 'utf-8'))

            # Прикрепляем файл
            with open(file_path, 'rb') as f:
                attachment = MIMEApplication(f.read(), _subtype="docx")
                attachment.add_header('Content-Disposition', 'attachment', filename=Path(file_path).name)
                msg.attach(attachment)

            # Отправляем через Yandex SMTP
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.starttls()
                server.login(self.smtp_user, self.smtp_password)
                server.send_message(msg)

            print(f"✓ Email отправлен: {subject}")
            print(f"  Получатели: {', '.join(recipients)}")
            return True, None

        except Exception as e:
            error_msg = f"Ошибка отправки email: {e}"
            print(error_msg)
            return False, error_msg

    def _shorten_name(self, full_name: str) -> str:
        """
        Сокращает ФИО (Иванов Иван Иванович -> Иванов И.И.)

        Args:
            full_name: Полное ФИО

        Returns:
            Сокращённое ФИО
        """
        parts = full_name.strip().split()

        if len(parts) == 0:
            return full_name
        elif len(parts) == 1:
            return parts[0]
        elif len(parts) == 2:
            return f"{parts[0]} {parts[1][0]}."
        else:
            # 3 и более частей
            return f"{parts[0]} {parts[1][0]}.{parts[2][0]}."
