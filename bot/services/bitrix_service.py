"""
Сервис интеграции с Битрикс24 для клиники ГОДОК.
Работает через входящий вебхук (REST API).
"""
import base64
import json
import os
import ssl
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import certifi
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context

try:
    import truststore
    _HAS_TRUSTSTORE = True
except ImportError:
    _HAS_TRUSTSTORE = False


class BitrixError(Exception):
    """Ошибка вызова Битрикс24 REST."""


class _LegacyTLSAdapter(HTTPAdapter):
    """
    HTTPAdapter с расширенным TLS-контекстом для совместимости с серверами,
    у которых узкий cipher-list, требуется legacy-renegotiation, или CA
    подписан корпоративным/MITM-CA (Cisco Umbrella, Kaspersky и т.п.).

    Сначала пробуем `truststore` — он использует нативный CryptoAPI Windows /
    Security.framework macOS / системные пути Linux и видит ВСЕ установленные
    в ОС CA. Это решает CERTIFICATE_VERIFY_FAILED от enterprise TLS-инспекторов.
    Если truststore не установлен, fallback: load_default_certs + certifi.
    """

    CIPHERS = "DEFAULT:@SECLEVEL=1"

    def _build_context(self) -> ssl.SSLContext:
        used_truststore = False
        if _HAS_TRUSTSTORE:
            # truststore.SSLContext наследуется от ssl.SSLContext — set_ciphers
            # и пр. работают как обычно. Trust store берётся из ОС нативно.
            ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            used_truststore = True
        else:
            ctx = create_urllib3_context(ciphers=self.CIPHERS)

        try:
            ctx.set_ciphers(self.CIPHERS)
        except ssl.SSLError:
            pass

        loaded_any = used_truststore
        if not used_truststore:
            # Fallback: системный store + Mozilla CA bundle
            try:
                ctx.load_default_certs(purpose=ssl.Purpose.SERVER_AUTH)
                loaded_any = True
            except Exception as e:
                print(f"[bitrix-tls] load_default_certs failed: {e}")
            try:
                ctx.load_verify_locations(cafile=certifi.where())
                loaded_any = True
            except Exception as e:
                print(f"[bitrix-tls] certifi load failed: {e}")

        # Escape-hatch: явное отключение или CA так и не нашлись.
        if os.getenv("BITRIX_TLS_VERIFY", "1") == "0" or not loaded_any:
            print("[bitrix-tls] ⚠️ TLS verification DISABLED (BITRIX_TLS_VERIFY=0 или CA bundle не загружен)")
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

        for opt_name in ("OP_LEGACY_SERVER_CONNECT",):
            opt = getattr(ssl, opt_name, None)
            if opt is not None:
                ctx.options |= opt
        return ctx

    def init_poolmanager(self, *args, **kwargs):
        kwargs["ssl_context"] = self._build_context()
        return super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, *args, **kwargs):
        kwargs["ssl_context"] = self._build_context()
        return super().proxy_manager_for(*args, **kwargs)


class BitrixService:
    """Минимальный клиент Б24 REST: поиск сделки, чтение, обновление."""

    GODOK_CATEGORY_ID = 10
    SICK_LEAVE_CATEGORY_ID = 14  # Воронка «БОЛЬНИЧНЫЕ ЛИСТЫ»
    DOCTOR_FIELD_CODE = "UF_CRM_1601396897"  # «Назначенный врач» (строка)
    POSITION_FIELD_CODE = "UF_CRM_1601396588"  # «Должность пациента»
    COMPLAINTS_FIELD_CODE = "UF_CRM_1601394859"  # «Жалобы пациента»
    # Ретраи на 5xx и сетевые сбои: попытки и backoff (секунды).
    RETRY_BACKOFFS = (1, 2, 4, 8)
    RETRY_STATUSES = frozenset({500, 502, 503, 504})

    def __init__(self, webhook_url: str, fields_map_path: Optional[Path] = None):
        if not webhook_url:
            raise ValueError("BITRIX_WEBHOOK_URL не задан")
        self.webhook = webhook_url.rstrip("/")
        self._session = requests.Session()
        # Кастомный TLS-адаптер: расширенные шифры + legacy-renegotiation.
        # Нужен для серверов Bitrix24, где дефолтный Python/OpenSSL получает
        # SSLV3_ALERT_HANDSHAKE_FAILURE.
        self._session.mount("https://", _LegacyTLSAdapter())

        if fields_map_path is None:
            fields_map_path = Path(__file__).parent.parent.parent / "data" / "bitrix" / "godok_fields.json"
        self.fields_map: Dict[str, Any] = json.loads(fields_map_path.read_text(encoding="utf-8"))
        self._deal_fields_cache: Optional[Dict[str, Any]] = None
        self._photo_field_cache: Optional[str] = None
        self._eln_type_field_cache: Optional[Dict[str, Any]] = None

    def _call(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """
        POST-вызов метода REST с ретраями на 5xx/сетевые сбои.
        Возвращает содержимое `result`. На бизнес-ошибках Б24 (поле `error`
        в ответе) ретраев не делает — только проброс BitrixError.
        """
        url = f"{self.webhook}/{method}.json"
        form = self._flatten(params or {})
        last_error: Optional[str] = None

        # Всего попыток = 1 + len(RETRY_BACKOFFS); пауза перед попыткой i>0.
        for attempt in range(len(self.RETRY_BACKOFFS) + 1):
            if attempt > 0:
                delay = self.RETRY_BACKOFFS[attempt - 1]
                print(f"[bitrix] {method}: повтор через {delay}s ({last_error})")
                time.sleep(delay)
            try:
                resp = self._session.post(url, data=form, timeout=30)
            except requests.RequestException as e:
                last_error = f"сеть: {e}"
                continue

            if resp.status_code in self.RETRY_STATUSES:
                last_error = f"HTTP {resp.status_code}: {resp.text[:120]}"
                continue

            try:
                payload = resp.json()
            except ValueError as e:
                # Не-JSON ответ при не-5xx — не транзиент, не ретраим.
                raise BitrixError(
                    f"{method}: некорректный JSON (HTTP {resp.status_code}): {resp.text[:200]}"
                ) from e

            if payload.get("error"):
                raise BitrixError(
                    f"{method}: {payload.get('error')} — {payload.get('error_description', '')}"
                )
            return payload.get("result")

        raise BitrixError(f"Сетевая ошибка {method} после {len(self.RETRY_BACKOFFS) + 1} попыток: {last_error}")

    @staticmethod
    def _flatten(data: Dict[str, Any], prefix: str = "") -> List[tuple]:
        """Разворачивает вложенные словари и списки в form-формат a[b][c]=v."""
        out: List[tuple] = []
        for key, value in data.items():
            full_key = f"{prefix}[{key}]" if prefix else str(key)
            if isinstance(value, dict):
                out.extend(BitrixService._flatten(value, full_key))
            elif isinstance(value, list):
                for i, item in enumerate(value):
                    if isinstance(item, (dict, list)):
                        out.extend(BitrixService._flatten({str(i): item}, full_key))
                    else:
                        out.append((f"{full_key}[{i}]", item))
            else:
                out.append((full_key, value))
        return out

    def find_deals_by_patient(self, full_name: str, date_iso: str) -> List[Dict[str, Any]]:
        """
        Поиск сделок по фрагменту ФИО и дате осмотра в воронке ГОДОК.
        date_iso: 'YYYY-MM-DD' — ищется по BEGINDATE в пределах этих суток.
        """
        name_query = (full_name or "").strip()
        if not name_query:
            return []
        result = self._call(
            "crm.deal.list",
            {
                "filter": {
                    "%TITLE": name_query,
                    ">=BEGINDATE": f"{date_iso}T00:00:00+03:00",
                    "<=BEGINDATE": f"{date_iso}T23:59:59+03:00",
                    "CATEGORY_ID": self.GODOK_CATEGORY_ID,
                },
                "select": [
                    "ID", "TITLE", "BEGINDATE", "CLOSEDATE", "STAGE_ID",
                    "CATEGORY_ID", "ASSIGNED_BY_ID", "CONTACT_ID", "DATE_CREATE",
                ],
            },
        )
        return result or []

    def get_deal(self, deal_id: int) -> Dict[str, Any]:
        return self._call("crm.deal.get", {"id": deal_id}) or {}

    def update_deal(self, deal_id: int, fields: Dict[str, Any]) -> bool:
        """Обновление полей сделки. fields — {UF_CRM_xxx: value, ...}."""
        ok = self._call("crm.deal.update", {"id": deal_id, "fields": fields})
        return bool(ok)

    def build_update_payload(
        self,
        ai_values: Dict[str, Any],
        context_values: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Собирает финальный словарь для crm.deal.update из:
        - ai_values: {field_code: value} (от Claude),
        - context_values: {field_code: value} (дата, врач, работа, ЭЛН).
        Шаблонные поля карты — НЕ включаем (бот их не трогает).
        AI-значения проходят whitelist по ai_fields (защита от галлюцинаций).
        Context-значения формируем мы сами — берём всё, что положили туда.
        """
        payload: Dict[str, Any] = {}
        for code in self.fields_map["ai_fields"].keys():
            if code in ai_values and ai_values[code] not in (None, ""):
                payload[code] = ai_values[code]
        for code, value in (context_values or {}).items():
            if value not in (None, ""):
                payload[code] = value
        return payload

    def ai_field_specs(self) -> Dict[str, Any]:
        return self.fields_map["ai_fields"]

    def assigned_doctor(self) -> str:
        return self.fields_map.get("assigned_doctor", "")

    def list_deal_fields(self) -> Dict[str, Any]:
        """crm.deal.fields с кешем — метаданные всех полей сделки."""
        if self._deal_fields_cache is None:
            self._deal_fields_cache = self._call("crm.deal.fields") or {}
        return self._deal_fields_cache

    def find_photo_field(self, label_hint: str = "рекомендац") -> Optional[str]:
        """
        Находит UF_CRM_* поле типа 'file' с названием, содержащим label_hint.
        Приоритет: явно заданное 'photo_recommendations_field' в fields_map.
        """
        explicit = self.fields_map.get("photo_recommendations_field")
        if explicit:
            return explicit
        if self._photo_field_cache is not None:
            return self._photo_field_cache or None

        hint_lower = label_hint.lower()
        for code, meta in self.list_deal_fields().items():
            if not code.startswith("UF_CRM_"):
                continue
            if str(meta.get("type", "")).lower() != "file":
                continue
            labels = meta.get("formLabel") or meta.get("title") or ""
            if isinstance(labels, dict):
                label_text = " ".join(str(v) for v in labels.values())
            else:
                label_text = str(labels)
            if hint_lower in label_text.lower():
                self._photo_field_cache = code
                return code
        self._photo_field_cache = ""
        return None

    def find_eln_type_field(self) -> Optional[Dict[str, Any]]:
        """
        Ищет UF_CRM_* enumeration-поле для типа ЛН (отказ/первичный/продолжение).
        Возвращает {'code': UF_..., 'options': {'refusal': id|None, 'primary': id|None,
        'continuation': id|None}} или None, если поле не найдено.

        Приоритет: explicit-конфиг fields_map['eln_type_field'] = {'code': ..., 'options': {...}}.
        """
        explicit = self.fields_map.get("eln_type_field")
        if explicit and isinstance(explicit, dict) and explicit.get("code"):
            return explicit
        if self._eln_type_field_cache is not None:
            return self._eln_type_field_cache or None

        # Ключевые слова в названии поля и в его опциях.
        label_kws = ("листок нетруд", "лн ", "нетрудоспособ", "продление")
        opt_keywords = {
            "refusal": ("отказ",),
            "primary": ("первичн",),
            "continuation": ("продолж", "продлен"),
        }

        for code, meta in self.list_deal_fields().items():
            if not code.startswith("UF_CRM_"):
                continue
            if str(meta.get("type", "")).lower() != "enumeration":
                continue
            labels = meta.get("formLabel") or meta.get("title") or ""
            if isinstance(labels, dict):
                label_text = " ".join(str(v) for v in labels.values())
            else:
                label_text = str(labels)
            label_low = label_text.lower()
            if not any(kw in label_low for kw in label_kws):
                continue

            options: Dict[str, Optional[int]] = {"refusal": None, "primary": None, "continuation": None}
            for item in meta.get("items", []) or []:
                value_low = str(item.get("VALUE", "")).lower()
                try:
                    item_id = int(item.get("ID"))
                except (TypeError, ValueError):
                    continue
                for kind, kws in opt_keywords.items():
                    if options[kind] is None and any(kw in value_low for kw in kws):
                        options[kind] = item_id
                        break

            # Запоминаем только если нашли хотя бы одну подходящую опцию.
            if any(v is not None for v in options.values()):
                self._eln_type_field_cache = {"code": code, "options": options}
                return self._eln_type_field_cache

        self._eln_type_field_cache = {}
        return None

    @staticmethod
    def _encode_file_for_bitrix(filename: str, content: bytes) -> List[str]:
        """Формат значения файла в Б24: [filename, base64_content]."""
        return [filename, base64.b64encode(content).decode("ascii")]

    def upload_photos_to_deal(
        self,
        deal_id: int,
        field_code: str,
        photos: List[Tuple[str, bytes]],
    ) -> bool:
        """
        Загружает список фото в файловое поле сделки. photos — [(filename, bytes), ...].
        Мультифайловое поле принимает список значений вида [filename, base64].
        """
        if not photos:
            return True
        encoded = [self._encode_file_for_bitrix(name, data) for name, data in photos]
        # Для мультифайлового поля — список; для одинарного — первое значение.
        meta = self.list_deal_fields().get(field_code, {})
        is_multiple = bool(meta.get("isMultiple"))
        value: Any = encoded if is_multiple else encoded[0]
        ok = self._call("crm.deal.update", {"id": deal_id, "fields": {field_code: value}})
        return bool(ok)

    def find_new_doctor_deals(
        self,
        doctor_name_fragment: str,
        since_id: int = 0,
        category_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Сделки в воронке «БОЛЬНИЧНЫЕ ЛИСТЫ» (CATEGORY_ID=14 по умолчанию),
        назначенные на доктора по подстроке в поле UF_CRM_1601396897, c ID > since_id.
        """
        cat = self.SICK_LEAVE_CATEGORY_ID if category_id is None else category_id
        result = self._call(
            "crm.deal.list",
            {
                "filter": {
                    "CATEGORY_ID": cat,
                    f"%{self.DOCTOR_FIELD_CODE}": doctor_name_fragment,
                    ">ID": since_id,
                },
                "order": {"ID": "ASC"},
                "select": [
                    "ID", "TITLE", "DATE_CREATE", "BEGINDATE", "STAGE_ID",
                    self.DOCTOR_FIELD_CODE,
                    "UF_CRM_1601395840",  # диагноз
                    self.POSITION_FIELD_CODE,  # должность
                    "UF_CRM_1601396599",  # место работы
                    "UF_CRM_1601396409",  # ЭЛН с
                    "UF_CRM_1601396467",  # ЭЛН по
                    self.COMPLAINTS_FIELD_CODE,  # жалобы (флаг — был ли AI-flow)
                ],
            },
        )
        return result or []

    def get_max_deal_id_for_doctor(
        self,
        doctor_name_fragment: str,
        category_id: Optional[int] = None,
    ) -> int:
        """Текущий максимальный ID сделки в воронке для инициализации поллера."""
        cat = self.SICK_LEAVE_CATEGORY_ID if category_id is None else category_id
        result = self._call(
            "crm.deal.list",
            {
                "filter": {
                    "CATEGORY_ID": cat,
                    f"%{self.DOCTOR_FIELD_CODE}": doctor_name_fragment,
                },
                "order": {"ID": "DESC"},
                "select": ["ID"],
                "start": 0,
            },
        ) or []
        if not result:
            return 0
        try:
            return int(result[0]["ID"])
        except (KeyError, TypeError, ValueError):
            return 0
