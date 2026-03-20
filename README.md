# CRM System

Web-приложение CRM, построенное на основе Google Spreadsheet.

## CRM V2 (Operating CRM + Omnichannel)

В проект добавлена расширяемая V2-архитектура с нормализованными сущностями заказов,
исполнителей, задач и омниканальных чатов. Подробный аудит и новая архитектура описаны в файле `ARCHITECTURE_V2.md`.

Основные изменения:
- PostgreSQL как primary storage;
- Google Sheets в export-first режиме (импорт отключается флагом `GOOGLE_SHEETS_IMPORT_ENABLED`);
- API для заказов, каталога услуг, омниканальных чатов, исполнителей;
- WebSocket realtime для чатов и заказов;
- подготовка deep links и bootstrap для мобильного клиента (Capacitor).

### V2 ENV переменные

Добавьте в `.env` при использовании V2:

```env
GOOGLE_SHEETS_IMPORT_ENABLED=false
FIREBASE_CREDENTIALS_JSON=
```

`FIREBASE_CREDENTIALS_JSON` поддерживает и путь к JSON-файлу сервисного аккаунта, и inline JSON.

## Tech Stack

- **Backend:** Python, FastAPI
- **Frontend:** Bootstrap 5, HTMX, Jinja2
- **Database:** PostgreSQL (fallback SQLite)
- **Sync:** gspread (Google Sheets API)
- **Infrastructure:** Docker, docker-compose

## Структура проекта

```
crm/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI application entry point
│   ├── config.py             # Settings and environment variables
│   ├── database.py           # SQLAlchemy async engine & session
│   ├── models.py             # ORM models (User, Role, ModuleConfig, DynamicRecord, SyncLog)
│   ├── auth.py               # Authentication, JWT, password hashing, RBAC
│   ├── schema_loader.py      # Load schema.json → seed modules
│   ├── sheets_adapter.py     # Google Sheets gspread adapter
│   ├── sync_service.py       # Pull/push sync service
│   ├── routes_auth.py        # Login, logout, register routes
│   ├── routes_modules.py     # Dynamic module CRUD routes
│   └── routes_admin.py       # Admin panel routes
├── templates/
│   ├── layout.html           # Base template with sidebar
│   ├── login.html
│   ├── register.html
│   ├── dashboard.html
│   ├── module_list.html      # Module data table/cards
│   ├── record_edit_modal.html
│   ├── record_new_modal.html
│   ├── partials/
│   │   └── module_table.html # HTMX partial for table refresh
│   └── admin/
│       ├── dashboard.html
│       ├── roles.html
│       ├── role_edit.html    # Role permissions editor
│       ├── users.html
│       └── modules.html
├── static/
│   ├── css/style.css
│   └── js/app.js
├── schema.json               # Auto-detected schema from Excel
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
└── README.md
```

## Модули (из Excel)

| Лист | Slug | Колонки |
|------|------|---------|
| Заказы | orders | № заказа, Дата приёма, Клиент, Устройство, Неисправность, Мастер, Дедлайн, Дата выдачи, Гарантия до, Статус |
| Гарантийное обслуживание | warranty | № заказа, Дата приёма, Клиент, Устройство, Неисправность, Мастер, Дедлайн, Дата выдачи, Гарантия до, Статус |
| Клиенты | clients | № заказа, ФИО название, Телефон, Тип обращения, Тип цен, Примечание |
| Финансы | finance | № заказа, Дата выдачи, Цена для клиента, Стоимость работы, Запчасти себестоимость, Зарплата мастера, Чистая прибыль, Статус оплаты заказа, Статус оплаты сотруднику |
| Расходники | supplies | № п/п, Дата покупки, Наименование расходника, Происхождение, Стоимость |
| Аналитика | analytics | Месяц, Выручка, Затраты (запчасти), Затраты (расходники), Выплаты (зарплата), Чистая прибыль |

## Запуск на VPS

### 1. Подготовка

```bash
# Клонировать проект
git clone <repo-url> crm
cd crm

# Скопировать и настроить переменные окружения
cp .env.example .env
nano .env
```

### 2. Настройка .env

Обязательно измените:

```
SECRET_KEY=<длинная-случайная-строка>
FIRST_SUPERUSER_EMAIL=admin@example.com
FIRST_SUPERUSER_PASSWORD=<надёжный-пароль>
POSTGRES_PASSWORD=<надёжный-пароль>
DATABASE_URL=postgresql://crm:<тот-же-пароль>@postgres:5432/crm_db
```

### 3. Google Sheets (опционально)

1. Создайте проект в Google Cloud Console
2. Включите Google Sheets API
3. Создайте Service Account, скачайте JSON-ключ
4. Поделитесь таблицей с email сервисного аккаунта
5. Укажите в `.env`:
   ```
   GOOGLE_CREDENTIALS_JSON=/app/credentials.json
   SPREADSHEET_ID=<ID-из-URL-таблицы>
   ```
6. Добавьте файл credentials.json в docker-compose volume

### 4. Запуск

```bash
docker compose up -d --build
```

### 5. Использование

- Откройте `http://<your-ip>:8000`
- Войдите с email/паролем из `.env`
- Перейдите в Админ → Синхронизация → Pull для загрузки данных из Google Sheets
- Настройте роли и права доступа

### 6. Полезные команды

```bash
# Логи
docker compose logs -f app

# Перезапуск
docker compose restart app

# Остановка
docker compose down

# С удалением данных
docker compose down -v
```

## Архитектура синхронизации

```
Google Sheets
     ↕
Sync Adapter (gspread)
     ↕
Local Database (PostgreSQL)
```

- **Pull:** Google Sheets → Local DB (полная перезапись модуля)
- **Push:** Local DB → Google Sheets (полная перезапись листа)
- Синхронизация запускается вручную из админ-панели
- Логи синхронизации сохраняются в таблице sync_logs

## Каталог услуг: схема калькулятора

Эндпоинт `POST /api/v1/catalog/services/{service_id}/calculate` поддерживает типы полей:
- `number|int|float`: расчет по формуле `value * coefficient + offset`
- `boolean|checkbox`: добавляет `true_price` или `false_price`
- `select|enum|choice`: ищет выбранную опцию и применяет `price` и `multiplier`

Пример `calculator_schema`:

```json
{
     "currency": "RUB",
     "round_to": 2,
     "minimum_total": 500,
     "fields": [
          {"name": "hours", "type": "number", "coefficient": 1200},
          {"name": "express", "type": "boolean", "true_price": 700},
          {
               "name": "device_type",
               "type": "select",
               "options": [
                    {"value": "phone", "price": 0},
                    {"value": "laptop", "price": 900}
               ]
          }
     ]
}
```

## Миграции Alembic

В проект добавлен Alembic для управляемых миграций базы данных.

### Локально

```bash
# применить миграции до последней версии
alembic -c alembic.ini upgrade head

# создать новую миграцию
alembic -c alembic.ini revision -m "your change"

# автогенерация миграции по моделям
alembic -c alembic.ini revision --autogenerate -m "your change"
```

### В Docker

```bash
# выполнить миграции в контейнере app
docker compose exec app alembic -c alembic.ini upgrade head
```

Первая миграция: `0001_initial_schema.py`.

## Последние обновления (2026-03-20)

- Добавлена отдельная админ-страница управления услугами: `GET /admin/services`.
- В админ-интерфейс добавлены переходы на раздел услуг из панели и сайдбара.
- Усилена ролевая безопасность каталога:
     - пользователи без права управления услугами получают санитизированную `calculator_schema`;
     - для таких ролей скрываются `base_price` и детальный `breakdown` в расчете.
- В ценообразование добавлена комиссия в `calculator_schema`:
     - `fixed` - фиксированная сумма;
     - `percent` - процент от рассчитанного subtotal.
- Комиссия применяется к итоговой сумме расчета, при этом внутренние детали по-прежнему скрываются для ролей без доступа.
- Актуальный статус тестов после изменений: `20 passed`.

## Система прав

- **Суперпользователь** — полный доступ ко всему
- **Роли** — настраиваемые через админ-панель:
  - Видимость модулей (вкл/выкл)
  - Видимость полей (чекбоксы по каждому полю)
  - Редактирование полей (чекбоксы по каждому полю)
