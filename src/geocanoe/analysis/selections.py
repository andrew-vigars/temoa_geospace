"""Persist run-specific map selections and expose them as SQLite query filters.

Run ``python -m geocanoe.analysis.selections --db RUN/model.sqlite`` to open
the run's generated map with direct saving to RUN/region_selections/.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import tempfile
from threading import Event, Lock, Thread
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import webbrowser


def validate_selection(value: dict, database: Path) -> dict:
    """Reject malformed selections and selections belonging to another run."""
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError("Unsupported region selection format.")
    if value.get("database") != str(database.resolve()):
        raise ValueError("Selection belongs to a different run/database.")
    regions = value.get("regions")
    if not isinstance(regions, list) or not regions or any(
        not isinstance(region, str) or not region.strip() for region in regions
    ):
        raise ValueError("Select at least one valid region.")
    name = value.get("name", "")
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", name):
        raise ValueError("Name must be 1–80 letters, numbers, underscores or hyphens.")
    return {**value, "regions": sorted(set(regions))}


def load_selection(path: Path, database: Path) -> dict:
    return validate_selection(json.loads(path.read_text(encoding="utf-8")), database)


def save_selection(value: dict, database: Path) -> Path:
    """Create a named selection; never silently overwrite an existing selection."""
    value = validate_selection(value, database)
    folder = database.resolve().parent / "region_selections"
    folder.mkdir(exist_ok=True)
    target = folder / f"{value['name']}.json"
    with target.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
    return target


def install_selection_table(connection: sqlite3.Connection, selection: dict) -> None:
    """Make exact region IDs available to diagnostic SQL in this connection.

    Example: SELECT o.* FROM OutputFlowOut o JOIN selected_regions s
    ON CAST(o.region AS TEXT) = s.region
    """
    connection.execute("CREATE TEMP TABLE IF NOT EXISTS selected_regions (region TEXT PRIMARY KEY)")
    connection.execute("DELETE FROM selected_regions")
    connection.executemany(
        "INSERT INTO selected_regions VALUES (?)", [(r,) for r in selection["regions"]]
    )


def region_matches(region: object, selected: set[str], transport: str = "touching") -> bool:
    """Match exact nodes and CANOE's two-endpoint corridor region encoding."""
    if transport not in {"touching", "within", "boundary"}:
        raise ValueError("Unknown transport selection mode.")
    region = str(region)
    if region in selected:
        return True
    endpoints = region.split("-")
    if len(endpoints) != 2 or not all(endpoints):
        return False
    count = sum(endpoint in selected for endpoint in endpoints)
    return {"touching": count > 0, "within": count == 2, "boundary": count == 1}[transport]


def query_selection(database: Path, selection_path: Path, sql_path: Path) -> Path:
    """Run diagnostic SQL against a read-only database, with a TEMP ID table."""
    selection = load_selection(selection_path, database)
    folder = database.resolve().parent / "region_selections" / selection["name"] / "sql_diagnostics"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{sql_path.stem}.csv"
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        install_selection_table(connection, selection)
        cursor = connection.execute(sql_path.read_text(encoding="utf-8"))
        if cursor.description is None:
            raise ValueError("Diagnostic SQL must return rows.")
        with target.open("x", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow([column[0] for column in cursor.description])
            writer.writerows(cursor)
    return target


def export_selection(database: Path, request: dict) -> Path:
    """Export a UI selection without trusting client-supplied paths or table SQL."""
    from geocanoe.analysis.exports import export_output_tables, list_existing_output_tables
    from geocanoe.analysis.subset_database import export_subset_database

    if not isinstance(request, dict):
        raise ValueError("Invalid export request.")
    selection = validate_selection(request.get("selection"), database)
    name = request.get("filename", "")
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}\.xlsx", name):
        raise ValueError("Workbook name must use letters, numbers, underscores or hyphens and end in .xlsx.")
    tables = request.get("tables")
    available = list_existing_output_tables(database)
    if not isinstance(tables, list) or not tables or any(not isinstance(t, str) or t not in available for t in tables):
        raise ValueError("Choose at least one available output table.")
    transport = request.get("transport", "touching")
    if transport not in {"touching", "within", "boundary"}:
        raise ValueError("Invalid transport scope.")
    folder = database.resolve().parent / "region_selections" / selection["name"] / "excel_outputs"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    targets = [target, target.with_suffix(".sqlite"), target.with_suffix(".sql")]
    if any(path.exists() for path in targets):
        raise FileExistsError("An export with this name already exists. Choose a new workbook name.")
    with tempfile.TemporaryDirectory(dir=folder) as temporary:
        temporary = Path(temporary)
        snapshot = temporary / "selection.json"
        snapshot.write_text(json.dumps(selection), encoding="utf-8")
        workbook, = export_output_tables(database, temporary, tables=list(dict.fromkeys(tables)),
                                         selection_path=snapshot, transport=transport)
        subset = export_subset_database(database, selection, temporary / target.with_suffix(".sqlite").name, transport)
        created = []
        try:
            for source_path, destination_path in zip([workbook, subset, subset.with_suffix(".sql")], targets, strict=True):
                with source_path.open("rb") as source, destination_path.open("xb") as destination:
                    created.append(destination_path)
                    shutil.copyfileobj(source, destination)
        except OSError:
            for path in created:
                path.unlink()
            raise
    return target


class MapClients:
    """Track browser tabs with a refresh grace period and crash expiry."""

    def __init__(self, grace: float = 15, expiry: float = 180):
        self.grace, self.expiry = grace, expiry
        self.clients: dict[str, float] = {}
        self.empty_since: float | None = None
        self.seen = False
        self.lock = Lock()

    def update(self, client: str, action: str, now: float) -> None:
        with self.lock:
            self.seen = True
            if action == "close":
                self.clients.pop(client, None)
                if not self.clients and self.empty_since is None:
                    self.empty_since = now
            else:
                self.clients[client] = now
                self.empty_since = None

    def should_stop(self, now: float) -> bool:
        with self.lock:
            self.clients = {key: seen for key, seen in self.clients.items() if now-seen < self.expiry}
            if self.clients or not self.seen:
                return False
            if self.empty_since is None:
                self.empty_since = now
            return now-self.empty_since >= self.grace


_CLIENT_SCRIPT = """<script>
(function(){
const client=crypto.randomUUID(), token='__TOKEN__';
function notify(event){
 const body=JSON.stringify({client,event,token});
 if(event==='close' && navigator.sendBeacon){
   navigator.sendBeacon('/clients',new Blob([body],{type:'application/json'}));
 }else{
   fetch('/clients',{method:'POST',headers:{'Content-Type':'application/json'},body,keepalive:true}).catch(()=>{});
 }
}
notify('heartbeat');
setInterval(()=>notify('heartbeat'),15000);
window.addEventListener('pagehide',()=>notify('close'));
window.addEventListener('pageshow',()=>notify('heartbeat'));
})();
</script>"""


def serve_map(database: Path, map_path: Path, *, open_browser: bool = True, keep_alive: bool = False) -> None:
    """Serve one map and a token-protected selection API on loopback only."""
    database = database.resolve(strict=True)
    html = map_path.read_text(encoding="utf-8")
    token = secrets.token_urlsafe(32)
    folder = database.parent / "region_selections"
    folder.mkdir(exist_ok=True)
    export_lock = Lock()
    clients = MapClients()

    class Handler(BaseHTTPRequestHandler):
        def reply(self, status: int, data: bytes, content_type: str = "application/json") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def authorized(self) -> bool:
            return self.headers.get("X-Selection-Token") == token

        def do_GET(self) -> None:
            if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
                self.reply(403, b'{}')
                return
            if self.path == "/":
                injected = html.replace("__REGION_SELECTION_TOKEN__", token)
                if not keep_alive:
                    injected = injected.replace("</body>", _CLIENT_SCRIPT.replace("__TOKEN__", token) + "</body>")
                self.reply(200, injected.encode(), "text/html; charset=utf-8")
            elif self.path == "/selections" and self.authorized():
                values = []
                for path in sorted(folder.glob("*.json")):
                    try:
                        values.append(load_selection(path, database))
                    except (ValueError, OSError):
                        continue
                self.reply(200, json.dumps(values).encode())
            elif self.path == "/export-options" and self.authorized():
                from geocanoe.analysis.exports import list_existing_output_tables
                self.reply(200, json.dumps({"database": str(database),
                                            "tables": list_existing_output_tables(database)}).encode())
            else:
                self.reply(404, b'{}')

        def do_POST(self) -> None:
            if (self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}"
                    or self.path not in {"/selections", "/exports", "/clients"}
                    or (self.path != "/clients" and not self.authorized())):
                self.reply(403, b'{}')
                return
            origin = self.headers.get("Origin")
            if origin and origin != f"http://127.0.0.1:{self.server.server_port}":
                self.reply(403, b'{}')
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 5_000_000:
                    raise ValueError("Invalid selection size.")
                value = json.loads(self.rfile.read(length))
                if self.path == "/clients":
                    if (not isinstance(value, dict) or value.get("token") != token
                            or not isinstance(value.get("client"), str) or not 1 <= len(value["client"]) <= 100
                            or value.get("event") not in {"heartbeat", "close"}):
                        self.reply(403, b'{}')
                        return
                    clients.update(value["client"], value["event"], time.monotonic())
                    self.reply(200, b'{}')
                    return
                if self.path == "/exports":
                    with export_lock:
                        path = export_selection(database, value)
                else:
                    path = save_selection(value, database)
                result = {"path": str(path)}
                if self.path == "/exports":
                    result.update(sqlite_path=str(path.with_suffix(".sqlite")), sql_path=str(path.with_suffix(".sql")))
                self.reply(201, json.dumps(result).encode())
            except (ValueError, OSError, sqlite3.Error) as error:
                self.reply(400, json.dumps({"error": str(error)}).encode())

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        url = f"http://127.0.0.1:{server.server_port}/"
        print(f"Region selection map: {url}\nSelections: {folder}\nPress Ctrl+C to stop.", flush=True)
        if open_browser:
            webbrowser.open(url)
        stopped = Event()

        def monitor() -> None:
            while not stopped.wait(1):
                if not keep_alive and clients.should_stop(time.monotonic()) and not export_lock.locked():
                    print("All map tabs closed; stopping local server.", flush=True)
                    server.shutdown()
                    return

        watcher = Thread(target=monitor, daemon=True)
        watcher.start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            stopped.set()
            watcher.join(timeout=2)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--map", type=Path)
    parser.add_argument("--import-selection", type=Path)
    parser.add_argument("--selection", type=Path, help="Saved selection for diagnostic SQL.")
    parser.add_argument("--sql-file", type=Path, help="SQL using the TEMP selected_regions table; writes CSV.")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--keep-alive", action="store_true", help="Keep serving after map tabs close.")
    args = parser.parse_args()
    if args.sql_file or args.selection:
        if not (args.sql_file and args.selection):
            parser.error("--selection and --sql-file must be used together.")
        print(query_selection(args.db, args.selection, args.sql_file))
        return
    if args.import_selection:
        print(save_selection(load_selection(args.import_selection, args.db), args.db))
        return
    candidates = sorted(args.db.parent.glob("*_interactive_folium.html"))
    if args.map is None and len(candidates) != 1:
        parser.error("Specify --map when the run does not contain exactly one interactive map.")
    serve_map(args.db, args.map or candidates[0], open_browser=not args.no_browser, keep_alive=args.keep_alive)


if __name__ == "__main__":
    main()
