"""Explicit operator bootstrap: no default production passwords."""
import argparse
import getpass
import os
import uuid
from .database import initialize, transaction
from .main import now, password_hash


def main():
    p=argparse.ArgumentParser(description="Создать или обновить учётную запись оператора")
    p.add_argument("email")
    p.add_argument("--name", default="Оператор")
    args=p.parse_args()
    password=getpass.getpass("Пароль оператора (минимум 12 символов): ")
    if len(password)<12:
        p.error("Нужен пароль не короче 12 символов")
    initialize()
    with transaction() as db:
        db.execute("INSERT INTO users VALUES(?,?,?,?,?,?) ON CONFLICT(email) DO UPDATE SET password_hash=excluded.password_hash,role='operator',name=excluded.name",(str(uuid.uuid4()),args.email.strip().lower(),args.name,password_hash(password),"operator",now()))
    print("Учётная запись оператора готова")


if __name__=="__main__":
    main()
