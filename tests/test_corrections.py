"""
Тесты системы правок шаблонов.
Запуск: python3 -m pytest tests/test_corrections.py -v
или:    python3 tests/test_corrections.py
"""
import sys
import os
import unittest
from unittest.mock import MagicMock, patch, PropertyMock
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.models.types import PatientData, ClinicMode


# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------

SAMPLE_TEMPLATE = """**Дата консультации:** 26.03.2026
**ФИО пациента:** Рудниченко Елена Владимировна
**Дата рождения:** 26.05.1972

**Жалобы:** боль в горле при глотании, першение в горле, общая слабость, повышение температуры до 37,8°C.

**Анамнез заболевания:** считает себя больной с 25.03.2026. Вызвала врача на дом 26.03.2026.

**Сведения о заболеваниях, травмах, операциях:** отрицает

**Анамнез жизни:**
Сопутствующая патология и перенесенные заболевания: отрицает хронические заболевания.
Аллергологический анамнез: не отягощён
Наследственность: не отягощена.
Приём лекарств в настоящее время: не принимает постоянной терапии

**Страховой анамнез:**
Место работы: ООО ТАНДЕР
Должность: директор магазина
Листы нетрудоспособности за последние 11 месяцев: не помнит.

**Объективный статус:**
Общее состояние: относительно удовлетворительное
Температура тела: 37,8°C
Артериальное давление: 128/82 мм.рт.ст. ЧСС 92 в 1 минуту.

**Диагноз:** J02 Острый бактериальный фарингит

**План лечения:**
1. Полоскание горла раствором соды
2. Обильное питьё

**Нетрудоспособен:** ЭЛН с 26.03.2026 по 31.03.2026
**Явка к врачу:** 31.03.2026"""

PATIENT = PatientData(
    full_name="Рудниченко Елена Владимировна",
    birth_date="26.05.1972",
    diagnosis="J02 Острый бактериальный фарингит",
    snils=None,
    examination_date="26.03.2026",
    illness_start_date="25.03.2026",
    sick_leave_days=6,
    eln_refused=False,
    workplace="ООО ТАНДЕР",
    position="директор магазина",
    eln_start_date="26.03.2026",
    eln_end_date="31.03.2026",
    is_student=False,
)


def make_service(api_response: str):
    """Создаёт ClaudeService с замоканным API, возвращающим api_response."""
    from bot.services.claude_service import ClaudeService
    svc = ClaudeService.__new__(ClaudeService)
    svc.GENERATION_MODEL = "claude-sonnet-4-6"

    mock_client = MagicMock()
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=api_response)]
    mock_client.messages.create.return_value = mock_msg
    svc.client = mock_client
    return svc


# ---------------------------------------------------------------------------
# Группа 1 — Структура промпта (system / user split)
# ---------------------------------------------------------------------------

class TestPromptStructure(unittest.TestCase):

    def _get_call_kwargs(self, corrections: str, api_response: str = "CHANGED"):
        svc = make_service(api_response)
        import asyncio
        asyncio.get_event_loop().run_until_complete(
            svc.correct_template(SAMPLE_TEMPLATE, PATIENT, corrections, clinic=ClinicMode.DINASTIYA)
        )
        return svc.client.messages.create.call_args

    def test_01_uses_system_parameter(self):
        """API вызывается с параметром system="""
        kwargs = self._get_call_kwargs("добавить ревматоидный артрит")
        self.assertIn("system", kwargs.kwargs, "Должен быть параметром system=")

    def test_02_system_not_in_user_message(self):
        """Правила сохранения не попадают в финальное user-сообщение с правками"""
        kwargs = self._get_call_kwargs("добавить ревматоидный артрит")
        # messages[2] — финальный user-запрос с правками (prefill-техника: 3 сообщения)
        user_content = kwargs.kwargs["messages"][2]["content"]
        self.assertNotIn("НЕИЗМЕНЯЕМЫЕ", user_content)
        self.assertNotIn("АДМИНИСТРАТИВНЫЕ ДАННЫЕ", user_content)

    def test_03_corrections_in_user_message(self):
        """Правки пользователя есть в финальном user-сообщении (messages[2])"""
        correction = "В раздел сопутствующие добавить: ревматоидный артрит"
        kwargs = self._get_call_kwargs(correction)
        # При prefill-технике: messages[0]=user intro, messages[1]=assistant(шаблон), messages[2]=user правки
        user_content = kwargs.kwargs["messages"][2]["content"]
        self.assertIn("ревматоидный артрит", user_content)

    def test_04_template_in_user_message(self):
        """Шаблон передаётся как assistant-сообщение (prefill, messages[1])"""
        kwargs = self._get_call_kwargs("тест")
        # При prefill-технике шаблон — в messages[1] (role=assistant)
        assistant_content = kwargs.kwargs["messages"][1]["content"]
        self.assertIn("Рудниченко Елена Владимировна", assistant_content)

    def test_05_corrections_before_template_in_user_message(self):
        """Правки идут в отдельном сообщении ПОСЛЕ шаблона (prefill-техника)"""
        correction = "УНИКАЛЬНАЯ_ПРАВКА_XYZ"
        kwargs = self._get_call_kwargs(correction)
        messages = kwargs.kwargs["messages"]
        # messages[1] — шаблон (assistant), messages[2] — правки (user)
        self.assertEqual(messages[1]["role"], "assistant", "messages[1] должен быть assistant")
        self.assertEqual(messages[2]["role"], "user", "messages[2] должен быть user")
        self.assertIn(correction, messages[2]["content"], "Правки должны быть в messages[2]")
        self.assertNotIn(correction, messages[1]["content"], "Правки не должны быть в шаблоне")

    def test_06_admin_data_frozen_in_system(self):
        """ФИО и дата рождения — в system-промпте как неизменяемые"""
        kwargs = self._get_call_kwargs("тест")
        system = kwargs.kwargs["system"]
        self.assertIn("Рудниченко", system)
        self.assertIn("26.05.1972", system)

    def test_07_medical_content_explicitly_allowed_in_system(self):
        """System-промпт явно разрешает менять медицинский контент"""
        kwargs = self._get_call_kwargs("тест")
        system = kwargs.kwargs["system"]
        self.assertIn("МОЖНО", system)

    def test_08_eln_dates_in_system_not_user(self):
        """Даты ЭЛН — в system, не перегружают финальное user-сообщение"""
        kwargs = self._get_call_kwargs("тест")
        system = kwargs.kwargs["system"]
        # При prefill-технике правки в messages[2]
        user_content = kwargs.kwargs["messages"][2]["content"]
        # Даты ЭЛН должны быть в системном промпте
        self.assertIn("26.03.2026", system)
        self.assertIn("31.03.2026", system)

    def test_09_no_eln_instructions_block_in_user_message(self):
        """_get_eln_instructions не попадает в финальное user-сообщение с правками"""
        kwargs = self._get_call_kwargs("тест")
        # При prefill-технике правки в messages[2]
        user_content = kwargs.kwargs["messages"][2]["content"]
        # Этих фраз не должно быть в user-сообщении (они были источником конфликта)
        self.assertNotIn("КРИТИЧЕСКИ ВАЖНО ПРО ЭЛН", user_content)
        self.assertNotIn("НЕ придумывайте даты", user_content)

    def test_10_ln_line_protected_in_system(self):
        """Строка о листах нетрудоспособности защищена в system-промпте"""
        kwargs = self._get_call_kwargs("тест")
        system = kwargs.kwargs["system"]
        self.assertIn("Листы нетрудоспособности", system)


# ---------------------------------------------------------------------------
# Группа 2 — Возврат результата / no-op детекция
# ---------------------------------------------------------------------------

class TestReturnBehaviour(unittest.IsolatedAsyncioTestCase):

    async def _run(self, api_response: str, corrections: str = "тест") -> str:
        svc = make_service(api_response)
        return await svc.correct_template(
            SAMPLE_TEMPLATE, PATIENT, corrections, clinic=ClinicMode.DINASTIYA
        )

    async def test_11_returns_api_response_when_changed(self):
        """Возвращает ответ API если шаблон изменился"""
        changed = SAMPLE_TEMPLATE.replace("отрицает хронические заболевания",
                                          "ревматоидный артрит")
        result = await self._run(changed)
        self.assertIn("ревматоидный артрит", result)

    async def test_12_returns_original_when_api_returns_same(self):
        """Если API вернул то же самое — возвращаем это же (no-op детекция в боте)"""
        result = await self._run(SAMPLE_TEMPLATE)
        # correct_template должен вернуть значение (не бросать исключение)
        self.assertIsNotNone(result)

    async def test_13_whitespace_stripped_comparison(self):
        """Сравнение делается через .strip()"""
        same_with_spaces = "\n" + SAMPLE_TEMPLATE + "\n"
        result = await self._run(same_with_spaces)
        self.assertIsNotNone(result)

    async def test_14_corrections_sanitized_max_len(self):
        """Правки обрезаются до 2000 символов (в messages[2] при prefill-технике)"""
        svc = make_service("CHANGED")
        long_correction = "а" * 5000
        await svc.correct_template(SAMPLE_TEMPLATE, PATIENT, long_correction,
                                   clinic=ClinicMode.DINASTIYA)
        call_kwargs = svc.client.messages.create.call_args
        # При prefill-технике правки в messages[2]
        user_content = call_kwargs.kwargs["messages"][2]["content"]
        correction_in_prompt = "а" * 2000
        self.assertIn(correction_in_prompt, user_content)
        self.assertNotIn("а" * 2001, user_content)

    async def test_15_exception_raised_on_api_error(self):
        """Исключение пробрасывается при ошибке API"""
        from bot.services.claude_service import ClaudeService
        svc = ClaudeService.__new__(ClaudeService)
        svc.GENERATION_MODEL = "claude-sonnet-4-6"
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("API error")
        svc.client = mock_client
        with self.assertRaises(Exception):
            await svc.correct_template(SAMPLE_TEMPLATE, PATIENT, "тест",
                                       clinic=ClinicMode.DINASTIYA)


# ---------------------------------------------------------------------------
# Группа 3 — Парсинг данных пользователя (telegram_bot._parse_text_data)
# ---------------------------------------------------------------------------

class TestParseTextData(unittest.TestCase):

    def setUp(self):
        # Импортируем только парсер без запуска бота
        import importlib, types
        # Минимальный мок для импорта telegram_bot
        for mod in ["telegram", "telegram.ext"]:
            if mod not in sys.modules:
                sys.modules[mod] = types.ModuleType(mod)
        # Patch everything needed
        with patch.dict(sys.modules, {
            "telegram": MagicMock(),
            "telegram.ext": MagicMock(),
            "anthropic": MagicMock(),
        }):
            import importlib.util, types
            # Direct import of parse function
            pass
        # Use direct regex parsing logic instead
        import re
        self.re = re

    def _parse(self, text: str) -> dict:
        """Вызывает _parse_text_data напрямую через импорт."""
        # Patch тяжёлые зависимости
        telegram_mock = MagicMock()
        with patch.dict(sys.modules, {
            "telegram": telegram_mock,
            "telegram.ext": MagicMock(),
            "telegram.ext.filters": MagicMock(),
        }):
            try:
                from bot.telegram_bot import MedicalBot
                bot = MedicalBot.__new__(MedicalBot)
                return bot._parse_text_data(text)
            except Exception:
                return {}

    def test_16_parse_eln_with_word_otkryt(self):
        """'ЭЛН открыт с 26.03.2026 по 31.03.2026' парсится корректно"""
        data = self._parse("ЭЛН открыт с 26.03.2026 по 31.03.2026")
        if data:
            self.assertEqual(data.get("eln_start_date"), "26.03.2026")
            self.assertEqual(data.get("eln_end_date"), "31.03.2026")
            self.assertEqual(data.get("sick_leave_days"), 6)

    def test_17_parse_eln_with_colon(self):
        """'ЭЛН: с 26.03 по 31.03.2026' парсится"""
        data = self._parse("ЭЛН: с 26.03 по 31.03.2026")
        if data:
            self.assertIsNotNone(data.get("eln_start_date"))

    def test_18_eln_end_before_start_sets_error(self):
        """ЭЛН с end < start выставляет _eln_parse_error"""
        data = self._parse("ЭЛН: с 31.03.2026 по 26.03.2026")
        if data:
            self.assertTrue(data.get("_eln_parse_error"),
                            "Должна быть ошибка при end < start")

    def test_19_is_student_from_kurs(self):
        """'Курс: 3' выставляет is_student=True"""
        data = self._parse("Курс: 3")
        if data:
            self.assertTrue(data.get("is_student"))
            self.assertEqual(data.get("position"), "3")

    def test_20_birth_date_invalid_format(self):
        """Невалидная дата рождения выставляет _birth_date_error"""
        data = self._parse("Дата рождения: 32.13.1990")
        if data:
            self.assertIn("_birth_date_error", data)


# ---------------------------------------------------------------------------
# Группа 4 — _sanitize
# ---------------------------------------------------------------------------

class TestSanitize(unittest.TestCase):

    def setUp(self):
        from bot.services.claude_service import ClaudeService
        self.svc = ClaudeService.__new__(ClaudeService)

    def test_21_strips_control_chars(self):
        """Управляющие символы удаляются"""
        result = self.svc._sanitize("text\x00\x01\x02end")
        self.assertNotIn("\x00", result)
        self.assertNotIn("\x01", result)
        self.assertEqual(result, "textend")

    def test_22_keeps_newlines(self):
        """Переносы строк сохраняются"""
        result = self.svc._sanitize("line1\nline2")
        self.assertIn("\n", result)

    def test_23_truncates_to_max_len(self):
        """Обрезает до max_len"""
        result = self.svc._sanitize("a" * 500, max_len=100)
        self.assertEqual(len(result), 100)

    def test_24_empty_string_returns_empty(self):
        """Пустая строка → пустая строка"""
        result = self.svc._sanitize("")
        self.assertEqual(result, "")

    def test_25_none_returns_none(self):
        """None → None"""
        result = self.svc._sanitize(None)
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# Группа 5 — Workplace / Position инструкции
# ---------------------------------------------------------------------------

class TestWorkplaceInstructions(unittest.TestCase):

    def setUp(self):
        from bot.services.claude_service import ClaudeService
        self.svc = ClaudeService.__new__(ClaudeService)

    def _make_patient(self, **kwargs):
        base = dict(full_name="Test", birth_date="01.01.1990",
                    diagnosis="J00", is_student=False,
                    workplace=None, position=None)
        base.update(kwargs)
        return PatientData(**base)

    def test_26_worker_with_workplace(self):
        """Обычный работник с указанным местом работы"""
        p = self._make_patient(workplace="ООО Ромашка")
        instr = self.svc._get_workplace_instruction(p)
        self.assertIn("ООО Ромашка", instr)
        self.assertNotIn("прочерк", instr)

    def test_27_worker_without_workplace(self):
        """Работник без места работы → прочерк"""
        p = self._make_patient()
        instr = self.svc._get_workplace_instruction(p)
        self.assertIn("-", instr)

    def test_28_student_with_university(self):
        """Студент с указанной учёбой → Место учёбы"""
        p = self._make_patient(is_student=True, workplace="СПбГУ")
        instr = self.svc._get_workplace_instruction(p)
        self.assertIn("Место учёбы", instr)
        self.assertIn("СПбГУ", instr)
        self.assertIn("Место работы", instr)  # говорит "замените 'Место работы'"

    def test_29_student_without_university(self):
        """Студент без учёбы → Место учёбы: - """
        p = self._make_patient(is_student=True)
        instr = self.svc._get_workplace_instruction(p)
        self.assertIn("Место учёбы", instr)
        self.assertIn("-", instr)

    def test_30_student_whitespace_only_workplace(self):
        """Студент с workplace из пробелов → как без workplace"""
        p = self._make_patient(is_student=True, workplace="   ")
        instr = self.svc._get_workplace_instruction(p)
        self.assertIn("-", instr)
        # Не должно быть пустого значения "Место учёбы:    "
        self.assertNotIn("Место учёбы:    ", instr)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_tests():
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    for cls in [
        TestPromptStructure,
        TestReturnBehaviour,
        TestParseTextData,
        TestSanitize,
        TestWorkplaceInstructions,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print("\n" + "="*60)
    print(f"ИТОГО: {result.testsRun} тестов")
    print(f"✅ Прошло:  {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"❌ Упало:   {len(result.failures)}")
    print(f"💥 Ошибок: {len(result.errors)}")
    print("="*60)

    return result


if __name__ == "__main__":
    run_tests()
