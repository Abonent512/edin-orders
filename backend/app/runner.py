"""Supervise API health so Docker restart policy also covers a hung process."""
import os
import signal
import subprocess
import sys
import time
import urllib.request


def main():
    port=os.getenv("PORT","8000")
    child=subprocess.Popen([sys.executable,"-m","uvicorn","app.main:app","--host","0.0.0.0","--port",port,"--proxy-headers","--forwarded-allow-ips",os.getenv("FORWARDED_ALLOW_IPS","127.0.0.1"),"--no-access-log","--timeout-graceful-shutdown","15"])
    def stop(*_):
        child.terminate()
        try:child.wait(timeout=20)
        except subprocess.TimeoutExpired:child.kill()
        sys.exit(0)
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    failures=0
    while child.poll() is None:
        time.sleep(20)
        try:
            with urllib.request.urlopen("http://127.0.0.1:"+port+"/api/v1/health",timeout=10) as r:
                if r.status!=200:raise RuntimeError()
            failures=0
        except Exception:
            failures+=1
            if failures>=3:
                child.terminate()
                try:child.wait(timeout=20)
                except subprocess.TimeoutExpired:child.kill()
                return 1
    return child.returncode or 1


if __name__=="__main__":sys.exit(main())
