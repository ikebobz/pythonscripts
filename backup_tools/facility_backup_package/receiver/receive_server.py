"""
Backup receiver: runs on the central computer and accepts backup zips streamed
by ship_backups.py from facility PCs over Tailscale.

Files are stored as  <DEST_DIR>/<facility>/<name>.zip  plus <name>.zip.sha256.
Unfinished uploads live in <DEST_DIR>/<facility>/.partial/ and are resumed from
where they stopped; a file only appears in the facility folder once its full
SHA-256 has been verified.

API (all calls need header X-Token: <RECEIVER_TOKEN>):
  GET /api/files/<facility>/<name>   -> {"received": bytes, "complete": bool, "sha256": ...}
  PUT /api/files/<facility>/<name>   headers X-Offset, X-Total-Size, X-SHA256; body = bytes
                                     from X-Offset to the end of the file
  GET /health                        -> {"ok": true}   (no token needed)

Settings come from receiver_config.bat (--config) or the command line.
"""
import argparse
import hashlib
import hmac
import json
import logging
import logging.handlers
import os
import re
import secrets
import socket
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

log = logging.getLogger("receiver")
SAFE_FACILITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
SAFE_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\.zip$")
CHUNK = 1024 * 1024


def read_bat_config(path):
    """Read 'set NAME=VALUE' lines from a .bat file (expands %~dp0 and %VAR%)."""
    path = Path(path).resolve()
    values, dp0 = {}, str(path.parent) + os.sep

    def expand(text):
        text = text.replace("%~dp0", dp0)
        return re.sub(r"%([A-Za-z_][A-Za-z0-9_]*)%",
                      lambda m: values.get(m.group(1).upper(),
                                           os.environ.get(m.group(1), m.group(0))), text)

    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r'^\s*set\s+"?([A-Za-z_][A-Za-z0-9_]*)=(.*?)"?\s*$', line, re.IGNORECASE)
        if m:
            values[m.group(1).upper()] = expand(m.group(2))
    return values


def init_token(config_path):
    """Fill in an empty RECEIVER_TOKEN in the config file and print it."""
    with open(config_path, encoding="utf-8", newline="") as f:
        text = f.read()
    m = re.search(r"(?im)^([ \t]*set[ \t]+RECEIVER_TOKEN=)([^\r\n]*)", text)
    if m and m.group(2).strip():
        print(m.group(2).strip())
        return
    token = secrets.token_urlsafe(32)
    if m:
        text = text[:m.start()] + m.group(1) + token + text[m.end():]
    else:
        text += f"\r\nset RECEIVER_TOKEN={token}\r\n"
    with open(config_path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    print(token)


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


class Store:
    def __init__(self, dest):
        self.dest = Path(dest)
        self.locks = {}
        self.locks_guard = threading.Lock()

    def lock_for(self, key):
        with self.locks_guard:
            return self.locks.setdefault(key, threading.Lock())

    def paths(self, facility, name):
        fdir = self.dest / facility
        pdir = fdir / ".partial"
        return (fdir / name, fdir / (name + ".sha256"),
                pdir / (name + ".part"), pdir / (name + ".meta.json"))

    def status(self, facility, name):
        final, sidecar, part, meta = self.paths(facility, name)
        if final.exists():
            sha = sidecar.read_text().strip() if sidecar.exists() else sha256_of(final)
            return {"received": final.stat().st_size, "complete": True, "sha256": sha}
        info = {}
        if meta.exists():
            try:
                info = json.loads(meta.read_text())
            except ValueError:
                info = {}
        return {"received": part.stat().st_size if part.exists() else 0,
                "complete": False, "sha256": info.get("sha256"), "total": info.get("total")}


class Handler(BaseHTTPRequestHandler):
    server_version = "FacilityBackupReceiver/1.0"
    protocol_version = "HTTP/1.1"
    timeout = 60     # seconds of silence before a stalled upload is dropped

    def log_message(self, fmt, *args):
        log.debug("%s %s", self.client_address[0], fmt % args)

    def reply(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        given = self.headers.get("X-Token", "")
        if hmac.compare_digest(given.encode(), self.server.token.encode()):
            return True
        log.warning("Rejected request from %s: bad token", self.client_address[0])
        self.close_connection = True
        self.reply(401, {"error": "bad token"})
        return False

    def target(self):
        parts = [urllib.parse.unquote(p) for p in urllib.parse.urlparse(self.path).path.split("/")]
        # ['', 'api', 'files', facility, name]
        if len(parts) != 5 or parts[1:3] != ["api", "files"]:
            return None
        facility, name = parts[3], parts[4]
        if not SAFE_FACILITY.match(facility) or not SAFE_FILE.match(name):
            return None
        return facility, name

    def do_GET(self):
        if self.path == "/health":
            return self.reply(200, {"ok": True})
        if not self.authorized():
            return
        t = self.target()
        if not t:
            return self.reply(404, {"error": "not found"})
        self.reply(200, self.server.store.status(*t))

    def do_PUT(self):
        if not self.authorized():
            return
        t = self.target()
        if not t:
            self.close_connection = True
            return self.reply(404, {"error": "not found"})
        facility, name = t
        try:
            offset = int(self.headers["X-Offset"])
            total = int(self.headers["X-Total-Size"])
            sha = self.headers["X-SHA256"].strip().lower()
            length = int(self.headers["Content-Length"])
            if offset < 0 or total < 0 or length != total - offset or not re.fullmatch(r"[0-9a-f]{64}", sha):
                raise ValueError
        except (TypeError, ValueError, KeyError):
            self.close_connection = True
            return self.reply(400, {"error": "bad or missing X-Offset / X-Total-Size / X-SHA256"})

        lock = self.server.store.lock_for((facility, name))
        if not lock.acquire(timeout=5):
            self.close_connection = True
            return self.reply(423, {"error": "busy with an earlier attempt, retry shortly"})
        try:
            self.receive(facility, name, offset, total, sha, length)
        finally:
            lock.release()

    def receive(self, facility, name, offset, total, sha, length):
        store = self.server.store
        final, sidecar, part, meta = store.paths(facility, name)
        part.parent.mkdir(parents=True, exist_ok=True)

        if final.exists():
            self.close_connection = True
            return self.reply(200, store.status(facility, name))

        # A different file (other checksum or size) under the same name: start over.
        cur = store.status(facility, name)
        if cur["sha256"] not in (None, sha) or (cur["total"] not in (None, total)):
            log.info("%s/%s: new version of file, discarding old partial upload", facility, name)
            part.unlink(missing_ok=True)
            cur["received"] = 0
        if cur["received"] != offset:
            self.close_connection = True
            return self.reply(409, {"error": "offset mismatch", "received": cur["received"]})
        meta.write_text(json.dumps({"sha256": sha, "total": total}))

        got = 0
        with open(part, "ab") as f:
            try:
                while got < length:
                    buf = self.rfile.read(min(CHUNK, length - got))
                    if not buf:
                        break
                    f.write(buf)
                    got += len(buf)
            except (socket.timeout, ConnectionError, OSError) as e:
                log.warning("%s/%s: connection lost after %s new bytes (%s); kept for resume",
                            facility, name, f"{got:,}", e)
            finally:
                f.flush()
                os.fsync(f.fileno())

        size = part.stat().st_size
        if got < length:
            self.close_connection = True
            log.info("%s/%s: partial, %s of %s bytes stored", facility, name,
                     f"{size:,}", f"{total:,}")
            try:
                self.reply(200, {"received": size, "complete": False, "sha256": sha})
            except OSError:
                pass
            return

        if size != total:
            return self.reply(409, {"error": "size mismatch", "received": size})
        actual = sha256_of(part)
        if actual != sha:
            log.error("%s/%s: checksum mismatch, discarding upload", facility, name)
            part.unlink(missing_ok=True)
            meta.unlink(missing_ok=True)
            return self.reply(422, {"error": "checksum mismatch", "received": 0})
        os.replace(part, final)
        sidecar.write_text(sha + "\n")
        meta.unlink(missing_ok=True)
        log.info("%s/%s: received complete (%s bytes, sha256 ok)", facility, name, f"{total:,}")
        self.reply(200, {"received": total, "complete": True, "sha256": sha})


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="settings file (default: receiver_config.bat next to this script)")
    ap.add_argument("--dest"), ap.add_argument("--bind"), ap.add_argument("--port", type=int)
    ap.add_argument("--token")
    ap.add_argument("--log")
    ap.add_argument("--init-token", action="store_true",
                    help="create RECEIVER_TOKEN in --config if empty, print it, exit")
    a = ap.parse_args()

    if not a.config and Path(__file__).with_name("receiver_config.bat").exists():
        a.config = str(Path(__file__).with_name("receiver_config.bat"))
    if a.init_token:
        if not a.config:
            ap.error("--init-token needs --config")
        init_token(a.config)
        return 0

    cfg = read_bat_config(a.config) if a.config else {}
    dest = a.dest or cfg.get("DEST_DIR") or str(Path(__file__).with_name("received"))
    bind = a.bind or cfg.get("RECEIVER_BIND") or "0.0.0.0"
    port = a.port or int(cfg.get("RECEIVER_PORT") or 8765)
    token = a.token or cfg.get("RECEIVER_TOKEN") or os.environ.get("RECEIVER_TOKEN")
    log_file = a.log or str(Path(__file__).resolve().with_name("logs") / "receiver.log")

    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    handlers = [logging.handlers.RotatingFileHandler(log_file, maxBytes=5_000_000,
                                                     backupCount=5, encoding="utf-8")]
    if sys.stdout is not None:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(message)s")

    if not token or len(token) < 16:
        log.error("RECEIVER_TOKEN must be set (at least 16 characters). "
                  "Run setup_receiver.bat to generate one.")
        return 2

    Path(dest).mkdir(parents=True, exist_ok=True)
    httpd = ThreadingHTTPServer((bind, port), Handler)
    httpd.daemon_threads = True
    httpd.token = token
    httpd.store = Store(dest)
    log.info("Receiver listening on %s:%d, saving to %s", bind, port, dest)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
