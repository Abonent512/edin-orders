"""Single-host transactional storage. All prices are integer kopecks."""
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
DATABASE_URL = os.getenv("DATABASE_URL", "")
POSTGRES = bool(DATABASE_URL)
_pool = None


class Row(dict):
    def __getitem__(self, key):
        return list(self.values())[key] if isinstance(key, int) else super().__getitem__(key)


class Cursor:
    def __init__(self, cursor):
        self.cursor = cursor

    def fetchone(self):
        value = self.cursor.fetchone()
        return Row(value) if value is not None else None

    def fetchall(self):
        return [Row(row) for row in self.cursor.fetchall()]

    def __iter__(self):
        return iter(self.fetchall())


class PostgresConnection:
    def __init__(self):
        global _pool
        if _pool is None:
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool
            _pool = ConnectionPool(DATABASE_URL, min_size=0, max_size=5, timeout=20, max_idle=60, open=True,
                check=ConnectionPool.check_connection,
                kwargs={"autocommit": True, "row_factory": dict_row, "connect_timeout": 15,
                        "prepare_threshold": None, "options": "-c statement_timeout=15000 -c lock_timeout=5000"})
        self.pool = _pool
        self.db = self.pool.getconn()

    @staticmethod
    def translate(sql):
        sql = sql.strip().replace("?", "%s")
        if sql.startswith("INSERT OR IGNORE"):
            sql = sql.replace("INSERT OR IGNORE", "INSERT", 1).rstrip(";")+" ON CONFLICT DO NOTHING"
        return sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")

    def execute(self, sql, params=()):
        return Cursor(self.db.execute(self.translate(sql), params))

    def executemany(self, sql, params):
        with self.db.cursor() as cursor:
            cursor.executemany(self.translate(sql), params)

    def executescript(self, sql):
        for statement in sql.split(";"):
            if statement.strip():
                self.execute(statement)

    def commit(self):
        self.db.commit()

    def rollback(self):
        self.db.rollback()

    def close(self):
        self.pool.putconn(self.db)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def connection():
    if POSTGRES:
        return PostgresConnection()
    DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    DATA_DIR.chmod(0o700)
    db = sqlite3.connect(DATA_DIR / "orders.sqlite3", timeout=15, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=15000")
    db.execute("PRAGMA synchronous=FULL")
    return db


@contextmanager
def transaction():
    db = connection()
    try:
        db.execute("BEGIN" if POSTGRES else "BEGIN IMMEDIATE")
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


PRODUCTS = [
    ("drill", "Дрель-шуруповёрт", "Аккумуляторный · 18 В · 2 А·ч", "ИН-001", "Инструменты", 1299000, 24, "drill", "Хит продаж"),
    ("meter", "Мультиметр цифровой", "True RMS · CAT III · 600 В", "ИН-002", "Измерение", 649000, 38, "meter", "Выбор мастера"),
    ("helmet", "Каска защитная", "ABS-пластик · регулировка 53–63 см", "ЗА-001", "Защита", 189000, 120, "helmet", ""),
    ("tools", "Набор инструментов", "46 предметов · хромованадиевая сталь", "ИН-003", "Инструменты", 899000, 18, "tools", ""),
    ("headphones", "Наушники противошумные", "Снижение шума 27 дБ · мягкое оголовье", "ЗА-002", "Защита", 349000, 45, "headphones", "Новинка"),
    ("laser", "Лазерный дальномер", "Дальность 50 м · точность ±2 мм", "ИН-004", "Измерение", 729000, 16, "laser", ""),
    ("gloves", "Перчатки рабочие", "Нитриловое покрытие · размер L · 5 пар", "РА-001", "Расходники", 79000, 200, "gloves", ""),
    ("bits", "Набор бит и свёрл", "32 предмета · универсальный кейс", "РА-002", "Расходники", 249000, 62, "bits", ""),
]


def initialize():
    if os.getenv("RENDER") and not POSTGRES:
        raise RuntimeError("Render requires DATABASE_URL: local SQLite would lose orders on restart.")
    if POSTGRES and len(os.getenv("JWT_SECRET", "")) < 32:
        raise RuntimeError("PostgreSQL requires a persistent JWT_SECRET of at least 32 characters. Set it in hosting secrets.")
    with connection() as db:
        if not POSTGRES:
            db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
        CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY);
        INSERT OR IGNORE INTO schema_version VALUES(1);
        CREATE TABLE IF NOT EXISTS users(
          id TEXT PRIMARY KEY,email TEXT UNIQUE NOT NULL,name TEXT NOT NULL,
          password_hash TEXT NOT NULL,role TEXT NOT NULL CHECK(role IN ('user','operator')),
          created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(
          id TEXT PRIMARY KEY,token_hash TEXT UNIQUE NOT NULL,previous_hash TEXT,previous_until INTEGER NOT NULL,
          user_id TEXT NOT NULL REFERENCES users(id),
          expires_at INTEGER NOT NULL,created_at INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
        CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires_at);
        CREATE TABLE IF NOT EXISTS products(
          id TEXT PRIMARY KEY,name TEXT NOT NULL,description TEXT NOT NULL,
          sku TEXT NOT NULL UNIQUE,category TEXT NOT NULL,price INTEGER NOT NULL CHECK(price>0),
          stock INTEGER NOT NULL CHECK(stock>=0),art TEXT NOT NULL,badge TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS orders(
          id TEXT PRIMARY KEY,number INTEGER UNIQUE NOT NULL,user_id TEXT NOT NULL REFERENCES users(id),
          status TEXT NOT NULL CHECK(status IN ('new','confirmed','assembling','shipping','completed','cancelled')),
          version INTEGER NOT NULL DEFAULT 1,total INTEGER NOT NULL,delivery_fee INTEGER NOT NULL,
          name TEXT NOT NULL,phone TEXT NOT NULL,address TEXT NOT NULL,delivery TEXT NOT NULL,
          comment TEXT NOT NULL,channel TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS orders_owner ON orders(user_id,created_at DESC);
        CREATE INDEX IF NOT EXISTS orders_status ON orders(status,created_at DESC);
        CREATE TABLE IF NOT EXISTS order_items(
          order_id TEXT NOT NULL REFERENCES orders(id),product_id TEXT NOT NULL REFERENCES products(id),
          name TEXT NOT NULL,price INTEGER NOT NULL,quantity INTEGER NOT NULL CHECK(quantity>0),
          PRIMARY KEY(order_id,product_id));
        CREATE TABLE IF NOT EXISTS order_events(
          id INTEGER PRIMARY KEY AUTOINCREMENT,order_id TEXT NOT NULL REFERENCES orders(id),
          user_id TEXT NOT NULL REFERENCES users(id),actor_id TEXT NOT NULL REFERENCES users(id),
          status TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS events_owner ON order_events(user_id,id);
        CREATE TABLE IF NOT EXISTS idempotency(
          user_id TEXT NOT NULL,key TEXT NOT NULL,body_hash TEXT NOT NULL,order_id TEXT NOT NULL REFERENCES orders(id),
          PRIMARY KEY(user_id,key));
        CREATE TABLE IF NOT EXISTS rate_limits(key TEXT PRIMARY KEY,attempts INTEGER NOT NULL,reset_at INTEGER NOT NULL);
        """)
        db.executemany("INSERT OR IGNORE INTO products VALUES(?,?,?,?,?,?,?,?,?)", PRODUCTS)
        if POSTGRES:
            db.execute("CREATE SEQUENCE IF NOT EXISTS order_number_seq START WITH 1041")
    if POSTGRES:
        return
    secret_path = DATA_DIR / ".jwt-secret"
    try:
        fd = os.open(secret_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_hex(48))


def secret():
    return os.getenv("JWT_SECRET") or (DATA_DIR / ".jwt-secret").read_text().strip()
