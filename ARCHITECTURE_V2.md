# CRM V2 Audit + Architecture

## 1. Текущий аудит проекта

### Что есть сейчас
- Технологии: FastAPI + SQLAlchemy + Jinja2 + HTMX + PostgreSQL/SQLite.
- Данные CRM (заказы/клиенты/финансы) хранятся в `dynamic_records.data` (JSON) по `module_slug`.
- `module_configs` хранит описание схем полей для модулей.
- Google Sheets используется как источник/приемник через pull/push.

### Узкие места и риски масштабирования (10k-100k заказов)
- Отсутствие нормализованных таблиц предметной области: нет явных связей ORDER -> ITEMS -> TASKS -> EXECUTORS.
- JSON-поиск (`data.cast(str).ilike`) быстро деградирует на больших объемах.
- Pull из Sheets перезаписывает модуль целиком (delete + insert), риск потери локальных изменений.
- Нет устойчивого audit trail по бизнес-сущностям.
- Нет очередей/событий для realtime-обновлений.
- Нет унифицированного чат-домена (конверсации, сообщения, вложения).

### Что нужно перевести из dynamic в нормализованные таблицы
- Заказы (`orders`) и их позиции (`order_items`).
- Клиентов (`clients`).
- Исполнителей, отделы и навыки (`executors`, `departments`, `executor_skills`).
- Производственные задачи (`tasks`).
- Каталог услуг и калькуляторы (`services`).
- Омниканальные коммуникации (`channels`, `conversations`, `messages`, `attachments`).

## 2. Новая целевая архитектура

```
Web / Mobile (Capacitor)
  -> API (FastAPI routers in /api)
    -> Services (business logic)
      -> Repositories (query/data access)
        -> Models (normalized SQLAlchemy entities)
          -> PostgreSQL (primary storage)

Integrations:
- Google Sheets: export-only
- Channel connectors: adapter/connector abstraction
- Firebase Push: notification facade
- Realtime: WebSocket + in-memory event bus
```

## 3. Добавленные модели

### Core CRM
- `clients`: клиент, контакты, стадия.
- `orders`: заказ, приоритет, статус, канал источника.
- `order_items`: позиции заказа, service linkage, calculator payload.
- `services`: справочник услуг + `calculator_schema` JSON.
- `departments`: отделы.
- `executors`: исполнители и текущая загрузка.
- `executor_skills`: навыки исполнителей по категориям услуг.
- `tasks`: задачи по заказам/позициям, исполнитель, score автоназначения.
- `audit_logs`: журнал изменений.

### Omnichannel
- `channels`: каналы (telegram/vk/avito/whatsapp/custom).
- `conversations`: диалоги (thread, статус, назначение).
- `messages`: вход/выход, текст, AI классификация.
- `attachments`: файлы сообщений.

## 4. Индексы и ключевые связи

- Композитные индексы:
  - `orders(client_id, status)`
  - `tasks(executor_id, status)`
  - `order_items(order_id, status)`
  - `conversations(channel_id, external_thread_id)` unique
  - `messages(conversation_id, created_at)`
- Внешние ключи обеспечивают целостность между заказом, позициями, задачами, исполнителями и чатами.

## 5. Алгоритм автоназначения

Формула:

`score = skill_match - load + priority`

- `skill_match`: уровень навыка исполнителя по категории услуги.
- `load`: `current_active_tasks` исполнителя.
- `priority`: приоритет заказа.

Реализовано в `services/assignment_service.py`.

## 6. AI слой

Реализован базовый классификатор в `services/ai_service.py`:
- определение категории услуги;
- определение стадии клиента;
- intent: `info` / `create_order`.

Пайплайн: `message -> lead classification -> create order proposal`.

## 7. Realtime / WebSocket

- Endpoint: `/ws/updates` (orders + chats).
- Endpoint: `/ws/chats/{conversation_id}` (точечный поток по диалогу).
- Публикация событий через `core/events.py`.

## 8. Push (Firebase)

- Фасад `services/push_service.py`.
- Подготовлены точки вызова для событий:
  - новое сообщение;
  - новый заказ;
  - назначение задачи.

## 9. Mobile / Capacitor readiness

- Добавлен `capacitor.config.json` (WebView по URL).
- Deep links:
  - `/mobile/deeplink/chat/{id}` -> `/chat/{id}`
  - `/mobile/deeplink/order/{id}` -> `/order/{id}`
- UX: мобильные quick-actions и FAB на дашборде.

## 10. Уход от зависимости Sheets

- PostgreSQL становится primary storage.
- Pull из Sheets по умолчанию отключен (`GOOGLE_SHEETS_IMPORT_ENABLED=false`).
- Sheets используется как export/integration канал.

## 11. Новые API endpoints

### Orders
- `POST /api/v1/orders`
- `GET /api/v1/orders/{order_id}`

### Catalog / Services
- `GET /api/v1/catalog/services?q=`
- `POST /api/v1/catalog/services`
- `POST /api/v1/catalog/services/{service_id}/calculate`

### Executors / Tasks
- `GET /api/v1/executors`
- `POST /api/v1/executors/tasks/{task_id}/assign`

### Omnichannel chats
- `GET /api/v1/chats/inbox`
- `POST /api/v1/chats/incoming`
- `POST /api/v1/chats/{conversation_id}/send`
- `GET /api/v1/chats/{conversation_id}/messages`

### Mobile
- `GET /mobile/bootstrap`
- `GET /mobile/deeplink/chat/{chat_id}`
- `GET /mobile/deeplink/order/{order_id}`

### Realtime
- `WS /ws/updates`
- `WS /ws/chats/{conversation_id}`

## 12. Пример создания заказа

```json
POST /api/v1/orders
{
  "order_no": "ORD-24032026-001",
  "priority": 2,
  "source_channel": "telegram",
  "client": {
    "name": "Иван Петров",
    "phone": "+79990001122",
    "email": "ivan@example.com"
  },
  "items": [
    {
      "service_id": 3,
      "title": "Визитки 500 шт",
      "quantity": 1,
      "unit_price": 3500,
      "calculator_payload": {
        "tiраж": 500,
        "paper": "300gsm",
        "lamination": "matt"
      }
    }
  ]
}
```

Результат:
- создается `order`;
- создается `order_item`;
- автоматически создается `task`;
- выбирается исполнитель по формуле score;
- событие уходит в realtime канал.

## 13. Flow: сообщение -> заказ -> задача -> исполнитель

1. Входящее сообщение приходит в `/api/v1/chats/incoming`.
2. `ChatService` сохраняет `conversation` + `message`.
3. `AILeadService` классифицирует сообщение и помечает intent.
4. Менеджер/автофлоу вызывает `POST /api/v1/orders`.
5. `OrderService` создает заказ и позиции.
6. `OrderService` создает `task` и автоподбирает исполнителя.
7. Realtime события и push уведомления информируют менеджера/исполнителя.
