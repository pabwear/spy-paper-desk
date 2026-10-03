"""PIN-locked desk console, served only on this computer.

    python3 console.py                 # http://127.0.0.1:8765  (PIN required)
    python3 console.py --port 9000
    python3 console.py set-pin         # change the PIN (stored as a salted PBKDF2 hash)

Read-only: the console shows the desk; it cannot place orders.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import hmac
import json
import secrets
import sys
import threading
import time
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import rebuild_dashboard
from common import load_json, save_json

PIN_FILE = "console_pin.json"
COOKIE = "desk_session"
IDLE_SECONDS = 30 * 60
MAX_FAILS = 5
LOCKOUT_SECONDS = 5 * 60
HERE = Path(__file__).resolve().parent
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def hash_pin(pin: str, salt: bytes | None = None, iterations: int = 300_000) -> dict:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, iterations)
    return {"algo": "pbkdf2_sha256", "iterations": iterations, "salt": salt.hex(), "hash": digest.hex()}


def verify_pin(pin: str, record: dict) -> bool:
    if not record or record.get("algo") != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(record["salt"]), int(record["iterations"]))
    return hmac.compare_digest(digest.hex(), record["hash"])


class Guard:
    """Sessions and the wrong-PIN lockout, in memory."""

    def __init__(self, pin_record: dict):
        self.pin_record = pin_record
        self.sessions: dict[str, float] = {}
        self.fails = 0
        self.locked_until = 0.0
        self.lock = threading.Lock()

    def try_unlock(self, pin: str) -> tuple[str | None, str]:
        with self.lock:
            now = time.time()
            if now < self.locked_until:
                return None, f"Too many wrong PINs. Try again in {int(self.locked_until - now) + 1}s."
            if isinstance(pin, str) and pin.isdigit() and verify_pin(pin, self.pin_record):
                self.fails = 0
                token = secrets.token_urlsafe(32)
                self.sessions[token] = now
                return token, "ok"
            self.fails += 1
            if self.fails >= MAX_FAILS:
                self.fails = 0
                self.locked_until = now + LOCKOUT_SECONDS
                return None, f"Too many wrong PINs. Locked for {LOCKOUT_SECONDS // 60} minutes."
            return None, f"Wrong PIN. {MAX_FAILS - self.fails} tries left."

    def check(self, token: str | None) -> bool:
        with self.lock:
            seen = self.sessions.get(token or "")
            if seen is None:
                return False
            if time.time() - seen > IDLE_SECONDS:
                del self.sessions[token]
                return False
            self.sessions[token] = time.time()
            return True

    def drop(self, token: str | None) -> None:
        with self.lock:
            self.sessions.pop(token or "", None)


def make_handler(guard: Guard):
    class Handler(BaseHTTPRequestHandler):
        server_version = "SpyDesk"
        sys_version = ""

        def log_message(self, fmt, *args):  # keep PIN bodies and paths out of the terminal
            pass

        # -- helpers
        def _token(self) -> str | None:
            jar = SimpleCookie(self.headers.get("Cookie", ""))
            return jar[COOKIE].value if COOKIE in jar else None

        def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' "
                             "'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
                             "form-action 'self'; base-uri 'none'")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status: int, data, extra=None):
            self._send(status, json.dumps(data, default=str).encode(), "application/json; charset=utf-8", extra)

        def _allowed_hosts(self) -> set[str]:
            port = self.server.server_address[1]
            return {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}

        def _host_ok(self) -> bool:
            # Blocks DNS rebinding: only answer requests addressed to this machine.
            return self.headers.get("Host", "") in self._allowed_hosts()

        def _same_origin(self) -> bool:
            origin = self.headers.get("Origin")
            return origin is None or origin.removeprefix("http://") in self._allowed_hosts()

        # -- routes
        def do_GET(self):
            if not self._host_ok():
                return self._send(HTTPStatus.FORBIDDEN, b"Forbidden", "text/plain")
            route = self.path.split("?", 1)[0]
            authed = guard.check(self._token())
            if route == "/":
                page = "dashboard.html" if authed else "lock.html"
                return self._send(HTTPStatus.OK, (HERE / page).read_bytes(), "text/html; charset=utf-8")
            if route == "/api/state":
                if not authed:
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "locked"})
                try:
                    return self._json(HTTPStatus.OK, rebuild_dashboard.build_state())
                except Exception as e:  # noqa: BLE001 - surface file problems on the console
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"{type(e).__name__}: {e}"})
            return self._send(HTTPStatus.NOT_FOUND, b"Not found", "text/plain")

        do_HEAD = do_GET

        def do_POST(self):
            if not self._host_ok() or not self._same_origin():
                return self._send(HTTPStatus.FORBIDDEN, b"Forbidden", "text/plain")
            route = self.path.split("?", 1)[0]
            if route == "/unlock":
                length = min(int(self.headers.get("Content-Length") or 0), 256)
                try:
                    pin = json.loads(self.rfile.read(length) or b"{}").get("pin", "")
                except (ValueError, AttributeError):
                    pin = ""
                token, msg = guard.try_unlock(str(pin))
                if not token:
                    return self._json(HTTPStatus.UNAUTHORIZED, {"ok": False, "message": msg})
                cookie = f"{COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/"
                return self._json(HTTPStatus.OK, {"ok": True}, {"Set-Cookie": cookie})
            if route == "/lock":
                guard.drop(self._token())
                return self._json(HTTPStatus.OK, {"ok": True},
                                  {"Set-Cookie": f"{COOKIE}=; Max-Age=0; HttpOnly; SameSite=Strict; Path=/"})
            return self._send(HTTPStatus.NOT_FOUND, b"Not found", "text/plain")

    return Handler


def make_server(port: int = 8765, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    if host not in LOOPBACK:
        raise SystemExit("The console only listens on this computer (127.0.0.1).")
    record = load_json(PIN_FILE)
    if not record:
        raise SystemExit(f"No {PIN_FILE}. Run: python3 console.py set-pin")
    return ThreadingHTTPServer((host, port), make_handler(Guard(record)))


def set_pin() -> None:
    pin = getpass.getpass("New 4-digit PIN: ")
    if not (pin.isdigit() and len(pin) == 4):
        raise SystemExit("PIN must be exactly 4 digits (the keypad takes 4).")
    if getpass.getpass("Repeat PIN: ") != pin:
        raise SystemExit("PINs did not match.")
    save_json(PIN_FILE, hash_pin(pin))
    print(f"Saved {PIN_FILE}. Restart the console to use the new PIN.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PIN-locked SPY paper desk console (local only).")
    parser.add_argument("action", nargs="?", default="serve", choices=["serve", "set-pin"])
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    if args.action == "set-pin":
        set_pin()
        return 0
    server = make_server(args.port)
    print(f"SPY paper desk console: http://127.0.0.1:{server.server_address[1]}  (PIN locked, Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
