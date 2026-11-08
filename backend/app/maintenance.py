"""Consistent SQLite backups; use pg_dump for PostgreSQL deployments."""
import argparse
import hashlib
import json
import os
import sqlite3
import tarfile
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from .database import DATA_DIR, POSTGRES, connection


def backup(destination):
    if POSTGRES:
        raise RuntimeError("Для PostgreSQL используйте pg_dump: см. docs/OPERATIONS.md")
    destination.mkdir(parents=True, exist_ok=True,mode=0o700)
    destination.chmod(0o700)
    name="edin-"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+".tar.gz"
    target=destination/name
    with tempfile.TemporaryDirectory() as temporary:
        db_path=Path(temporary)/"orders.sqlite3"
        source=connection()
        dest=sqlite3.connect(db_path)
        try:
            source.backup(dest)
            assert dest.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
        finally:
            source.close();dest.close()
        with tarfile.open(target,"w:gz") as archive:
            archive.add(db_path,arcname="orders.sqlite3")
            key=DATA_DIR/".jwt-secret"
            if key.exists():
                archive.add(key,arcname=".jwt-secret")
    os.chmod(target,0o600)
    digest=hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix(target.suffix+".sha256").write_text(digest+"  "+target.name+"\n")
    # Retain 30 days; retain at least 7 successful archives even if the clock changes.
    files=sorted(destination.glob("edin-*.tar.gz"),key=lambda p:p.stat().st_mtime,reverse=True)
    for old in files[7:]:
        if old.stat().st_mtime<time.time()-30*86400:
            old.unlink();old.with_suffix(old.suffix+".sha256").unlink(missing_ok=True)
    print(json.dumps({"event":"backup_ok","file":name,"sha256":digest}),flush=True)
    return target


if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--directory",default="/backups");p.add_argument("--loop",action="store_true");args=p.parse_args()
    while True:
        try:
            backup(Path(args.directory))
        except Exception as error:
            print(json.dumps({"event":"backup_failed","type":type(error).__name__}),flush=True)
            if not args.loop: raise
        if not args.loop:break
        time.sleep(6*3600)
