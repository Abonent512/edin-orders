import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import jwt
from fastapi import Depends, FastAPI, Header, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from starlette.exceptions import HTTPException

from .database import DATA_DIR, POSTGRES, connection, initialize, secret, transaction
from .schemas import CatalogOut, ErrorOut, OrderOut, OrdersOut, TokenOut, UserOut

try:
    from psycopg import IntegrityError as PostgresIntegrityError
except ImportError:
    PostgresIntegrityError = sqlite3.IntegrityError

log = logging.getLogger("edin")
logging.basicConfig(level=logging.INFO, format="%(message)s")
DEMO_MODE = os.getenv("DEMO_MODE", "false").lower() == "true"
SECURE = os.getenv("COOKIE_SECURE", "true").lower() == "true"
RENDER_ORIGIN = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
DEFAULT_ORIGINS = RENDER_ORIGIN or "http://localhost:5173,http://localhost:8000,http://127.0.0.1:8000,http://127.0.0.1:5173"
ORIGINS = [s.strip().rstrip("/") for s in os.getenv("ALLOWED_ORIGINS", DEFAULT_ORIGINS).split(",") if s.strip()]
if RENDER_ORIGIN and RENDER_ORIGIN not in ORIGINS:
    ORIGINS.append(RENDER_ORIGIN)
STATUSES = {"new": ["confirmed", "cancelled"], "confirmed": ["assembling", "cancelled"], "assembling": ["shipping", "cancelled"], "shipping": ["completed"], "completed": [], "cancelled": []}


def now():
    return datetime.now(timezone.utc).isoformat()


class APIError(Exception):
    def __init__(self, status, code, message, details=None):
        self.status, self.code, self.message, self.details = status, code, message, details


def password_hash(password):
    salt = secrets.token_hex(16)
    value = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()
    return f"scrypt${salt}${value}"


def check_password(password, value):
    try:
        _, salt, expected = value.split("$")
        actual = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()
        return hmac.compare_digest(expected, actual)
    except (ValueError, TypeError):
        return False


@asynccontextmanager
async def lifespan(app):
    initialize()
    if DEMO_MODE:
        with transaction() as db:
            for role, name in [("user", "Алексей Демин"), ("operator", "Мария · оператор")]:
                db.execute("INSERT OR IGNORE INTO users VALUES(?,?,?,?,?,?)", (f"demo-{role}", f"{role}@demo.edin.local", name, password_hash(secrets.token_urlsafe(40)), role, now()))
    yield


app = FastAPI(title="ЕДИН · Омниканальная система заказов", version="1.0.0", lifespan=lifespan,
              description="Общий контракт веб- и мобильного клиентов. Цены — целые копейки. JWT Bearer + HttpOnly refresh cookie.",
              docs_url=None, redoc_url=None, openapi_url="/api/v1/openapi.json",
              responses={code: {"model": ErrorOut, "description": description} for code, description in {400:"Некорректный запрос",401:"Требуется вход",403:"Нет прав",404:"Ресурс не найден",409:"Конфликт состояния или повторного запроса",422:"Ошибка валидации",429:"Лимит попыток входа",500:"Внутренняя ошибка",503:"База временно недоступна"}.items()})
app.add_middleware(CORSMiddleware, allow_origins=ORIGINS, allow_credentials=True,
                   allow_methods=["GET", "POST", "PATCH", "OPTIONS"], allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "Last-Event-ID"])


@app.middleware("http")
async def middleware(request: Request, call_next):
    request.state.request_id = str(uuid.uuid4())
    start = time.monotonic()
    origin = request.headers.get("origin")
    if request.method in {"POST", "PATCH", "PUT", "DELETE"} and origin and origin not in ORIGINS:
        return error_response(request, 403, "ORIGIN_FORBIDDEN", "Источник запроса не разрешён")
    # Limit requests before JSON parsing; reverse proxy applies an independent 64 KB limit.
    try:
        length = int(request.headers.get("content-length", "0"))
    except ValueError:
        return error_response(request, 400, "INVALID_LENGTH", "Некорректный размер запроса")
    if length > 65536:
        return error_response(request, 413, "BODY_TOO_LARGE", "Запрос слишком большой")
    try:
        response = await call_next(request)
    except Exception:
        log.exception(json.dumps({"event": "unhandled_error", "request_id": request.state.request_id, "path": request.url.path}))
        response = error_response(request, 500, "INTERNAL_ERROR", "Внутренняя ошибка. Попробуйте позже")
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Frame-Options"] = "DENY"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    if request.url.path != "/api/v1/events":
        log.info(json.dumps({"event": "request", "request_id": request.state.request_id, "method": request.method, "path": request.url.path, "status": response.status_code, "duration_ms": round((time.monotonic()-start)*1000)}))
    return response


def error_response(request, status, code, message, details=None):
    headers = {"Retry-After": "300"} if status == 429 else ({"Retry-After": "5"} if status == 503 else {})
    return JSONResponse(status_code=status, headers=headers, content={"error": {"code": code, "message": message, "details": details, "request_id": getattr(request.state, "request_id", "")}})


@app.exception_handler(APIError)
async def api_error(request, exc):
    return error_response(request, exc.status, exc.code, exc.message, exc.details)


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    fields = [{"field": ".".join(map(str, e["loc"])), "message": e["msg"]} for e in exc.errors()]
    return error_response(request, 422, "VALIDATION_ERROR", "Проверьте заполненные поля", fields)


@app.exception_handler(HTTPException)
async def http_error(request, exc):
    return error_response(request, exc.status_code, "HTTP_ERROR", str(exc.detail))


async def database_error(request, exc):
    log.error(json.dumps({"event": "database_unavailable", "request_id": getattr(request.state, "request_id", ""), "type": type(exc).__name__}))
    return error_response(request, 503, "DATABASE_UNAVAILABLE", "База временно недоступна. Повторите через несколько секунд")


app.add_exception_handler(sqlite3.OperationalError, database_error)
try:
    from psycopg import OperationalError as PostgresOperationalError
    from psycopg_pool import PoolTimeout
    app.add_exception_handler(PostgresOperationalError, database_error)
    app.add_exception_handler(PoolTimeout, database_error)
except ImportError:
    pass


def public_user(row):
    return {k: row[k] for k in ["id", "email", "name", "role"]}


def current_user(authorization: str | None = Header(default=None)):
    try:
        if not authorization or not authorization.startswith("Bearer "):
            raise ValueError()
        claims = jwt.decode(authorization[7:], secret(), algorithms=["HS256"], issuer="edin", audience="edin-clients", options={"require": ["exp", "iat", "sub", "sid"]})
        with connection() as db:
            row = db.execute("SELECT u.* FROM users u JOIN sessions s ON s.user_id=u.id WHERE u.id=? AND s.id=? AND s.expires_at>?", (claims["sub"], claims["sid"], int(time.time()))).fetchone()
        if not row:
            raise ValueError()
        return public_user(row)
    except (jwt.PyJWTError, ValueError):
        raise APIError(401, "UNAUTHORIZED", "Войдите в аккаунт")


def operator(user=Depends(current_user)):
    if user["role"] != "operator":
        raise APIError(403, "FORBIDDEN", "Действие доступно только оператору")
    return user


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Login(StrictModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class Register(Login):
    name: str = Field(min_length=2, max_length=80)
    password: str = Field(min_length=10, max_length=128)


class Demo(StrictModel):
    role: Literal["user", "operator"]


def rate_limit(key, maximum=8, seconds=300):
    ts = int(time.time())
    with transaction() as db:
        db.execute("DELETE FROM rate_limits WHERE reset_at<?", (ts,))
        row = db.execute("SELECT * FROM rate_limits WHERE key=?", (key,)).fetchone()
        if row and row["attempts"] >= maximum:
            raise APIError(429, "RATE_LIMIT", "Слишком много попыток. Повторите через 5 минут")
        db.execute("INSERT INTO rate_limits VALUES(?,1,?) ON CONFLICT(key) DO UPDATE SET attempts=rate_limits.attempts+1", (key, ts+seconds))


def issue_session(user, response, db=None, session_id=None):
    refresh = secrets.token_urlsafe(48)
    token_hash = hashlib.sha256(refresh.encode()).hexdigest()
    sid = session_id or secrets.token_hex(24)
    ts = int(time.time())
    def write(conn):
        conn.execute("DELETE FROM sessions WHERE expires_at<=?", (ts,))
        if session_id:
            conn.execute("UPDATE sessions SET previous_hash=token_hash,previous_until=?,token_hash=?,expires_at=? WHERE id=?", (ts+10,token_hash,ts+30*86400,sid))
        else:
            conn.execute("INSERT INTO sessions VALUES(?,?,?,?,?,?,?)", (sid,token_hash,None,0,user["id"],ts+30*86400,ts))
    if db is not None:
        write(db)
    else:
        with transaction() as conn:
            write(conn)
    token = jwt.encode({"sub": user["id"], "sid": sid, "iat": ts, "exp": ts+900, "iss": "edin", "aud": "edin-clients"}, secret(), algorithm="HS256")
    response.set_cookie("edin_refresh", refresh, max_age=30*86400, httponly=True, secure=SECURE, samesite="strict", path="/api/v1/auth")
    return {"access_token": token, "token_type": "bearer", "expires_in": 900, "user": public_user(user)}


@app.get("/api/v1/config", tags=["system"])
def config():
    return {"demo_mode": DEMO_MODE, "version": "1.0.0", "currency": "RUB", "mobile_path": "/mobile/"}


@app.get("/api/v1/health", tags=["system"])
def health():
    with connection() as db:
        db.execute("SELECT 1").fetchone()
    return {"status": "ok", "version": "1.0.0"}


@app.get("/api/docs", include_in_schema=False, response_class=HTMLResponse)
def docs():
    return '<!doctype html><html lang="ru"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ЕДИН API v1</title><link rel="stylesheet" href="/docs-assets/swagger-ui.css"><link rel="icon" href="/icon.svg"></head><body><div id="swagger-ui"></div><script src="/docs-assets/swagger-ui-bundle.js"></script><script src="/docs-assets/init.js"></script></body></html>'


@app.get("/downloads/edin-android-demo.apk", include_in_schema=False)
def android_download():
    path = Path(os.getenv("APK_FILE", str(Path(__file__).resolve().parents[2] / "releases/edin-android-demo.apk")))
    if not path.is_file():
        raise APIError(404, "APK_NOT_AVAILABLE", "Сборка Android пока недоступна")
    return FileResponse(path, media_type="application/vnd.android.package-archive", filename="edin-android-demo.apk")


@app.post("/api/v1/auth/register", status_code=201, tags=["auth"], response_model=TokenOut)
def register(data: Register, request: Request, response: Response):
    rate_limit("register:"+request.client.host, 10)
    user = {"id": str(uuid.uuid4()), "email": str(data.email).lower(), "name": data.name, "role": "user"}
    try:
        with transaction() as db:
            db.execute("INSERT INTO users VALUES(?,?,?,?,?,?)", (user["id"], user["email"], user["name"], password_hash(data.password), "user", now()))
    except (sqlite3.IntegrityError, PostgresIntegrityError):
        raise APIError(409, "EMAIL_EXISTS", "Этот email уже зарегистрирован")
    return issue_session(user, response)


@app.post("/api/v1/auth/login", tags=["auth"], response_model=TokenOut)
def login(data: Login, request: Request, response: Response):
    rate_limit("login:"+request.client.host)
    with connection() as db:
        user = db.execute("SELECT * FROM users WHERE email=?", (str(data.email).lower(),)).fetchone()
    # Equal-cost hash for nonexistent accounts to reduce account enumeration by timing.
    dummy = "scrypt$00000000000000000000000000000000$" + "0"*128
    valid = check_password(data.password, user["password_hash"] if user else dummy)
    if not user or not valid:
        raise APIError(401, "BAD_CREDENTIALS", "Неверный email или пароль")
    return issue_session(user, response)


@app.post("/api/v1/auth/demo", tags=["auth"], response_model=TokenOut)
def demo(data: Demo, response: Response):
    if not DEMO_MODE:
        raise APIError(404, "NOT_FOUND", "Ресурс не найден")
    with connection() as db:
        user = db.execute("SELECT * FROM users WHERE id=?", (f"demo-{data.role}",)).fetchone()
    return issue_session(user, response)


@app.post("/api/v1/auth/refresh", tags=["auth"], response_model=TokenOut)
def refresh(request: Request, response: Response):
    token_hash = hashlib.sha256(request.cookies.get("edin_refresh", "").encode()).hexdigest()
    with transaction() as db:
        ts = int(time.time())
        user = db.execute("SELECT u.*,s.id AS session_id FROM users u JOIN sessions s ON u.id=s.user_id WHERE (s.token_hash=? OR (s.previous_hash=? AND s.previous_until>?)) AND s.expires_at>?"+(" FOR UPDATE OF s" if POSTGRES else ""), (token_hash,token_hash,ts,ts)).fetchone()
        if not user:
            raise APIError(401, "SESSION_EXPIRED", "Войдите в аккаунт")
        return issue_session(user, response, db, user["session_id"])


@app.post("/api/v1/auth/logout", status_code=204, tags=["auth"])
def logout(request: Request, response: Response):
    token_hash = hashlib.sha256(request.cookies.get("edin_refresh", "").encode()).hexdigest()
    with transaction() as db:
        db.execute("DELETE FROM sessions WHERE token_hash=? OR (previous_hash=? AND previous_until>?)", (token_hash,token_hash,int(time.time())))
    response.delete_cookie("edin_refresh", path="/api/v1/auth", secure=SECURE, httponly=True, samesite="strict")


@app.get("/api/v1/me", tags=["auth"], response_model=UserOut)
def me(user=Depends(current_user)):
    return user


@app.get("/api/v1/products", tags=["catalog"], response_model=CatalogOut)
def products():
    with connection() as db:
        return {"items": [dict(r) for r in db.execute("SELECT * FROM products ORDER BY CASE id WHEN 'drill' THEN 1 WHEN 'meter' THEN 2 WHEN 'helmet' THEN 3 WHEN 'tools' THEN 4 WHEN 'headphones' THEN 5 WHEN 'laser' THEN 6 WHEN 'gloves' THEN 7 ELSE 8 END")]}


class Item(StrictModel):
    product_id: str = Field(min_length=1, max_length=50)
    quantity: int = Field(ge=1, le=99, strict=True)


class NewOrder(StrictModel):
    items: list[Item] = Field(min_length=1, max_length=50)
    name: str = Field(min_length=2, max_length=80)
    phone: str = Field(min_length=10, max_length=25)
    address: str = Field(default="", max_length=300)
    delivery: Literal["pickup", "courier"]
    comment: str = Field(default="", max_length=1000)
    channel: Literal["web", "mobile"]

    @field_validator("phone")
    @classmethod
    def phone_valid(cls, value):
        digits = re.sub(r"\D", "", value)
        if len(digits) != 11 or digits[0] not in "78":
            raise ValueError("Укажите российский номер: +7 и 10 цифр")
        return "+7"+digits[1:]


def get_order(db, order_id, user, lock=False):
    row = db.execute("SELECT * FROM orders WHERE id=?"+(" FOR UPDATE" if lock and POSTGRES else ""), (order_id,)).fetchone()
    if not row or (row["user_id"] != user["id"] and user["role"] != "operator"):
        raise APIError(404, "ORDER_NOT_FOUND", "Заказ не найден")
    result = dict(row)
    result["items"] = [dict(r) for r in db.execute("SELECT product_id,name,price,quantity FROM order_items WHERE order_id=?", (order_id,))]
    result["history"] = [dict(r) for r in db.execute("SELECT id,status,created_at FROM order_events WHERE order_id=? ORDER BY id", (order_id,))]
    return result


@app.post("/api/v1/orders", status_code=201, tags=["orders"], response_model=OrderOut,
          responses={200: {"model": OrderOut, "description": "Повторный запрос с тем же Idempotency-Key: исходный заказ"}})
def create_order(data: NewOrder, response: Response, idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=128), user=Depends(current_user)):
    if data.delivery == "courier" and len(data.address.strip()) < 10:
        raise APIError(422, "ADDRESS_REQUIRED", "Укажите адрес доставки (не менее 10 символов)")
    ids = [i.product_id for i in data.items]
    if len(set(ids)) != len(ids):
        raise APIError(422, "DUPLICATE_PRODUCT", "Объедините одинаковые товары в одну позицию")
    body_hash = hashlib.sha256(data.model_dump_json().encode()).hexdigest()
    with transaction() as db:
        if POSTGRES:
            db.execute("SELECT pg_advisory_xact_lock(hashtextextended(?,0))", (user["id"]+":"+idempotency_key,))
        prior = db.execute("SELECT * FROM idempotency WHERE user_id=? AND key=?", (user["id"], idempotency_key)).fetchone()
        if prior:
            if prior["body_hash"] != body_hash:
                raise APIError(409, "IDEMPOTENCY_CONFLICT", "Этот ключ уже использован для другого заказа")
            response.status_code = 200
            return get_order(db, prior["order_id"], user)
        rows = []
        total = 0
        for item in sorted(data.items, key=lambda i: i.product_id):
            product = db.execute("SELECT * FROM products WHERE id=?"+(" FOR UPDATE" if POSTGRES else ""), (item.product_id,)).fetchone()
            if not product:
                raise APIError(422, "PRODUCT_NOT_FOUND", "Товар больше не доступен")
            if product["stock"] < item.quantity:
                raise APIError(409, "OUT_OF_STOCK", f"Недостаточно товара «{product['name']}»", {"product_id": item.product_id, "available": product["stock"]})
            total += product["price"] * item.quantity
            rows.append((product, item.quantity))
        fee = 49000 if data.delivery == "courier" and total < 1500000 else 0
        oid, timestamp = str(uuid.uuid4()), now()
        number = db.execute("SELECT nextval('order_number_seq')" if POSTGRES else "SELECT COALESCE(MAX(number),1040)+1 FROM orders").fetchone()[0]
        db.execute("INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (oid, number, user["id"], "new", 1, total+fee, fee, data.name, data.phone, data.address if data.delivery == "courier" else "Пункт выдачи: Воронеж, ул. Кольцовская, 35", data.delivery, data.comment, data.channel, timestamp, timestamp))
        for product, qty in rows:
            db.execute("INSERT INTO order_items VALUES(?,?,?,?,?)", (oid, product["id"], product["name"], product["price"], qty))
            db.execute("UPDATE products SET stock=stock-? WHERE id=?", (qty, product["id"]))
        db.execute("INSERT INTO order_events(order_id,user_id,actor_id,status,created_at) VALUES(?,?,?,?,?)", (oid, user["id"], user["id"], "new", timestamp))
        db.execute("INSERT INTO idempotency VALUES(?,?,?,?)", (user["id"], idempotency_key, body_hash, oid))
        response.headers["Location"] = f"/api/v1/orders/{oid}"
        return get_order(db, oid, user)


@app.get("/api/v1/orders", tags=["orders"], response_model=OrdersOut)
def orders(status: Literal["new", "confirmed", "assembling", "shipping", "completed", "cancelled"] | None = None, limit: int = 100, offset: int = 0, user=Depends(current_user)):
    if not 1 <= limit <= 100 or offset < 0:
        raise APIError(422, "INVALID_PAGE", "Некорректные параметры страницы")
    where, params = [], []
    if user["role"] != "operator":
        where.append("user_id=?"); params.append(user["id"])
    if status:
        where.append("status=?"); params.append(status)
    clause = " WHERE " + " AND ".join(where) if where else ""
    with connection() as db:
        count = db.execute("SELECT COUNT(*) FROM orders"+clause, params).fetchone()[0]
        ids = db.execute("SELECT id FROM orders"+clause+" ORDER BY created_at DESC LIMIT ? OFFSET ?", [*params, limit, offset]).fetchall()
        return {"items": [get_order(db, r["id"], user) for r in ids], "total": count, "limit": limit, "offset": offset}


@app.get("/api/v1/orders/{order_id}", tags=["orders"], response_model=OrderOut)
def order(order_id: str, user=Depends(current_user)):
    with connection() as db:
        return get_order(db, order_id, user)


class StatusChange(StrictModel):
    status: Literal["new", "confirmed", "assembling", "shipping", "completed", "cancelled"]
    version: int = Field(ge=1, strict=True)


@app.patch("/api/v1/orders/{order_id}/status", tags=["operator"], response_model=OrderOut)
def change_status(order_id: str, data: StatusChange, user=Depends(operator)):
    with transaction() as db:
        result = get_order(db, order_id, user, lock=True)
        if result["version"] != data.version:
            raise APIError(409, "VERSION_CONFLICT", "Заказ уже изменён. Обновите данные", {"version": result["version"]})
        if data.status not in STATUSES[result["status"]]:
            raise APIError(409, "INVALID_TRANSITION", "Этот переход статуса недоступен")
        ts = now()
        db.execute("UPDATE orders SET status=?,version=version+1,updated_at=? WHERE id=?", (data.status, ts, order_id))
        if data.status == "cancelled":
            for item in sorted(result["items"], key=lambda i: i["product_id"]):
                db.execute("UPDATE products SET stock=stock+? WHERE id=?", (item["quantity"], item["product_id"]))
        db.execute("INSERT INTO order_events(order_id,user_id,actor_id,status,created_at) VALUES(?,?,?,?,?)", (order_id, result["user_id"], user["id"], data.status, ts))
        return get_order(db, order_id, user)


@app.get("/api/v1/events", tags=["sync"], response_class=StreamingResponse)
async def events(request: Request, after: int = 0, last_event_id: str | None = Header(default=None), user=Depends(current_user)):
    try:
        cursor = max(0, int(last_event_id or after))
    except ValueError:
        raise APIError(422, "INVALID_CURSOR", "Некорректный курсор событий")
    async def generate():
        nonlocal cursor
        yield "retry: 2000\n\n"
        # Reconnect every 10 minutes to renew authentication and avoid an unbounded stream.
        for _ in range(600):
            if await request.is_disconnected():
                return
            try:
                await asyncio.to_thread(current_user, request.headers.get("authorization"))
            except APIError:
                return
            def read_events():
                with connection() as db:
                    if user["role"] == "operator":
                        return db.execute("SELECT id,order_id,status FROM order_events WHERE id>? ORDER BY id LIMIT 100", (cursor,)).fetchall()
                    return db.execute("SELECT id,order_id,status FROM order_events WHERE id>? AND user_id=? ORDER BY id LIMIT 100", (cursor, user["id"])).fetchall()
            rows = await asyncio.to_thread(read_events)
            for row in rows:
                cursor = row["id"]
                yield f"id: {cursor}\nevent: order.updated\ndata: {json.dumps(dict(row))}\n\n"
            if _ % 15 == 0:
                yield ": heartbeat\n\n"
            await asyncio.sleep(1)
    return StreamingResponse(generate(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache, no-transform"})


def contract():
    if app.openapi_schema:
        return app.openapi_schema
    from fastapi.openapi.utils import get_openapi
    schema = get_openapi(title=app.title, version=app.version, description=app.description, routes=app.routes)
    schema.setdefault("components", {}).setdefault("securitySchemes", {})["BearerAuth"] = {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
    for path, methods in schema["paths"].items():
        if path.startswith("/api/v1/orders") or path in ["/api/v1/me", "/api/v1/events"]:
            for method in methods.values():
                method["security"] = [{"BearerAuth": []}]
                method["parameters"] = [p for p in method.get("parameters", []) if p.get("name", "").lower() != "authorization"]
    app.openapi_schema = schema
    return schema


app.openapi = contract
static = Path(os.getenv("STATIC_DIR", "../frontend/dist"))
if static.exists():
    app.mount("/", StaticFiles(directory=static, html=True), name="frontend")
