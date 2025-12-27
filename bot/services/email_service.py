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
            # Создаём структуру с разными клиниками и типами получателей
            default_data = {
                "dinastiya": {
                    "all": [],  # Получают все осмотры
                    "eln_only": []  # Получают только осмотры с ЭЛН (исключая отказы)
                },
                "pskp": {
                    "all": [],
                    "eln_only": []
                }
            }
            self.recipients_file.write_text(json.dumps(default_data, ensure_ascii=False, indent=2), encoding="utf-8")

    def get_recipients(self, clinic: str = "dinastiya", recipient_type: str = "all") -> List[str]:
        """
        Получить список email получателей для клиники

        Args:
            clinic: Название клиники ("dinastiya" или "pskp")
            recipient_type: Тип получателей ("all" - все осмотры, "eln_only" - только с ЭЛН)

        Returns:
            Список email адресов для указанной клиники и типа
        """
        try:
            data = json.loads(self.recipients_file.read_text(encoding="utf-8"))

            # Поддержка старого формата (простой список)
            if isinstance(data, list):
                # Мигрируем в новый формат
                new_data = {
                    "dinastiya": {"all": data, "eln_only": []},
                    "pskp": {"all": [], "eln_only": []}
                }
                self.recipients_file.write_text(json.dumps(new_data, ensure_ascii=False, indent=2), encoding="utf-8")
                return data if clinic == "dinastiya" and recipient_type == "all" else []

            # Поддержка среднего формата (словарь со списками)
            clinic_data = data.get(clinic, [])
            if isinstance(clinic_data, list):
                # Мигрируем в новейший формат
                new_data = {}
                for key, value in data.items():
                    if isinstance(value, list):
                        new_data[key] = {"all": value, "eln_only": []}
                    else:
                        new_data[key] = value
                self.recipients_file.write_text(json.dumps(new_data, ensure_ascii=False, indent=2), encoding="utf-8")
                return clinic_data if recipient_type == "all" else []

            # Новейший формат (словарь по клиникам и типам)
            clinic_recipients = data.get(clinic, {})
            return clinic_recipients.get(recipient_type, [])
        except Exception as e:
            print(f"Ошибка чтения списка получателей: {e}")
            return []

    def add_recipient(self, email: str, clinic: str = "dinastiya", recipient_type: str = "all") -> bool:
        """
        Добавить email получателя для клиники

        Args:
            email: Email адрес
            clinic: Название клиники ("dinastiya" или "pskp")
            recipient_type: Тип получателей ("all" - все осмотры, "eln_only" - только с ЭЛН)

        Returns:
            True если успешно добавлен, False если уже существует
        """
        try:
            # Читаем всю структуру
            data = json.loads(self.recipients_file.read_text(encoding="utf-8"))

            # Поддержка миграции старого формата (простой список)
            if isinstance(data, list):
                data = {
                    "dinastiya": {"all": data, "eln_only": []},
                    "pskp": {"all": [], "eln_only": []}
                }

            # Поддержка среднего формата (словарь со списками)
            clinic_data = data.get(clinic, [])
            if isinstance(clinic_data, list):
                new_data = {}
                for key, value in data.items():
                    if isinstance(value, list):
                        new_data[key] = {"all": value, "eln_only": []}
                    else:
                        new_data[key] = value
                data = new_data

            # Получаем структуру для клиники
            if clinic not in data:
                data[clinic] = {"all": [], "eln_only": []}

            clinic_recipients = data[clinic]
            if not isinstance(clinic_recipients, dict):
                clinic_recipients = {"all": [], "eln_only": []}
                data[clinic] = clinic_recipients

            # Получаем список для типа
            recipients = clinic_recipients.get(recipient_type, [])
            email = email.strip().lower()

            if email in recipients:
                return False

            recipients.append(email)
            clinic_recipients[recipient_type] = recipients
            data[clinic] = clinic_recipients
            self.recipients_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            return True
        except Exception as e:
            print(f"Ошибка добавления получателя: {e}")
            return False

    def remove_recipient(self, email: str, clinic: str = "dinastiya", recipient_type: str = "all") -> bool:
        """
        Удалить email получателя для клиники

        Args:
            email: Email адрес
            clinic: Название клиники ("dinastiya" или "pskp")
            recipient_type: Тип получателей ("all" - все осмотры, "eln_only" - только с ЭЛН)

        Returns:
            True если успешно удалён, False если не найден
        """
        try:
            # Читаем всю структуру
            data = json.loads(self.recipients_file.read_text(encoding="utf-8"))

            # Поддержка миграции старого формата (простой список)
            if isinstance(data, list):
                data = {
                    "dinastiya": {"all": data, "eln_only": []},
                    "pskp": {"all": [], "eln_only": []}
                }

            # Поддержка среднего формата (словарь со списками)
            clinic_data = data.get(clinic, [])
            if isinstance(clinic_data, list):
                new_data = {}
                for key, value in data.items():
                    if isinstance(value, list):
                        new_data[key] = {"all": value, "eln_only": []}
                    else:
                        new_data[key] = value
                data = new_data

            # Получаем структуру для клиники
            clinic_recipients = data.get(clinic, {})
            if not isinstance(clinic_recipients, dict):
                return False

            # Получаем список для типа
            recipients = clinic_recipients.get(recipient_type, [])
            email = email.strip().lower()

            if email not in recipients:
                return False

            recipients.remove(email)
            clinic_recipients[recipient_type] = recipients
            data[clinic] = clinic_recipients
            self.recipients_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            return True
        except Exception as e:
            print(f"Ошибка удаления получателя: {e}")
            return False

    async def send_document(
        self,
        file_path: str,
        patient_name: str,
        examination_date: str,
        clinic: str = "dinastiya",
        doctor_name: str = "Гаджимурадлы Д.Д",
        photos: Optional[List[bytes]] = None,
        has_eln: bool = True
    ) -> tuple[bool, str]:
        """
        Отправить документ по email всем получателям клиники

        Args:
            file_path: Путь к .docx файлу
            patient_name: ФИО пациента (будет сокращено)
            examination_date: Дата осмотра
            clinic: Название клиники ("dinastiya" или "pskp")
            doctor_name: ФИО врача (уже сокращённое)
            photos: Список фото осмотра (опционально, только для Династии)
            has_eln: Есть ли ЭЛН у пациента (False если отказ от ЭЛН)

        Returns:
            Tuple (успех, сообщение об ошибке или None)
        """
        # Получаем обычных получателей (получают все осмотры)
        all_recipients = self.get_recipients(clinic, recipient_type="all")

        # Получаем ЭЛН-получателей (получают только осмотры с ЭЛН)
        eln_recipients = []
        if has_eln:
            eln_recipients = self.get_recipients(clinic, recipient_type="eln_only")

        # Объединяем списки и удаляем дубликаты
        recipients = list(set(all_recipients + eln_recipients))

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
            photos_info = f"\nФото осмотра: {len(photos)} шт." if photos else ""
            body = f"""Медицинский осмотр

Пациент: {patient_name}
Дата осмотра: {examination_date}
Врач: {doctor_name}{photos_info}

Документ во вложении.
"""
            msg.attach(MIMEText(body, 'plain', 'utf-8'))

            # Формируем имя файла для вложения (по ФИО пациента)
            safe_name = "".join(c if c.isalnum() or c == ' ' else '_' for c in patient_name)
            filename = f"{safe_name}_ГОТОВЫЙ_ОСМОТР_{examination_date.replace('.', '-')}.docx"

            # Прикрепляем файл документа
            with open(file_path, 'rb') as f:
                attachment = MIMEApplication(f.read(), _subtype="docx")
                attachment.add_header('Content-Disposition', 'attachment', filename=filename)
                msg.attach(attachment)

            # Прикрепляем фото осмотра (если есть)
            if photos:
                for i, photo_bytes in enumerate(photos, 1):
                    photo_attachment = MIMEApplication(photo_bytes, _subtype="jpeg")
                    photo_filename = f"{safe_name}_фото_{i}.jpg"
                    photo_attachment.add_header('Content-Disposition', 'attachment', filename=photo_filename)
                    msg.attach(photo_attachment)
                print(f"✓ Прикреплено {len(photos)} фото осмотра")

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

    async def send_batch_documents(
        self,
        documents: List[dict],
        clinic: str = "dinastiya",
        doctor_name: str = "Гаджимурадлы Д.Д",
        has_eln: bool = True
    ) -> tuple[bool, str]:
        """
        Отправить несколько документов одним письмом (пакетная отправка для ПСКП)

        Args:
            documents: Список документов, каждый содержит:
                - file_path: путь к .docx файлу
                - patient_name: ФИО пациента
                - examination_date: дата осмотра
            clinic: Название клиники ("dinastiya" или "pskp")
            doctor_name: ФИО врача (уже сокращённое)
            has_eln: Есть ли хотя бы один документ с ЭЛН

        Returns:
            Tuple (успех, сообщение об ошибке или None)
        """
        # Получаем обычных получателей (получают все осмотры)
        all_recipients = self.get_recipients(clinic, recipient_type="all")

        # Получаем ЭЛН-получателей (получают только осмотры с ЭЛН)
        eln_recipients = []
        if has_eln:
            eln_recipients = self.get_recipients(clinic, recipient_type="eln_only")

        # Объединяем списки и удаляем дубликаты
        recipients = list(set(all_recipients + eln_recipients))

        if not recipients:
            return False, "Нет настроенных получателей. Используйте /add_email"

        if not documents:
            return False, "Нет документов для отправки"

        # Проверяем существование всех файлов
        for doc in documents:
            if not Path(doc["file_path"]).exists():
                return False, f"Файл не найден: {doc['file_path']}"

        try:
            # Формируем тему письма
            today = datetime.now().strftime("%d.%m.%Y")
            subject = f"Пакет осмотров {today} {doctor_name} ({len(documents)} шт.)"

            # Создаём письмо
            msg = MIMEMultipart()
            msg['From'] = self.smtp_user
            msg['To'] = ', '.join(recipients)
            msg['Subject'] = subject

            # Формируем список пациентов для тела письма
            patients_list = []
            for i, doc in enumerate(documents, 1):
                short_name = self._shorten_name(doc["patient_name"])
                patients_list.append(f"{i}. {short_name} ({doc['examination_date']})")

            # Текст письма
            body = f"""Пакет медицинских осмотров

Врач: {doctor_name}
Дата отправки: {today}
Количество осмотров: {len(documents)}

Список пациентов:
{chr(10).join(patients_list)}

Документы во вложении.
"""
            msg.attach(MIMEText(body, 'plain', 'utf-8'))

            # Прикрепляем все документы
            for i, doc in enumerate(documents, 1):
                file_path = doc["file_path"]
                patient_name = doc["patient_name"]
                examination_date = doc["examination_date"]

                # Формируем имя файла для вложения
                safe_name = "".join(c if c.isalnum() or c == ' ' else '_' for c in patient_name)
                filename = f"{i}_{safe_name}_ГОТОВЫЙ_ОСМОТР_{examination_date.replace('.', '-')}.docx"

                # Прикрепляем файл
                with open(file_path, 'rb') as f:
                    attachment = MIMEApplication(f.read(), _subtype="docx")
                    attachment.add_header('Content-Disposition', 'attachment', filename=filename)
                    msg.attach(attachment)

            # Отправляем через Yandex SMTP
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.starttls()
                server.login(self.smtp_user, self.smtp_password)
                server.send_message(msg)

            print(f"✓ Пакет из {len(documents)} документов отправлен: {subject}")
            print(f"  Получатели: {', '.join(recipients)}")
            return True, None

        except Exception as e:
            error_msg = f"Ошибка отправки пакета: {e}"
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
