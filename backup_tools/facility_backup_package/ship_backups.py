"""
Backup shipper: watches the backup folder and streams every new .zip to the
receiver (receive_server.py) on another computer in the same Tailscale network.

* A zip is sent as soon as it appears (checked every --interval seconds).
* Transfers resume where they stopped: before each attempt the shipper asks the
  receiver how many bytes it already has and sends only the rest.
* Network errors are retried forever with a growing pause (5 s up to 5 min).
* The receiver checks the SHA-256 of the finished file; a mismatch restarts it.
* Sent files are remembered in <backup folder>/shipped.json. The zips are kept.

Settings come from backup_config.bat (--config) or from the command line:
  SHIP_URL      e.g. http://100.101.102.103:8765  or  http://central-pc:8765
  SHIP_TOKEN    shared secret, same as on the receiver
  FACILITY_ID   folder name used on the receiver (default: computer name)
  BACKUP_DIR    folder to watch
"""
import argparse
import hashlib
import http.client
import json
import logging
import logging.handlers
import os
import re
import socket
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("ship")
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


# ---------------------------------------------------------------- config
def read_bat_config(path):
    """Read 'set NAME=VALUE' lines from a .bat file (expands %~dp0 and %VAR%)."""
    path = Path(path).resolve()
    values = {}
    dp0 = str(path.parent) + os.sep

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


# ---------------------------------------------------------------- helpers
def sha256_of(path, block=1024 * 1024):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(block), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, data):
    tmp = Path(str(path) + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


class TransferError(Exception):
    pass


class Shipper:
    def __init__(self, url, token, facility, watch_dir, timeout=60, chunk=1024 * 1024):
        u = urllib.parse.urlparse(url)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise SystemExit(f"Invalid SHIP_URL: {url!r} (expected http://host:port)")
        self.scheme, self.host = u.scheme, u.hostname
        self.port = u.port or (443 if u.scheme == "https" else 80)
        self.base = u.path.rstrip("/")
        self.token, self.facility = token, facility
        self.watch_dir = Path(watch_dir)
        self.timeout, self.chunk = timeout, chunk
        self.ledger_path = self.watch_dir / "shipped.json"
        self.ledger = load_json(self.ledger_path, {})
        self._sha_cache = {}

    # -- http
    def _conn(self):
        cls = http.client.HTTPSConnection if self.scheme == "https" else http.client.HTTPConnection
        return cls(self.host, self.port, timeout=self.timeout)

    def _path(self, name):
        return (f"{self.base}/api/files/{urllib.parse.quote(self.facility)}/"
                f"{urllib.parse.quote(name)}")

    def _status(self, name):
        conn = self._conn()
        try:
            conn.request("GET", self._path(name), headers={"X-Token": self.token})
            resp = conn.getresponse()
            body = resp.read()
            if resp.status == 401:
                raise SystemExit("Receiver rejected SHIP_TOKEN (401). Check both configs.")
            if resp.status != 200:
                raise TransferError(f"status check HTTP {resp.status}: {body[:200]!r}")
            return json.loads(body)
        finally:
            conn.close()

    def _put(self, path, name, offset, size, sha):
        conn = self._conn()
        try:
            conn.putrequest("PUT", self._path(name))
            conn.putheader("X-Token", self.token)
            conn.putheader("X-Offset", str(offset))
            conn.putheader("X-Total-Size", str(size))
            conn.putheader("X-SHA256", sha)
            conn.putheader("Content-Type", "application/octet-stream")
            conn.putheader("Content-Length", str(size - offset))
            conn.endheaders()
            sent, next_report = offset, offset + max(size // 10, 1)
            with open(path, "rb") as f:
                f.seek(offset)
                while sent < size:
                    buf = f.read(min(self.chunk, size - sent))
                    if not buf:
                        raise TransferError("file shrank while sending")
                    conn.send(buf)
                    sent += len(buf)
                    if sent >= next_report and sent < size:
                        log.info("  %s: %d%% (%s / %s bytes)", name, sent * 100 // size,
                                 f"{sent:,}", f"{size:,}")
                        next_report += max(size // 10, 1)
            resp = conn.getresponse()
            body = resp.read()
            try:
                data = json.loads(body)
            except ValueError:
                data = {"error": body[:200].decode("utf-8", "replace")}
            return resp.status, data
        finally:
            conn.close()

    # -- one file
    def file_sha(self, path):
        st = path.stat()
        key = (path.name, st.st_size, st.st_mtime_ns)
        if key not in self._sha_cache:
            self._sha_cache[key] = sha256_of(path)
        return self._sha_cache[key]

    def ship(self, path):
        name, size = path.name, path.stat().st_size
        sha = self.file_sha(path)
        for _ in range(5):
            st = self._status(name)
            if st.get("complete"):
                if st.get("sha256") == sha:
                    return st
                raise TransferError(f"receiver already has a DIFFERENT {name} "
                                    f"(sha {st.get('sha256')}); rename one of them")
            offset = int(st.get("received", 0))
            if st.get("sha256") not in (None, sha) or offset > size:
                offset = 0     # receiver holds part of another file with this name
            if offset:
                log.info("Resuming %s at %s of %s bytes", name, f"{offset:,}", f"{size:,}")
            else:
                log.info("Sending %s (%s bytes)", name, f"{size:,}")
            code, data = self._put(path, name, offset, size, sha)
            if code == 200 and data.get("complete"):
                return data
            if code in (409, 422):           # offset moved / checksum failed: ask again
                log.warning("  %s: receiver said %s %s; retrying", name, code, data)
                continue
            if code == 423:
                raise TransferError("receiver is still finishing a previous attempt")
            if code == 401:
                raise SystemExit("Receiver rejected SHIP_TOKEN (401). Check both configs.")
            raise TransferError(f"upload HTTP {code}: {data}")
        raise TransferError("gave up after 5 attempts in a row")

    # -- folder scan
    def pending(self, min_age):
        now = time.time()
        files = []
        for p in sorted(self.watch_dir.glob("*.zip"), key=lambda x: x.name):
            if p.name in self.ledger or not SAFE_NAME.match(p.name):
                continue
            try:
                if now - p.stat().st_mtime < min_age:   # just written; let it settle
                    continue
            except FileNotFoundError:
                continue
            files.append(p)
        return files

    def run_once(self, min_age):
        """Ship all pending files. Returns True if everything went through."""
        for path in self.pending(min_age):
            started = time.time()
            self.ship(path)
            secs = max(time.time() - started, 0.001)
            size = path.stat().st_size
            self.ledger[path.name] = {
                "sha256": self.file_sha(path), "size": size,
                "shipped_at": datetime.now(timezone.utc).isoformat(),
            }
            save_json(self.ledger_path, self.ledger)
            log.info("Delivered %s (%s bytes, %.1f s, %.0f KB/s)", path.name, f"{size:,}",
                     secs, size / 1024 / secs)
        return True


def single_instance(port):
    """Hold a localhost port as a lock so only one shipper runs at a time."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        s.listen(1)
    except OSError:
        return None
    return s


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="settings file (default: backup_config.bat next to this script)")
    ap.add_argument("--url"), ap.add_argument("--token"), ap.add_argument("--facility")
    ap.add_argument("--watch", help="folder with backup zips")
    ap.add_argument("--log", help="log file (default: logs\\ship.log next to this script)")
    ap.add_argument("--interval", type=float, default=15, help="seconds between folder checks")
    ap.add_argument("--min-age", type=float, default=5, help="ignore zips younger than this (s)")
    ap.add_argument("--timeout", type=float, default=60, help="network timeout (s)")
    ap.add_argument("--max-backoff", type=float, default=300)
    ap.add_argument("--lock-port", type=int, default=47391)
    ap.add_argument("--once", action="store_true", help="ship what is there now, then exit")
    a = ap.parse_args()

    if not a.config and Path(__file__).with_name("backup_config.bat").exists():
        a.config = str(Path(__file__).with_name("backup_config.bat"))
    cfg = read_bat_config(a.config) if a.config else {}
    url = a.url or cfg.get("SHIP_URL") or os.environ.get("SHIP_URL")
    token = a.token or cfg.get("SHIP_TOKEN") or os.environ.get("SHIP_TOKEN")
    facility = (a.facility or cfg.get("FACILITY_ID") or os.environ.get("FACILITY_ID")
                or socket.gethostname())
    watch = a.watch or cfg.get("BACKUP_DIR") or str(Path(__file__).with_name("backups"))
    log_file = a.log or str(Path(__file__).resolve().with_name("logs") / "ship.log")

    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    handlers = [logging.handlers.RotatingFileHandler(log_file, maxBytes=5_000_000,
                                                     backupCount=5, encoding="utf-8")]
    if sys.stdout is not None:            # None under pythonw.exe
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(message)s")

    if not url or not token:
        log.error("SHIP_URL and SHIP_TOKEN must be set (backup_config.bat).")
        return 2
    facility = re.sub(r"[^A-Za-z0-9_.-]", "_", facility)[:64]
    Path(watch).mkdir(parents=True, exist_ok=True)

    lock = single_instance(a.lock_port)
    if lock is None:
        log.info("Another shipper is already running on this computer; exiting.")
        return 0

    shipper = Shipper(url, token, facility, watch, timeout=a.timeout)
    log.info("Shipper started: watching %s -> %s as facility '%s'", watch, url, facility)
    backoff = 5
    while True:
        try:
            shipper.run_once(a.min_age)
            backoff = 5
            if a.once:
                return 0
            time.sleep(a.interval)
        except SystemExit:
            raise
        except (OSError, http.client.HTTPException, TransferError, ValueError) as e:
            if a.once:
                log.error("Transfer failed: %s", e)
                return 1
            log.warning("Transfer interrupted (%s). Retrying in %d s.", e, backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, a.max_backoff)
        except Exception:
            log.exception("Unexpected error; retrying in %d s.", backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, a.max_backoff)


if __name__ == "__main__":
    sys.exit(main())
