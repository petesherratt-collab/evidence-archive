#!/usr/bin/env python3
"""Minimal off-host receiver for collector liveness and alert-delivery faults."""
import argparse
import hmac
import json
import os
import pathlib
import tempfile
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _required(name):
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"{name} is required")
    return value


def _read_token():
    path = pathlib.Path(_required("DEADMAN_RECEIVER_TOKEN_FILE"))
    if path.stat().st_mode & 0o077:
        raise SystemExit("DEADMAN_RECEIVER_TOKEN_FILE must not be accessible by group or other")
    return path.read_text(encoding="utf-8").strip()


def _write_state(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".deadman-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _alarm(reason, event):
    payload = json.dumps({"text": f"Evidence dead-man alarm: {reason}", "event": event}).encode()
    request = urllib.request.Request(
        _required("DEADMAN_ALARM_URL"), data=payload,
        headers={"content-type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
        if not 200 <= response.status < 300:
            raise RuntimeError(f"alarm destination returned HTTP {response.status}")


def receive(state_path, event):
    now = int(time.time())
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}
    if event.get("event") == "collector-heartbeat":
        state.update({"last_seen": now, "event": event})
        _write_state(state_path, state)
    elif event.get("event") == "alert-delivery-failed":
        state["pending_alert_failure"] = event
        _write_state(state_path, state)
        _alarm("collector could not deliver its health alert", event)
        state.pop("pending_alert_failure")
        _write_state(state_path, state)
    else:
        raise ValueError("unsupported event")


def check(state_path, max_age, repeat):
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        pending = state.get("pending_alert_failure")
        if pending:
            _alarm("collector could not deliver its health alert", pending)
            state.pop("pending_alert_failure")
            _write_state(state_path, state)
        age = int(time.time()) - int(state["last_seen"])
    except (FileNotFoundError, KeyError, ValueError, json.JSONDecodeError):
        age = max_age + 1
        state = {"event": {"instance_id": "unknown"}}
    alarm_stamp = state_path.with_suffix(".alarm")
    if age <= max_age:
        if alarm_stamp.exists():
            alarm_stamp.unlink()
        return False
    if alarm_stamp.exists() and int(time.time()) - int(alarm_stamp.stat().st_mtime) < repeat:
        return False
    _alarm(f"collector heartbeat absent for {age} seconds", state.get("event", {}))
    alarm_stamp.touch()
    return True


def serve(host, port, state_path, token):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            supplied = self.headers.get("authorization", "").removeprefix("Bearer ")
            if self.path != "/events" or not hmac.compare_digest(supplied, token):
                self.send_error(404 if self.path != "/events" else 401)
                return
            try:
                length = int(self.headers.get("content-length", "0"))
                if length <= 0 or length > 16384:
                    raise ValueError("invalid body length")
                event = json.loads(self.rfile.read(length))
                receive(state_path, event)
            except (ValueError, json.JSONDecodeError) as exc:
                self.send_error(400, str(exc))
                return
            self.send_response(204)
            self.end_headers()

        def log_message(self, fmt, *args):
            print(f"deadman receiver: {fmt % args}")

    ThreadingHTTPServer((host, port), Handler).serve_forever()


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    receiver = sub.add_parser("serve")
    receiver.add_argument("--host", default="127.0.0.1")
    receiver.add_argument("--port", default=8080, type=int)
    checker = sub.add_parser("check")
    checker.add_argument("--max-age", default=2400, type=int)
    checker.add_argument("--repeat", default=21600, type=int)
    args = parser.parse_args()
    state_path = pathlib.Path(_required("DEADMAN_STATE_FILE"))
    if args.command == "serve":
        serve(args.host, args.port, state_path, _read_token())
    else:
        check(state_path, args.max_age, args.repeat)


if __name__ == "__main__":
    main()
