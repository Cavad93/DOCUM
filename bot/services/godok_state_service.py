"""
Перситентное состояние для ГОДОК-поллера:
- doctor_chat_id: Telegram chat_id, куда слать уведомления о новых заявках.
- last_seen_deal_id: максимальный обработанный ID сделки в воронке «БОЛЬНИЧНЫЕ ЛИСТЫ»,
  чтобы при рестарте не дублировать старые.
"""
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional


class GodokStateService:
    def __init__(self, state_path: Optional[Path] = None):
        if state_path is None:
            state_path = Path(__file__).parent.parent.parent / "data" / "bitrix" / "godok_state.json"
        self.path = state_path
        self._data: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"[godok-state] не удалось прочитать {self.path}: {e} — сбрасываю")
        return {"doctor_chat_id": None, "last_seen_deal_id": 0}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(str(tmp), str(self.path))

    @property
    def doctor_chat_id(self) -> Optional[int]:
        v = self._data.get("doctor_chat_id")
        return int(v) if v is not None else None

    def set_doctor_chat_id(self, value: int) -> None:
        self._data["doctor_chat_id"] = int(value)
        self._save()

    @property
    def last_seen_deal_id(self) -> int:
        try:
            return int(self._data.get("last_seen_deal_id") or 0)
        except (TypeError, ValueError):
            return 0

    def set_last_seen_deal_id(self, value: int) -> None:
        self._data["last_seen_deal_id"] = int(value)
        self._save()
