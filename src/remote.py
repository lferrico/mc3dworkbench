"""Connection to an Encore job server on the cluster.

The server (Encore's Python/encore/server.py) binds its ports to localhost on
the head node, so the workbench reaches it through an SSH tunnel it opens
itself (key-based login; there is no password prompt). The wire format is
Encore's Python/encore/protocol.py: ZeroMQ multipart messages, one JSON frame
plus file frames.

    request  DEALER -> server ROUTER   {"id", "cmd", "args"} -> {"id", "ok", "result" | "error"}
    events   SUB    <- server PUB      {"event": "job" | "pool" | "progress", ...}

Both sockets are read on the GUI thread through QSocketNotifier, so replies
and events arrive as ordinary Qt signals and callbacks.
"""

import itertools
import json
import os
import socket
from pathlib import Path

import yaml
import zmq
from PySide6.QtCore import QObject, QProcess, QSocketNotifier, QTimer, Signal

GUI_PORT = 5555
EVENT_PORT = 5557
CONNECT_TIMEOUT_MS = 20000
HEARTBEAT_MS = 5000
POLL_MS = 100
REQUEST_TIMEOUT_MS = 120000  # uploads and fetches of large files take a while

# Job states, as the server names them.
QUEUED, RUNNING, DONE, FAILED, CANCELLED = "queued", "running", "done", "failed", "cancelled"
FINISHED = (DONE, FAILED, CANCELLED)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def prepare_submission(input_path, path_map):
    """Make a written encore.yaml self-contained for the server.

    Returns (document, file names, file contents). Device meshes are uploaded.
    A material is referenced by its cluster path when its local path starts
    with a key of path_map (a local copy of the cluster's material library),
    and otherwise uploaded together with every file in its directory, since a
    material file references sibling decks (Params.yaml, bands, EPM).
    Mirrors prepare_submission in Encore's Python/encore/client.py.
    """
    input_path = Path(input_path).resolve()
    base = input_path.parent
    doc = yaml.safe_load(input_path.read_text()) or {}
    files = {}

    for m in doc.get("materials") or []:
        local = (base / m["file"]).resolve()
        mapped = next((remote + str(local)[len(prefix):] for prefix, remote in path_map.items()
                       if prefix and str(local).startswith(prefix)), None)
        if mapped is not None:
            m["file"] = mapped
            continue
        folder = f"materials/{m['name']}"
        for f in local.parent.rglob("*"):
            if f.is_file():
                files[f"{folder}/{f.relative_to(local.parent).as_posix()}"] = f
        m["file"] = f"{folder}/{local.name}"

    for d in doc.get("devices") or []:
        if "mesh" in d:
            local = (base / d["mesh"]).resolve()
            d["mesh"] = f"meshes/{d['name']}/{local.name}"
            files[d["mesh"]] = local

    names = list(files)
    return yaml.safe_dump(doc, sort_keys=False), names, [files[n].read_bytes() for n in names]


class ServerConnection(QObject):
    """An SSH tunnel plus the request and event sockets to one job server.

    Signals:
        stateChanged(str, str)   "disconnected" | "connecting" | "connected", detail
        jobChanged(dict)         a job record (with "deleted": True when removed)
        poolChanged(dict)        a pool record (with "deleted": True when removed)
        progress(str, dict)      job id, progress snapshot
    """

    stateChanged = Signal(str, str)
    jobChanged = Signal(object)
    poolChanged = Signal(object)
    progress = Signal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ctx = zmq.Context.instance()
        self.state = "disconnected"
        self.host = ""
        self.tunnel = None
        self.req = self.sub = None
        self.notifiers = []
        self.pending = {}  # request id -> (callback, error callback, timer)
        self.ids = itertools.count(1)
        self.heartbeat = QTimer(self)
        self.heartbeat.setInterval(HEARTBEAT_MS)
        self.heartbeat.timeout.connect(self.send_heartbeat)
        self.missed_beats = 0
        # ZeroMQ's FD is edge-triggered and does not always signal a message
        # that arrives on a socket nobody has touched since; a short poll
        # backs the notifiers up so no reply waits for the next heartbeat.
        self.poller = QTimer(self)
        self.poller.setInterval(POLL_MS)
        self.poller.timeout.connect(self.drain_all)

    # --- lifecycle ----------------------------------------------------------

    def connect_to(self, host, gui_port=GUI_PORT, event_port=EVENT_PORT, ssh_args=()):
        """Open the tunnel (unless host is localhost) and the sockets.

        host is an ssh destination (user@head, or an alias from ~/.ssh/config).
        """
        self.disconnect()
        self.host = host
        self.set_state("connecting", f"Connecting to {host}...")
        if host in ("", "localhost", "127.0.0.1"):
            local_gui, local_event = gui_port, event_port
        else:
            local_gui, local_event = free_port(), free_port()
            self.tunnel = QProcess(self)
            self.tunnel.setProcessChannelMode(QProcess.MergedChannels)
            self.tunnel.finished.connect(self.on_tunnel_finished)
            self.tunnel.start("ssh", [
                "-N", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
                "-o", "ServerAliveInterval=30",
                "-L", f"{local_gui}:localhost:{gui_port}",
                "-L", f"{local_event}:localhost:{event_port}",
                *ssh_args, host])

        self.req = self.ctx.socket(zmq.DEALER)
        self.req.setsockopt(zmq.LINGER, 0)
        self.req.connect(f"tcp://127.0.0.1:{local_gui}")
        self.sub = self.ctx.socket(zmq.SUB)
        self.sub.setsockopt(zmq.LINGER, 0)
        self.sub.setsockopt(zmq.SUBSCRIBE, b"")
        self.sub.connect(f"tcp://127.0.0.1:{local_event}")
        for sock, handler in ((self.req, self.on_reply), (self.sub, self.on_event)):
            notifier = QSocketNotifier(sock.getsockopt(zmq.FD), QSocketNotifier.Read, self)
            notifier.activated.connect(lambda *_, s=sock, h=handler: self.drain(s, h))
            self.notifiers.append(notifier)
        self.poller.start()

        # ZeroMQ queues the ping until the tunnel is up; it answers once the
        # server is reachable.
        self.request("ping", callback=self.on_connected, error=self.on_connect_failed,
                     timeout=CONNECT_TIMEOUT_MS)

    def disconnect(self):
        self.heartbeat.stop()
        self.poller.stop()
        for notifier in self.notifiers:
            notifier.setEnabled(False)
            notifier.deleteLater()
        self.notifiers = []
        for _, _, timer in self.pending.values():
            timer.stop()
        self.pending = {}
        for sock in (self.req, self.sub):
            if sock is not None:
                sock.close(0)
        self.req = self.sub = None
        if self.tunnel is not None:
            self.tunnel.finished.disconnect(self.on_tunnel_finished)
            self.tunnel.kill()
            self.tunnel.waitForFinished(2000)
            self.tunnel.deleteLater()
            self.tunnel = None
        if self.state != "disconnected":
            self.set_state("disconnected", "Not connected")

    def set_state(self, state, detail):
        self.state = state
        self.stateChanged.emit(state, detail)

    def on_connected(self, info):
        self.missed_beats = 0
        self.heartbeat.start()
        self.set_state("connected", f"{info.get('host', self.host)} ({info.get('jobs_dir', '')})")

    def on_connect_failed(self, message):
        detail = message
        if self.tunnel is not None:
            output = bytes(self.tunnel.readAll()).decode(errors="replace").strip()
            if output:
                detail = f"{message}\n\nssh: {output}"
        self.disconnect()
        self.set_state("disconnected", detail)

    def on_tunnel_finished(self, *_):
        output = bytes(self.tunnel.readAll()).decode(errors="replace").strip()
        self.disconnect()
        self.set_state("disconnected", f"SSH tunnel closed{': ' + output if output else ''}")

    def send_heartbeat(self):
        self.missed_beats += 1
        if self.missed_beats > 3:
            self.disconnect()
            self.set_state("disconnected", "The server stopped answering")
            return
        self.request("ping", callback=lambda _: setattr(self, "missed_beats", 0),
                     error=lambda _: None, timeout=HEARTBEAT_MS * 3)

    # --- messaging ----------------------------------------------------------

    def request(self, cmd, args=None, blobs=(), callback=None, error=None, timeout=REQUEST_TIMEOUT_MS):
        """Send a command; callback(result) or callback(result, blobs) on success,
        error(message) on failure or timeout."""
        if self.req is None:
            if error:
                error("Not connected to a job server")
            return
        rid = next(self.ids)
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: self.on_timeout(rid, cmd))
        timer.start(timeout)
        self.pending[rid] = (callback, error, timer)
        frames = [json.dumps({"id": rid, "cmd": cmd, "args": args or {}}).encode(), *blobs]
        self.req.send_multipart(frames)
        # The notifier is edge-triggered on ZeroMQ's FD: a send can consume the
        # edge for a reply that is already waiting.
        QTimer.singleShot(0, lambda: self.drain(self.req, self.on_reply))

    def on_timeout(self, rid, cmd):
        entry = self.pending.pop(rid, None)
        if entry and entry[1]:
            entry[1](f"No reply to {cmd} from the job server")

    def drain_all(self):
        self.drain(self.req, self.on_reply)
        self.drain(self.sub, self.on_event)

    def drain(self, sock, handler):
        while sock is not None and not sock.closed and sock.getsockopt(zmq.EVENTS) & zmq.POLLIN:
            frames = sock.recv_multipart(zmq.NOBLOCK)
            handler(json.loads(frames[0]), frames[1:])

    def on_reply(self, reply, blobs):
        entry = self.pending.pop(reply.get("id"), None)
        if entry is None:
            return
        callback, error, timer = entry
        timer.stop()
        if not reply.get("ok"):
            if error:
                error(reply.get("error") or "request failed")
            return
        if callback is None:
            return
        if blobs:
            callback(reply.get("result"), blobs)
        else:
            callback(reply.get("result"))

    def on_event(self, event, _):
        kind = event.get("event")
        if kind == "job":
            self.jobChanged.emit(event["job"])
        elif kind == "pool":
            self.poolChanged.emit(event["pool"])
        elif kind == "progress":
            self.progress.emit(event["job"], event["progress"])

    # --- commands -----------------------------------------------------------

    def submit(self, input_path, name, pool, path_map, callback=None, error=None, meta=None):
        """Upload a written encore.yaml (and its meshes/materials) as a job.

        meta comes back with the job record, e.g. to tie it to a sweep row."""
        try:
            document, names, blobs = prepare_submission(input_path, path_map)
        except (OSError, KeyError, yaml.YAMLError) as exc:
            if error:
                error(f"Could not prepare the job: {exc}")
            return
        self.request("job.submit", {"document": document, "files": names, "name": name,
                                    "pool": pool or None, "meta": meta or {}}, blobs, callback, error)

    def fetch_all(self, job_id, directory, done=None, error=None, skip=("job.json",)):
        """Download every file of a job into directory, keeping its layout."""
        def got_list(files):
            queue = [f["path"] for f in files if f["path"] not in skip]
            fetch_next(queue, 0)

        def fetch_next(queue, count):
            if not queue:
                if done:
                    done(count)
                return
            path = queue.pop(0)

            def got_file(result, blobs=()):
                target = os.path.join(directory, *path.split("/"))
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with open(target, "wb") as f:
                    f.write(blobs[0] if blobs else b"")
                fetch_next(queue, count + 1)

            self.request("job.fetch", {"id": job_id, "path": path}, callback=got_file, error=error)

        self.request("job.files", {"id": job_id}, callback=got_list, error=error)
