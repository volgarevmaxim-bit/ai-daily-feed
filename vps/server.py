#!/usr/bin/env python3
"""Feedback endpoint для AI Daily Feed.

Принимает оценки ленты (POST /feedback), отдаёт профиль предпочтений (GET /profile).
Только stdlib — работает на голом VPS. Хранит данные в feedback.json рядом.

Запуск:  python3 server.py [port]      (default 8099)
"""
import json, os, sys, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "feedback.json")


def load():
    try:
        return json.load(open(DATA, encoding="utf-8"))
    except Exception:
        return {"items": {}}


def save(d):
    tmp = DATA + ".tmp"
    json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
    os.replace(tmp, DATA)


class H(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if urlparse(self.path).path == "/profile":
            d = load()
            src = {}
            for it in d["items"].values():
                s = it.get("source") or "(unknown)"
                a = src.setdefault(s, {"pos": 0, "neg": 0})
                if it.get("v") == 1:
                    a["pos"] += 1
                elif it.get("v") == -1:
                    a["neg"] += 1
            prof = [{"source": s, "pos": v["pos"], "neg": v["neg"],
                     "score": v["pos"] - v["neg"]} for s, v in src.items()]
            prof.sort(key=lambda x: -x["score"])
            self._json(200, {"total": len(d["items"]), "sources": prof})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if urlparse(self.path).path == "/feedback":
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b"[]"
            try:
                items = json.loads(raw)
            except Exception:
                items = []
            if not isinstance(items, list):
                items = [items]
            d = load()
            for it in items:
                iid = it.get("id")
                if not iid:
                    continue
                d["items"][iid] = {
                    "id": iid, "v": it.get("v"), "ts": it.get("ts") or int(time.time() * 1000),
                    "title": it.get("title", ""), "source": it.get("source", ""),
                    "url": it.get("url", ""),
                }
            save(d)
            self._json(200, {"ok": True, "stored": len(d["items"])})
        else:
            self._json(404, {"error": "not found"})

    def log_message(self, *a):
        pass  # тихо


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8099
    print(f"feedback endpoint on 0.0.0.0:{port}, data in {DATA}")
    HTTPServer(("0.0.0.0", port), H).serve_forever()
