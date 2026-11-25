#!/usr/bin/env python3
# © 2026 Юрій Падляк (Yuriy Padlyak). «Класні ігри».
# Ліцензія CC BY-NC-SA 4.0 (див. LICENSE). Продаж або платне поширення лише за окремою ліцензією автора.
"""Класний сервер для апки «Реферат до друку».

Запуск на вчительському ноутбуці:
    python3 format-server.py            # порт 8000
    python3 format-server.py 8080       # інший порт
    python3 format-server.py --reset    # почати рейтинг з нуля

Учні відкривають  http://<IP ноутбука>:8000
Проєктор:         http://localhost:8000/board
Лише стандартна бібліотека Python, нічого не треба встановлювати.
"""
import json
import os
import socket
import ssl
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "format.html")
BOARD = os.path.join(HERE, "format-board.html")
DATA = os.path.join(HERE, "format-scores.json")
CERT_DIR = os.path.join(HERE, ".house-cert")
https_on = False
tls_ctx = None
HEAD = ('<!doctype html><html lang="uk"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">\n')

lock = threading.Lock()
players = {}
ceremony = None  # frozen top-3 while prizes are being handed out
round_ = None  # class timer: {"id", "minutes", "ends" (epoch ms)}
session = ""  # class session id; browsers from an older session forget their state


def new_session():
    return os.urandom(6).hex()


def load():
    global players, session
    try:
        with open(DATA, encoding="utf-8") as f:
            d = json.load(f)
        if "players" in d and "session" in d:
            players, session = d["players"], d["session"]
        else:  # old file format: just the players
            players, session = d, new_session()
    except (OSError, ValueError, TypeError):
        players, session = {}, new_session()


def persist():
    tmp = DATA + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"session": session, "players": players}, f, ensure_ascii=False)
    os.replace(tmp, DATA)


def clear_class():
    """New class: empty the board and make every open browser start fresh."""
    global players, session, ceremony, round_
    players, session, ceremony, round_ = {}, new_session(), None, None
    persist()


def num(v, lo=0, hi=100000):
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return lo


def board():
    now = time.time()
    rows = []
    for p in players.values():
        c, b = p.get("cur") or {}, p.get("best")
        # only finished attempts count; an unfinished one only shows progress
        rows.append({
            "name": p["name"],
            "score": b["score"] if b else 0,
            "finished": bool(b),
            "step": c.get("step", 0),
            "steps": c.get("steps", 8),
            "stars": b["stars"] if b else 0,
            "time": b["time"] if b else 0,
            "badges": b.get("badges", []) if b else [],
            "drawing": bool(c) and not c.get("finished") and now - p.get("seen", 0) < 90,
        })
    rows.sort(key=lambda r: (not r["finished"], -r["score"], r["time"], -r["step"]))
    return rows


def round_state():
    if not round_:
        return None
    return dict(round_, over=time.time() * 1000 > round_["ends"])


def round_closed():
    """True once the class timer has run out (5 s grace for slow wifi)."""
    return bool(round_) and time.time() * 1000 > round_["ends"] + 5000


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # no packet is sent, only picks the outgoing interface
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def make_cert(ip):
    """Self-signed certificate so browsers allow the camera (needs https outside localhost)."""
    os.makedirs(CERT_DIR, exist_ok=True)
    # one key per address: a shared key.pem got overwritten on another network and broke older certs
    crt, key = os.path.join(CERT_DIR, f"cert-{ip}.pem"), os.path.join(CERT_DIR, f"key-{ip}.pem")
    if os.path.exists(crt) and os.path.exists(key):
        try:
            ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(crt, key)
            return crt, key
        except (OSError, ssl.SSLError):
            pass  # mismatched or broken pair: make a fresh one
    base = ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "825",
            "-keyout", key, "-out", crt, "-subj", "/CN=house-perspective"]
    san = ["-addext", f"subjectAltName=IP:{ip},DNS:localhost,IP:127.0.0.1"]
    for cmd in (base + san, base):
        if subprocess.run(cmd, capture_output=True).returncode == 0:
            return crt, key
    return None


def public_url():
    ip = lan_ip()
    return f"{'https' if https_on else 'http'}://{ip}:{PORT}"


class DualServer(ThreadingHTTPServer):
    """One port for both http:// and https://: peek at the first byte of each connection."""
    def finish_request(self, request, client_address):
        if tls_ctx is not None:
            try:
                request.settimeout(10)
                first = request.recv(1, socket.MSG_PEEK)
                if first == b"\x16":  # TLS handshake record
                    request = tls_ctx.wrap_socket(request, server_side=True)
                request.settimeout(None)
            except (OSError, ssl.SSLError):
                return
        try:
            super().finish_request(request, client_address)
        except (ssl.SSLError, ConnectionError):
            pass


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def json(self, obj, code=200):
        self.send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            with open(APP, encoding="utf-8") as f:
                self.send(200, (HEAD + f.read() + "\n</html>").encode(), "text/html; charset=utf-8")
        elif path == "/board":
            with open(BOARD, encoding="utf-8") as f:
                self.send(200, f.read().encode(), "text/html; charset=utf-8")
        elif path == "/api/ping":
            self.json({"ok": True, "app": "format", "session": session})
        elif path == "/api/board":
            with lock:
                self.json({"board": board(), "url": public_url(), "ceremony": ceremony, "session": session,
                           "round": round_state(), "now": int(time.time() * 1000),
                           "teacher": self.is_local()})
        else:
            self.send(404, b"not found", "text/plain")

    def is_local(self):
        return self.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1")

    def do_POST(self):
        global ceremony, round_
        path = self.path.split("?")[0]
        if path == "/api/clear":
            if not self.is_local():
                return self.json({"error": "only from the teacher laptop"}, 403)
            with lock:
                clear_class()
                return self.json({"ok": True, "session": session})
        if path == "/api/round":
            if not self.is_local():
                return self.json({"error": "only from the teacher laptop"}, 403)
            n = num(self.headers.get("Content-Length"), 0, 1024)
            try:
                d = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                d = {}
            with lock:
                if d.get("action") == "start":
                    mins = num(d.get("minutes"), 1, 60)
                    now = int(time.time() * 1000)
                    round_ = {"id": str(now), "minutes": mins, "ends": now + mins * 60000}
                elif d.get("action") == "stop":
                    round_ = None
                return self.json({"ok": True, "round": round_state()})
        if path == "/api/ceremony":
            if not self.is_local():
                return self.json({"error": "only from the teacher laptop"}, 403)
            n = num(self.headers.get("Content-Length"), 0, 1024)
            try:
                action = json.loads(self.rfile.read(n) or b"{}").get("action")
            except ValueError:
                action = None
            with lock:
                if action == "start":
                    top = [{"name": r["name"], "score": r["score"], "stars": r["stars"], "time": r["time"]}
                           for r in board() if r["finished"]][:3]
                    ceremony = {"id": str(int(time.time() * 1000)), "top": top}
                elif action == "stop":
                    ceremony = None
                return self.json({"ok": True, "ceremony": ceremony})
        if path != "/api/progress":
            return self.send(404, b"not found", "text/plain")
        n = num(self.headers.get("Content-Length"), 0, 8192)
        try:
            d = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self.json({"error": "bad json"}, 400)
        name = " ".join(str(d.get("name", "")).split())[:40]
        if len(name) < 2:
            return self.json({"error": "name"}, 400)
        row = {
            "attempt": str(d.get("attempt", ""))[:16],
            "score": num(d.get("score"), 0, 5000),
            "step": num(d.get("step"), 0, 20),
            "steps": num(d.get("steps"), 1, 20),
            "stars": num(d.get("stars"), 0, 60),
            "mistakes": num(d.get("mistakes"), 0, 10000),
            "time": num(d.get("time"), 0, 100000),
            "finished": bool(d.get("finished")),
            "badges": [str(b)[:20] for b in (d.get("badges") or [])][:8],
        }
        with lock:
            if d.get("session") != session:  # a browser left over from an earlier class
                return self.json({"ok": False, "stale": True, "session": session})
            if ceremony or round_closed():  # frozen: award ceremony or time is up
                return self.json({"ok": True, "frozen": True, "board": board(), "ceremony": ceremony,
                                  "round": round_state(), "now": int(time.time() * 1000)})
            p = players.setdefault(name.lower(), {"name": name, "best": None})
            p["name"], p["cur"], p["seen"] = name, row, time.time()
            if row["finished"] and (not p["best"] or row["score"] > p["best"]["score"]):
                p["best"] = row
            persist()
            self.json({"ok": True, "board": board(), "ceremony": ceremony,
                       "round": round_state(), "now": int(time.time() * 1000)})


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    PORT = int(args[0]) if args else 8000
    if "--reset" in sys.argv:
        clear_class()
    else:
        load()
    ip = lan_ip()
    cert = None if "--no-https" in sys.argv else make_cert(ip)
    if cert:
        try:
            tls_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            tls_ctx.load_cert_chain(*cert)
            https_on = True
        except (OSError, ssl.SSLError) as e:
            tls_ctx = None
            print(f"  https не запустився: {e}")
    srv = DualServer(("0.0.0.0", PORT), H)
    if https_on:
        print(f"\n  Учням: https://{ip}:{PORT}   (працює і http://{ip}:{PORT}, але без камери)")
        print("     браузер попередить про сертифікат: Додатково, потім Перейти / Прийняти ризик")
    else:
        print(f"\n  Учням: http://{ip}:{PORT}")
    print(f"  Проєктор:  http://localhost:{PORT}/board")
    print(f"  Рейтинг зберігається в {DATA}")
    print("  Зупинити: Ctrl+C\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
