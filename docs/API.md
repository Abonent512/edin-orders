# API v1

Машиночитаемый контракт: `/api/v1/openapi.json`; автономный Swagger UI: `/api/docs`. В архиве также есть снимок `docs/openapi.json`. Все денежные поля — целые копейки, дата-время — ISO 8601 UTC. JSON UTF-8. JWT передаётся `Authorization: Bearer <access_token>`, не в query string. В Swagger нажмите Authorize и вставьте access token после входа.

| Метод и путь | Доступ | Успех |
|---|---|---|
| GET `/api/v1/config` | Любой | 200: версия, demo_mode |
| GET `/api/v1/health` | Любой | 200: status=ok после проверки БД |
| POST `/api/v1/auth/register` | Гость | 201: JWT и пользователь, Set-Cookie refresh |
| POST `/api/v1/auth/login` | Гость | 200: JWT и пользователь |
| POST `/api/v1/auth/demo` | Только DEMO_MODE | 200: JWT; `role=user/operator` |
| POST `/api/v1/auth/refresh` | Refresh cookie | 200: новый JWT и cookie, ротация |
| POST `/api/v1/auth/logout` | Cookie | 204: отзыв сессии |
| GET `/api/v1/me` | JWT | 200: текущий пользователь |
| GET `/api/v1/products` | Любой | 200: `{items: Product[]}` |
| POST `/api/v1/orders` | JWT | 201: новый Order, 200: идемпотентный повтор |
| GET `/api/v1/orders` | JWT | 200: `{items,total,limit,offset}`, свои/все по роли |
| GET `/api/v1/orders/{id}` | Владелец/оператор | 200: полный Order |
| PATCH `/api/v1/orders/{id}/status` | Оператор | 200: обновлённый Order |
| GET `/api/v1/events?after=0` | JWT | 200: `text/event-stream` |

Список заказов: `limit` 1–100 (по умолчанию 100), `offset` от 0, необязательный `status`. UI загружает страницы по 100; фильтры/статистика страницы явно помечены. Для полного агрегирования большого количества заказов нужен отдельный analytics endpoint.

## Создание заказа

Обязателен `Idempotency-Key` длиной 8–128 символов: новый UUID для одного пользовательского действия. При сетевой ошибке повторите **тот же** запрос с **тем же** ключом. Один ключ с другим телом даёт 409.

```http
POST /api/v1/orders
Authorization: Bearer <JWT>
Idempotency-Key: aec70149-4650-4832-ad38-2e0a717ef1a1
Content-Type: application/json
```

```json
{
  "items": [{"product_id": "drill", "quantity": 1}],
  "name": "Тестовый покупатель",
  "phone": "+79000000000",
  "delivery": "pickup",
  "address": "",
  "comment": "",
  "channel": "web"
}
```

`channel`: `web` или `mobile`; метка носит информационный характер и не предоставляет прав. `delivery`: `pickup` или `courier`. Для курьера адрес от 10 символов. Телефон +7/8 и 10 цифр нормализуется к +7. Quantity — строго integer 1–99, не boolean/строка/дробь. Сумма и цены из клиента не принимаются. 1–50 уникальных товарных позиций. Лишние поля отклоняются.

Успешный ответ содержит `id`, `number`, `status`, `version`, `total`, `delivery_fee`, получателя, канал, UTC-даты, `items` (снимок цены/названия), `history` (статус/время/номер события). Новый заказ имеет `status=new`, `version=1`. При первом создании также возвращается заголовок `Location`.

## Статусы и конфликты

```text
new → confirmed → assembling → shipping → completed
  └→ cancelled  └→ cancelled  └→ cancelled
```

Из `completed` и `cancelled` переходов нет. Из `shipping` допускается только `completed`; возвраты/отмена уже отправленного заказа в v1 не реализованы.

```json
{"status":"confirmed","version":1}
```

PATCH разрешён только оператору. Сервер проверяет текущую версию, при расхождении возвращает `409 VERSION_CONFLICT`. UI обновляет заказ и предлагает повторить действие. При отмене остатки восстанавливаются атомарно один раз.

## SSE

С авторизацией через fetch streaming (не EventSource с JWT в URL):

```text
id: 18
event: order.updated
data: {"id":18,"order_id":"...","status":"confirmed"}

```

`after=18` или `Last-Event-ID: 18` позволяет продолжить. События покупателя отфильтрованы по владельцу на сервере. Heartbeat каждые 15 секунд. При logout/истечении сессии поток закрывается. Прокси должен пропускать поток без буферизации.

## Ошибки

```json
{"error":{"code":"VERSION_CONFLICT","message":"Заказ уже изменён. Обновите данные","details":{"version":2},"request_id":"..."}}
```

| HTTP | Примеры code |
|---|---|
| 400 | INVALID_LENGTH |
| 401 | UNAUTHORIZED, BAD_CREDENTIALS, SESSION_EXPIRED |
| 403 | FORBIDDEN, ORIGIN_FORBIDDEN |
| 404 | ORDER_NOT_FOUND, NOT_FOUND |
| 409 | OUT_OF_STOCK, IDEMPOTENCY_CONFLICT, VERSION_CONFLICT, INVALID_TRANSITION, EMAIL_EXISTS |
| 413 | BODY_TOO_LARGE |
| 422 | VALIDATION_ERROR, ADDRESS_REQUIRED, DUPLICATE_PRODUCT, PRODUCT_NOT_FOUND, INVALID_PAGE |
| 429 | RATE_LIMIT |
| 500 | INTERNAL_ERROR |
| 503 | DATABASE_UNAVAILABLE |

`X-Request-ID` связывает ответ с журналом. Тело запроса, пароли, токены, cookie и персональные данные не включаются в обычный журнал запросов. При недоступности базы данные заказа не придумываются локально: клиент показывает ошибку и даёт повторить действие.
