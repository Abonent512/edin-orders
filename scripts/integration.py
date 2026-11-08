"""Real HTTP/SSE, process restart and SQLite backup verification (isolated temp DB)."""
import json
import os
import queue
import socket
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import uuid
from pathlib import Path

import httpx

ROOT=Path(__file__).resolve().parents[1]


def run():
    with tempfile.TemporaryDirectory(prefix="edin-integration-") as temporary:
        folder=Path(temporary)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1",0));port=sock.getsockname()[1]
        base=f"http://127.0.0.1:{port}"
        env={**os.environ,"PYTHONPATH":str(ROOT/"backend"),"DATA_DIR":str(folder/"data"),"DATABASE_URL":"","DEMO_MODE":"true","COOKIE_SECURE":"false","RENDER_EXTERNAL_URL":base,"STATIC_DIR":str(ROOT/"frontend/dist")}
        env.pop("ALLOWED_ORIGINS",None)
        env.pop("RENDER",None)
        logs=open(folder/"server.log","w")
        process=None
        def start():
            p=subprocess.Popen([sys.executable,"-m","uvicorn","app.main:app","--host","127.0.0.1","--port",str(port),"--no-access-log"],env=env,cwd=ROOT,stdout=logs,stderr=logs)
            for _ in range(100):
                try:
                    if httpx.get(base+"/api/v1/health",timeout=1).status_code==200:return p
                except httpx.HTTPError:pass
                if p.poll() is not None:raise RuntimeError("API failed to start; inspect server log")
                time.sleep(.1)
            p.terminate();raise RuntimeError("API startup timed out")
        def stop(p):
            p.terminate()
            try:p.wait(timeout=6)
            except subprocess.TimeoutExpired:p.kill();p.wait()
        try:
            process=start()
            with httpx.Client(base_url=base,timeout=15,headers={"Origin":base}) as web,httpx.Client(base_url=base,timeout=15,headers={"Origin":base}) as mobile,httpx.Client(base_url=base,timeout=15,headers={"Origin":base}) as operator:
                def login(client,role):
                    r=client.post("/api/v1/auth/demo",json={"role":role});assert r.status_code==200
                    return {"Authorization":"Bearer "+r.json()["access_token"]}
                wh=login(web,"user");mh=login(mobile,"user");oh=login(operator,"operator")
                body={"items":[{"product_id":"meter","quantity":2}],"name":"Интеграционный тест","phone":"+79000000000","delivery":"pickup","channel":"mobile"}
                key=str(uuid.uuid4())
                r=mobile.post("/api/v1/orders",json=body,headers={**mh,"Idempotency-Key":key});assert r.status_code==201,r.text
                order=r.json();oid=order["id"]
                assert web.get("/api/v1/orders/"+oid,headers=wh).json()==order
                events=queue.Queue()
                def listen():
                    try:
                        with httpx.stream("GET",base+"/api/v1/events?after="+str(order["history"][-1]["id"]),headers=wh,timeout=12) as response:
                            assert response.status_code==200
                            events.put("connected")
                            for line in response.iter_lines():
                                if line.startswith("data: "):
                                    value=json.loads(line[6:])
                                    if value["order_id"]==oid and value["status"]=="confirmed":events.put(value);return
                    except BaseException as error:events.put(error)
                thread=threading.Thread(target=listen,daemon=True);thread.start()
                assert events.get(timeout=5)=="connected"
                r=operator.patch(f"/api/v1/orders/{oid}/status",headers=oh,json={"status":"confirmed","version":1});assert r.status_code==200,r.text
                event=events.get(timeout=8);assert isinstance(event,dict),str(event)
                assert event["status"]=="confirmed";thread.join(timeout=2)
                assert web.get("/api/v1/orders/"+oid,headers=wh).json()==mobile.get("/api/v1/orders/"+oid,headers=mh).json()
                assert mobile.post("/api/v1/orders",json=body,headers={**mh,"Idempotency-Key":key}).json()["id"]==oid
                # Static deliveries are actually included, not references to a dev server.
                for path in ['/','/mobile/','/api/docs','/docs-assets/swagger-ui-bundle.js','/icon-192.png','/downloads/edin-android-demo.apk']:
                    response=web.get(path);assert response.status_code==200,(path,response.status_code)
                    if path.endswith('.apk'):
                        assert response.content==(ROOT/'releases/edin-android-demo.apk').read_bytes()
                stop(process);process=start()
                after=web.get("/api/v1/orders/"+oid,headers=wh)
                assert after.status_code==200 and after.json()["status"]=="confirmed"
                subprocess.run([sys.executable,"-m","app.maintenance","--directory",str(folder/"backups")],env=env,cwd=ROOT,check=True,capture_output=True)
                archive=next((folder/"backups").glob('*.tar.gz'))
                with tarfile.open(archive) as tar:
                    contents=tar.extractfile('orders.sqlite3').read()
                copy=folder/'restored.sqlite3';copy.write_bytes(contents)
                with sqlite3.connect(copy) as db:
                    assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
                    assert db.execute('SELECT status FROM orders WHERE id=?',(oid,)).fetchone()[0]=='confirmed'
                print(json.dumps({"result":"PASS","checks":["real_http_web_mobile_same_order","operator_status_to_live_SSE","equal_clients_after_update","idempotent_retry","static_web_mobile_docs_assets","orders_and_sessions_survive_restart","consistent_backup_integrity_and_order"]},ensure_ascii=False))
        finally:
            if process and process.poll() is None:stop(process)
            logs.close()


if __name__=="__main__":run()
