"""
Сервис интеграции с Битрикс24 для клиники ГОДОК.
Работает через входящий вебхук (REST API).
"""
import base64
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests


class BitrixError(Exception):
    """Ошибка вызова Битрикс24 REST."""


class BitrixService:
    """Минимальный клиент Б24 REST: поиск сделки, чтение, обновление."""

    GODOK_CATEGORY_ID = 10
    # Ретраи на 5xx и сетевые сбои: попытки и backoff (секунды).
    RETRY_BACKOFFS = (1, 2, 4, 8)
    RETRY_STATUSES = frozenset({500, 502, 503, 504})

    def __init__(self, webhook_url: str, fields_map_path: Optional[Path] = None):
        if not webhook_url:
            raise ValueError("BITRIX_WEBHOOK_URL не задан")
        self.webhook = webhook_url.rstrip("/")
        self._session = requests.Session()

        if fields_map_path is None:
            fields_map_path = Path(__file__).parent.parent.parent / "data" / "bitrix" / "godok_fields.json"
        self.fields_map: Dict[str, Any] = json.loads(fields_map_path.read_text(encoding="utf-8"))
        self._deal_fields_cache: Optional[Dict[str, Any]] = None
        self._photo_field_cache: Optional[str] = None

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
        Шаблонные и ручные поля НЕ включаются.
        """
        payload: Dict[str, Any] = {}
        for code in self.fields_map["ai_fields"].keys():
            if code in ai_values and ai_values[code] not in (None, ""):
                payload[code] = ai_values[code]
        for code in self.fields_map["context_fields"].keys():
            if code in context_values and context_values[code] not in (None, ""):
                payload[code] = context_values[code]
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
