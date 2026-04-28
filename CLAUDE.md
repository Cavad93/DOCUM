# CLAUDE.md

Инструкции для Claude Code при работе с этим репозиторием.

## Project Overview

Telegram-бот для генерации шаблонов медицинского осмотра.
Поддерживает 3 клиники:

- **Династия / ПСКП** — генерируют `.docx`-документы по шаблону, отправляют по email.
- **ГОДОК** — заполняет карточку осмотра в **Битрикс24** (CRM, воронка `CATEGORY_ID=10` «ПНД ПОМОЩЬ НА ДОМУ»). Дополнительно поллит воронку 14 «БОЛЬНИЧНЫЕ ЛИСТЫ» каждые 5 минут на новые заявки.

LLM — Claude (Anthropic SDK). Ключ обязателен.

## Архитектура

```
bot/
├── telegram_bot.py            — основной обработчик: команды, callbacks, поллер ГОДОК
├── models/types.py            — PatientData, BotState, BotContext, ClinicMode
├── services/
│   ├── claude_service.py      — генерация шаблонов и полей через Anthropic API (tool_use)
│   ├── archive_service.py     — поиск похожих шаблонов в архиве
│   ├── email_service.py       — SMTP-отправка, реестр получателей по клиникам
│   ├── document_queue_service.py — очередь .docx-документов для пакетной отправки (ПСКП)
│   ├── memory_service.py      — обучающая память (тренды диагнозов)
│   ├── corrections_memory_service.py — типичные правки врача
│   ├── bitrix_service.py      — REST-клиент Битрикс24 (TLS-адаптер, ретраи, find_*)
│   └── godok_state_service.py — перситентный state для ГОДОК-поллера (chat_id, last_id)
data/
├── templates/                 — .docx-шаблоны Династия/ПСКП
├── archive/                   — сохранённые шаблоны (gitignored)
├── bitrix/godok_fields.json   — карта 36 AI-полей карточки осмотра ГОДОК
└── bitrix/godok_state.json    — chat_id врача и last_seen_deal_id (gitignored)
main.py                        — entry-point: load_dotenv, MedicalBot, run
```

## Pre-deployment checklist (новый сервер)

При разворачивании на новой машине проверить **до запуска бота**:

### 1. DNS — не Cisco / не OpenDNS

Cisco Umbrella / OpenDNS блокирует ряд российских доменов как «phishing»,
включая `crm.go-doc.ru`. Проверить:

```cmd
nslookup crm.go-doc.ru
ipconfig /all | findstr "DNS"
```

- Если в ответе **`146.112.61.x`** или **`208.67.x.x`** или DNS-сервер
  `dns.sse.cisco.com` — это **Cisco DNS**, нужно сменить.
- Норма — реальный IP `130.193.53.12`.

Сменить DNS на Cloudflare/Google:
```powershell
Get-NetAdapter | Where-Object Status -eq "Up" | ForEach-Object {
    Set-DnsClientServerAddress -InterfaceIndex $_.ifIndex -ServerAddresses ("1.1.1.1","8.8.8.8")
}
ipconfig /flushdns
```

Если в RuVDS-фаерволе нет правила Out для UDP/53 — DNS не пройдёт. Решений два:
- **(A)** добавить Out-правило для UDP порта 53 в панели RuVDS, или
- **(B)** добавить статичную запись в `C:\Windows\System32\drivers\etc\hosts`:
  ```
  130.193.53.12  crm.go-doc.ru
  ```

Если установлен **Cisco Secure Client / Umbrella Roaming Client** —
он перехватывает DNS даже после смены настроек. Проверить и отключить:
```powershell
Get-Service | Where-Object {$_.DisplayName -like "*Umbrella*" -or $_.DisplayName -like "*Cisco*"}
```

### 2. Сетевой фаервол хостера (RuVDS, и т.п.)

Должны быть открыты:

| Направление | Протокол | Порт | Назначение |
|---|---|---|---|
| In | TCP | 3389 | RDP — иначе нельзя подключиться к серверу |
| Out | TCP | 443 | HTTPS — Telegram, Anthropic, Битрикс24 |
| Out | UDP | 53 | DNS (если не используем hosts) |
| Out | TCP | 587 / 465 | SMTP (если используется отправка email) |

⚠️ В панели RuVDS кнопки «Установить правила для Windows / Linux»
**ПОЛНОСТЬЮ ЗАМЕНЯЮТ** список правил. Не нажимать без надобности —
можно случайно отрезать RDP и потерять доступ к серверу.

### 3. Файл `.env`

Обязательные переменные:
```
TELEGRAM_BOT_TOKEN=<токен>
ANTHROPIC_API_KEY=sk-ant-api03-...
```

Опциональные:
```
SMTP_HOST=smtp.yandex.ru
SMTP_PORT=587
SMTP_USER=<email>
SMTP_PASSWORD=<app-password>
BITRIX_WEBHOOK_URL=https://crm.go-doc.ru/rest/<id>/<secret>
BITRIX_TLS_VERIFY=0   # эскейп-хатч если TLS-инспектор/MITM на сервере
```

### 4. Системные переменные Windows

`load_dotenv(override=True)` в `main.py` должен перезаписать системные
переменные значениями из `.env`. Но на всякий случай — проверить:
```cmd
echo %ANTHROPIC_API_KEY%
echo %TELEGRAM_BOT_TOKEN%
```

Если показывают значения, отличные от `.env` — лучше очистить:
```cmd
setx ANTHROPIC_API_KEY ""
setx TELEGRAM_BOT_TOKEN ""
```

(после `setx` нужно закрыть/открыть cmd, чтобы изменения применились в новых процессах).

### 5. Установка зависимостей

```cmd
pip install -r requirements.txt
```

Ключевые пакеты, добавленные для ГОДОК / Битрикс24:
- `requests>=2.32.0`
- `truststore>=0.10.0` — нативный системный CA store (Windows CryptoAPI)
- `certifi` (приходит транзитивно)

## Common Commands

### Запуск
```cmd
python main.py
```

### Проверка подключения к Битрикс24 без бота
```cmd
python -c "from bot.services.bitrix_service import BitrixService; bs=BitrixService('https://crm.go-doc.ru/rest/<id>/<secret>'); print(bs._call('profile'))"
```

### Проверка ключа Anthropic без бота
```cmd
python -c "from dotenv import load_dotenv; load_dotenv(override=True); from anthropic import Anthropic; r=Anthropic().messages.create(model='claude-haiku-4-5-20251001',max_tokens=10,messages=[{'role':'user','content':'hi'}]); print(r.content[0].text)"
```

### Проверка DNS-резолвинга для Битрикс24
```cmd
python -c "import socket; print(socket.gethostbyname('crm.go-doc.ru'))"
```
Должно вернуть `130.193.53.12`. Если возвращает `146.112.61.x` — DNS идёт через Cisco.

## TLS-сюрпризы и как их лечить

`BitrixService` использует `_LegacyTLSAdapter` для совместимости. При проблемах:

| Симптом | Причина | Лечение |
|---|---|---|
| `SSLV3_ALERT_HANDSHAKE_FAILURE` | OpenSSL дефолтный SECLEVEL=2 режет шифры | Уже фиксится (SECLEVEL=1 в адаптере) |
| `CERTIFICATE_VERIFY_FAILED: unable to get local issuer` | пустой trust store в кастомном ssl_context | Уже фиксится (`certifi.load_verify_locations`) |
| `CERTIFICATE_VERIFY_FAILED: not trusted by trust provider` | enterprise CA (Cisco Umbrella, антивирус) не виден Python | `truststore` подгружает Windows CryptoAPI store |
| Тот же verify_failed после truststore | truststore игнорирует CERT_NONE на части версий | `BITRIX_TLS_VERIFY=0` в `.env` (короткое замыкание) |
| HTML с `phish.opendns.com` вместо JSON | Cisco Umbrella DNS флагует домен | См. раздел 1 — сменить DNS / добавить hosts |

## Development Guidelines

- Не хардкодить секреты в исходниках. Только через `.env`.
- При работе с Битрикс24 всегда обрабатывать `BitrixError` — он включает в себя
  и сетевые ошибки (после исчерпания ретраев), и бизнес-ошибки API.
- При добавлении новых полей в карточку ГОДОК — обновлять `data/bitrix/godok_fields.json`,
  раздел `ai_fields` (для AI-генерируемых) или `context_fields` (для системных).
- Не трогать поля из `template_fields` и `manual_fields` — врач заполняет их вручную.
- При изменении состояний (`BotState`) — добавлять обработку в `handle_text` и в `_reset_godok_context`.

## Полезные ссылки

- Битрикс24 REST API: https://apidocs.bitrix24.com/
- Anthropic API: https://docs.anthropic.com/
- python-telegram-bot 21.x: https://docs.python-telegram-bot.org/
- Truststore (системный CA): https://truststore.readthedocs.io/
