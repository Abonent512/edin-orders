import importlib
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("COOKIE_SECURE", "false")
    # Never use a developer's DATABASE_URL implicitly. PostgreSQL tests use disposable DBs only.
    test_pg = os.getenv("TEST_POSTGRES_URL")
    pg_admin = None
    if test_pg:
        import psycopg
        from psycopg import sql
        pg_admin = psycopg.connect(test_pg, autocommit=True)
        db_name = "edin_test_"+uuid.uuid4().hex
        pg_admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))
        from psycopg.conninfo import conninfo_to_dict, make_conninfo
        info = conninfo_to_dict(test_pg)
        info["dbname"] = db_name
        monkeypatch.setenv("DATABASE_URL", make_conninfo(**info))
        monkeypatch.setenv("JWT_SECRET", "test-only-"+uuid.uuid4().hex+uuid.uuid4().hex)
    else:
        monkeypatch.setenv("DATABASE_URL", "")
    import app.database as database
    import app.main as main
    importlib.reload(database)
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c
    if pg_admin:
        if database._pool:
            database._pool.close()
        pg_admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(db_name)))
        pg_admin.close()


def auth(client, role="user"):
    r = client.post("/api/v1/auth/demo", json={"role": role})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer "+r.json()["access_token"]}


def payload(channel="web", quantity=1):
    return {"items": [{"product_id": "drill", "quantity": quantity}], "name": "Тестовый покупатель", "phone": "+79000000000", "delivery": "pickup", "channel": channel}


def create(client, headers, body=None, key=None):
    return client.post("/api/v1/orders", headers={**headers, "Idempotency-Key": key or str(uuid.uuid4())}, json=body or payload())


def test_web_mobile_same_order_and_operator_sync(client):
    web = auth(client)
    mobile = auth(client)
    op = auth(client, "operator")
    r = create(client, web)
    assert r.status_code == 201, r.text
    order = r.json()
    assert order["total"] == 1299000
    assert client.get("/api/v1/orders/"+order["id"], headers=mobile).json() == order
    r = client.patch(f"/api/v1/orders/{order['id']}/status", headers=op, json={"status": "confirmed", "version": 1})
    assert r.status_code == 200
    assert client.get("/api/v1/orders/"+order["id"], headers=web).json() == client.get("/api/v1/orders/"+order["id"], headers=mobile).json()
    assert r.json()["history"][-1]["status"] == "confirmed"


def test_server_roles_and_object_ownership(client):
    h = auth(client)
    oid = create(client, h).json()["id"]
    assert client.patch(f"/api/v1/orders/{oid}/status", headers=h, json={"status": "confirmed", "version": 1}).status_code == 403
    r = client.post("/api/v1/auth/register", json={"email": "other@example.ru", "password": "StrongPass123", "name": "Другой пользователь"})
    other = {"Authorization": "Bearer "+r.json()["access_token"]}
    assert client.get(f"/api/v1/orders/{oid}", headers=other).status_code == 404
    assert client.get("/api/v1/orders", headers=other).json()["total"] == 0
    assert client.get("/api/v1/orders").status_code == 401
    # Role escalation through registration is rejected.
    assert client.post("/api/v1/auth/register", json={"email": "attacker@example.ru", "password": "StrongPass123", "name": "Attacker", "role": "operator"}).status_code == 422


def test_idempotency_and_body_conflict(client):
    h=auth(client); key=str(uuid.uuid4())
    first=create(client,h,key=key)
    retry=create(client,h,key=key)
    assert first.status_code == 201 and retry.status_code == 200
    assert first.json()["id"] == retry.json()["id"]
    assert create(client,h,payload(quantity=2),key).status_code == 409
    assert client.get("/api/v1/orders",headers=h).json()["total"] == 1
    assert client.get("/api/v1/products").json()["items"][0]["stock"] == 23


@pytest.mark.parametrize("change", [{"phone":"123"},{"name":""},{"items":[]},{"items":[{"product_id":"drill","quantity":-1}]},{"items":[{"product_id":"drill","quantity":True}]},{"total":1},{"delivery":"courier","address":"123"}])
def test_validation(client,change):
    r=create(client,auth(client),{**payload(),**change})
    assert r.status_code == 422, r.text
    assert "code" in r.json()["error"]


def test_stock_atomicity_and_delivery_price(client):
    h=auth(client)
    assert create(client,h,payload(quantity=25)).status_code == 409
    assert client.get("/api/v1/products").json()["items"][0]["stock"] == 24
    r=create(client,h,{**payload(),"delivery":"courier","address":"Воронеж, улица Тестовая, 10"})
    assert r.json()["total"] == 1348000
    assert r.json()["delivery_fee"] == 49000
    r=create(client,h,{**payload(quantity=2),"delivery":"courier","address":"Воронеж, улица Тестовая, 10"})
    assert r.json()["total"] == 2598000 and r.json()["delivery_fee"] == 0


def test_optimistic_conflict_and_state_machine(client):
    h=auth(client);op=auth(client,"operator");oid=create(client,h).json()["id"]
    url=f"/api/v1/orders/{oid}/status"
    assert client.patch(url,headers=op,json={"status":"completed","version":1}).status_code==409
    assert client.patch(url,headers=op,json={"status":"confirmed","version":1}).status_code==200
    r=client.patch(url,headers=op,json={"status":"assembling","version":1})
    assert r.status_code==409 and r.json()["error"]["code"]=="VERSION_CONFLICT"
    assert client.patch(url,headers=op,json={"status":"cancelled","version":2}).status_code==200
    assert client.get("/api/v1/products").json()["items"][0]["stock"]==24
    assert client.patch(url,headers=op,json={"status":"cancelled","version":3}).status_code==409


def test_concurrent_last_stock_not_oversold(client):
    h=auth(client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:create(client,h,payload(quantity=20)),range(2)))
    assert sorted(r.status_code for r in results)==[201,409]
    assert client.get("/api/v1/products").json()["items"][0]["stock"]==4


def test_refresh_rotation_logout_and_revocation(client):
    h=auth(client)
    old_cookie=client.cookies.get("edin_refresh")
    r=client.post("/api/v1/auth/refresh")
    assert r.status_code==200 and client.cookies.get("edin_refresh")!=old_cookie
    # Rotation preserves session ID: another open web/mobile tab keeps its valid JWT.
    assert client.get("/api/v1/me",headers=h).status_code==200
    new={"Authorization":"Bearer "+r.json()["access_token"]}
    assert client.get("/api/v1/me",headers=new).status_code==200
    assert client.post("/api/v1/auth/logout").status_code==204
    assert client.get("/api/v1/me",headers=new).status_code==401
    assert client.get("/api/v1/me",headers=h).status_code==401


def test_two_tabs_shared_cookie_remain_authorized(client):
    first=auth(client)
    second={"Authorization":"Bearer "+client.post("/api/v1/auth/refresh").json()["access_token"]}
    third={"Authorization":"Bearer "+client.post("/api/v1/auth/refresh").json()["access_token"]}
    for headers in [first,second,third]:
        assert client.get("/api/v1/orders",headers=headers).status_code==200


def test_origin_and_contract(client):
    assert client.post("/api/v1/auth/refresh",headers={"Origin":"https://evil.example"}).status_code==403
    schema=client.get("/api/v1/openapi.json").json()
    assert schema["info"]["version"]=="1.0.0"
    assert "/api/v1/orders" in schema["paths"]
    assert client.get("/api/v1/health").json()["status"]=="ok"


def test_register_login_and_no_plaintext_password(client):
    body={"email":"person@example.ru","password":"GoodPassword123","name":"Покупатель"}
    r=client.post("/api/v1/auth/register",json=body)
    assert r.status_code==201
    assert client.post("/api/v1/auth/register",json=body).status_code==409
    assert client.post("/api/v1/auth/login",json={"email":body["email"],"password":body["password"]}).status_code==200
    assert client.post("/api/v1/auth/login",json={"email":body["email"],"password":"wrong"}).status_code==401
    from app.database import connection
    with connection() as db:
        hashed=db.execute("SELECT password_hash FROM users WHERE email=?",(body["email"],)).fetchone()[0]
    assert body["password"] not in hashed and hashed.startswith('scrypt$')
