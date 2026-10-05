import os
import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFontDatabase, QTextCursor
from PySide6.QtWidgets import *

import remote
import theme
from widgets.enter_key_button import EnterKeyButton

ANY_POOL = "Any idle pool"


def progress_text(p):
    """One line for a job's progress snapshot."""
    if not p:
        return ""
    text = p.get("sim", "")
    if p.get("entries", 1) > 1:
        text = f"{p['entry'] + 1}/{p['entries']}  " + text
    if p.get("points", 1) > 1:
        text += f"  point {p['point'] + 1}/{p['points']}"
    if p.get("steps"):
        done = p["step"] / p["steps"]
        text += f"  {done * 100:.0f}%"
        if 0 < done < 1 and p.get("elapsed"):
            eta = p["elapsed"] * (1 - done) / done
            text += f"  ETA {time.strftime('%H:%M:%S', time.gmtime(eta))}"
    return text


def progress_fraction(p):
    """Fraction of a job done: its `sim:` entries, their sweep points, and the
    solver loop of the current point."""
    if not p:
        return None
    points = max(1, p.get("points", 1))
    inner = p["step"] / p["steps"] if p.get("steps") else 0.0
    entry = (p.get("point", 0) + inner) / points
    return (p.get("entry", 0) + entry) / max(1, p.get("entries", 1))


class ConnectDialog(QDialog):
    """Where the job server runs, and which local material paths it can see."""

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Connect to Cluster")
        self.settings = settings

        self.host = QLineEdit(settings.value("cluster/host", ""))
        self.host.setPlaceholderText("user@headnode, an ~/.ssh/config alias, or localhost")
        self.gui_port = QSpinBox(maximum=65535, value=int(settings.value("cluster/gui_port", remote.GUI_PORT)))
        self.event_port = QSpinBox(maximum=65535, value=int(settings.value("cluster/event_port", remote.EVENT_PORT)))
        self.path_map = QPlainTextEdit(settings.value("cluster/path_map", ""))
        self.path_map.setPlaceholderText(
            "/home/me/Encore/Data/Materials = /mnt/csmhome2/common/Encore/material_lib")
        self.path_map.setFixedHeight(80)

        form = QFormLayout()
        form.addRow("SSH host", self.host)
        form.addRow("Server port", self.gui_port)
        form.addRow("Event port", self.event_port)
        form.addRow("Material paths", self.path_map)
        hint = QLabel("One `local = cluster` prefix per line, for folders both machines see "
                      "(e.g. the NAS). A project under one runs in place: each sweep row writes "
                      "to <project>/results/<row>. Paths that are the same on both machines "
                      "need no line. Anything the cluster cannot see is uploaded.")
        hint.setWordWrap(True)
        form.addRow("", hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Connect")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.resize(520, 0)

    def accept(self):
        self.settings.setValue("cluster/host", self.host.text().strip())
        self.settings.setValue("cluster/gui_port", self.gui_port.value())
        self.settings.setValue("cluster/event_port", self.event_port.value())
        self.settings.setValue("cluster/path_map", self.path_map.toPlainText())
        super().accept()


def path_map_from_settings(settings):
    mapping = {}
    for line in str(settings.value("cluster/path_map", "")).splitlines():
        if "=" in line:
            local, cluster = (part.strip() for part in line.split("=", 1))
            if local and cluster:
                mapping[local] = cluster
    return mapping


class PoolDialog(QDialog):
    """A new pool: a name, some free nodes, and its ranks and threads."""

    def __init__(self, nodes, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New Pool")

        self.name = QLineEdit()
        self.nodes = QListWidget()
        for node in nodes:
            item = QListWidgetItem(f"{node['name']}" + (f"  ({node['cores']} cores)" if node.get("cores") else "")
                                   + (f"  in pool {node['pool']}" if node.get("pool") else ""))
            item.setData(Qt.UserRole, node["name"])
            item.setFlags(Qt.ItemIsUserCheckable | (Qt.NoItemFlags if node.get("pool") else Qt.ItemIsEnabled))
            item.setCheckState(Qt.Unchecked)
            self.nodes.addItem(item)
        self.ppn = QSpinBox(minimum=1, maximum=1024, value=8)
        self.threads = QSpinBox(minimum=1, maximum=1024, value=8)

        form = QFormLayout()
        form.addRow("Name", self.name)
        form.addRow("Nodes", self.nodes)
        form.addRow("Ranks per node", self.ppn)
        form.addRow("Threads per rank", self.threads)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Create")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def spec(self):
        nodes = [self.nodes.item(i).data(Qt.UserRole) for i in range(self.nodes.count())
                 if self.nodes.item(i).checkState() == Qt.Checked]
        return {"name": self.name.text().strip(), "nodes": nodes,
                "ppn": self.ppn.value(), "threads": self.threads.value()}


class LogDialog(QDialog):
    """A job's log, followed live while the job runs."""

    def __init__(self, connection, job, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Log - {job['name']}")
        self.connection, self.job_id, self.offset = connection, job["id"], 0
        self.text = QPlainTextEdit(readOnly=True)
        self.text.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.text.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        layout = QVBoxLayout(self)
        layout.addWidget(self.text)
        self.resize(900, 500)
        self.timer = QTimer(self, interval=1000)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        self.busy = False
        self.poll()

    def poll(self):
        if self.busy:
            return
        self.busy = True
        self.connection.request("job.log", {"id": self.job_id, "offset": self.offset},
                                callback=self.got, error=self.failed)

    def got(self, result):
        self.busy = False
        if result["text"]:
            # Progress bars redraw with carriage returns; keep only the last frame of a line.
            text = "\n".join(line.rsplit("\r", 1)[-1] for line in result["text"].split("\n"))
            self.text.moveCursor(QTextCursor.End)
            self.text.insertPlainText(text)
            self.text.ensureCursorVisible()
        self.offset = result["offset"]

    def failed(self, message):
        self.busy = False
        self.timer.stop()
        self.text.appendPlainText(f"\n[{message}]")

    def done(self, code):
        self.timer.stop()
        super().done(code)


class JobsPanel(QWidget):
    """Pools and jobs of the connected Encore job server.

    Signals:
        connectedChanged(bool)   the panel connected to or lost the server
        jobsReset(dict)          the full job list arrived (id -> job), after connecting
    """

    connectedChanged = Signal(bool)
    jobsReset = Signal(object)

    JOB_COLUMNS = ["Job", "Pool", "State", "Progress", "Detail", "Submitted"]
    POOL_COLUMNS = ["Pool", "Nodes", "Ranks", "State", "Job"]

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.connection = remote.ServerConnection(self)
        self.connection.stateChanged.connect(self.on_state)
        self.connection.jobChanged.connect(self.on_job)
        self.connection.poolChanged.connect(self.on_pool)
        self.connection.progress.connect(self.on_progress)
        self.jobs = {}   # id -> job record
        self.pools = {}  # name -> pool record

        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme.SPACING_SM, theme.SPACING_SM, theme.SPACING_SM, theme.SPACING_SM)
        layout.setSpacing(theme.SPACING_SM)

        # Connection row
        top = QHBoxLayout()
        self.status = QLabel("Not connected")
        self.status.setWordWrap(True)
        self.connect_btn = EnterKeyButton("Connect...")
        self.connect_btn.clicked.connect(self.toggle_connection)
        top.addWidget(self.status, 1)
        top.addWidget(self.connect_btn)
        layout.addLayout(top)

        # Pools
        self.pool_table = self.make_table(self.POOL_COLUMNS)
        pool_buttons = QHBoxLayout()
        self.new_pool_btn = EnterKeyButton("New pool...")
        self.new_pool_btn.clicked.connect(self.new_pool)
        self.delete_pool_btn = EnterKeyButton("Delete")
        self.delete_pool_btn.clicked.connect(self.delete_pool)
        self.restart_pool_btn = EnterKeyButton("Restart")
        self.restart_pool_btn.clicked.connect(self.restart_pool)
        for b in (self.new_pool_btn, self.delete_pool_btn, self.restart_pool_btn):
            pool_buttons.addWidget(b)
        pool_buttons.addStretch(1)

        # Jobs
        self.job_table = self.make_table(self.JOB_COLUMNS)
        self.job_table.doubleClicked.connect(lambda *_: self.show_log())
        job_buttons = QHBoxLayout()
        self.cancel_btn = EnterKeyButton("Cancel")
        self.cancel_btn.clicked.connect(self.cancel_job)
        self.log_btn = EnterKeyButton("Log")
        self.log_btn.clicked.connect(self.show_log)
        self.fetch_btn = EnterKeyButton("Download...")
        self.fetch_btn.clicked.connect(self.fetch_job)
        self.delete_job_btn = EnterKeyButton("Delete")
        self.delete_job_btn.clicked.connect(self.delete_job)
        for b in (self.cancel_btn, self.log_btn, self.fetch_btn, self.delete_job_btn):
            job_buttons.addWidget(b)
        job_buttons.addStretch(1)

        layout.addWidget(QLabel("Pools"))
        layout.addWidget(self.pool_table, 1)
        layout.addLayout(pool_buttons)
        layout.addWidget(QLabel("Jobs"))
        layout.addWidget(self.job_table, 3)
        layout.addLayout(job_buttons)

        self.pool_table.itemSelectionChanged.connect(self.update_buttons)
        self.job_table.itemSelectionChanged.connect(self.update_buttons)
        self.update_buttons()

    def make_table(self, columns):
        table = QTableWidget(0, len(columns))
        table.setHorizontalHeaderLabels(columns)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(theme.ROW_HEIGHT)
        table.horizontalHeader().setStretchLastSection(True)
        table.setProperty("cornerless", True)
        return table

    # --- connection ---------------------------------------------------------

    @property
    def connected(self):
        return self.connection.state == "connected"

    def toggle_connection(self):
        if self.connection.state != "disconnected":
            # Disconnecting on purpose: do not reconnect on the next start.
            self.settings.setValue("cluster/reconnect", False)
            self.connection.disconnect()
            return
        dialog = ConnectDialog(self.settings, self)
        if dialog.exec() != QDialog.Accepted:
            return
        self.connect_saved()

    def connect_saved(self, quiet=False):
        """Connect to the server in the settings; quiet keeps failures to the status line."""
        self.quiet_failure = quiet
        self.connection.connect_to(self.settings.value("cluster/host", ""),
                                   int(self.settings.value("cluster/gui_port", remote.GUI_PORT)),
                                   int(self.settings.value("cluster/event_port", remote.EVENT_PORT)))

    def reconnect_on_start(self):
        """Reconnect if the workbench was connected when it last closed."""
        if str(self.settings.value("cluster/reconnect", "false")).lower() == "true":
            self.connect_saved(quiet=True)

    def on_state(self, state, detail):
        self.status.setText(detail if state != "connected" else f"Connected: {detail}")
        self.connect_btn.setText("Disconnect" if state != "disconnected" else "Connect...")
        if state == "connected":
            self.settings.setValue("cluster/reconnect", True)
            self.quiet_failure = False
            self.refresh()
        elif state == "disconnected" and detail not in ("Not connected", ""):
            if self.isVisible() and not getattr(self, "quiet_failure", False):
                QMessageBox.warning(self, "Cluster", detail)
        self.update_buttons()
        self.connectedChanged.emit(state == "connected")

    def refresh(self):
        def got_pools(pools):
            self.pools = {p["name"]: p for p in pools}
            self.fill_pools()

        def got_jobs(jobs):
            self.jobs = {j["id"]: j for j in jobs}
            self.fill_jobs()
            self.jobsReset.emit(self.jobs)

        self.connection.request("pool.list", callback=got_pools, error=self.warn)
        self.connection.request("job.list", callback=got_jobs, error=self.warn)

    def warn(self, message):
        QMessageBox.warning(self, "Cluster", message)

    # --- events -------------------------------------------------------------

    def on_job(self, job):
        if job.get("deleted"):
            self.jobs.pop(job["id"], None)
        else:
            self.jobs[job["id"]] = job
        self.fill_jobs()

    def on_pool(self, pool):
        if pool.get("deleted"):
            self.pools.pop(pool["name"], None)
        else:
            self.pools[pool["name"]] = pool
        self.fill_pools()

    def on_progress(self, job_id, progress):
        job = self.jobs.get(job_id)
        if job is None:
            return
        job["progress"] = progress
        row = self.job_row(job_id)
        if row is not None:
            self.set_progress_cells(row, job)

    # --- tables -------------------------------------------------------------

    def fill_pools(self):
        selected = self.selected_pool()
        self.pool_table.setRowCount(0)
        for name in sorted(self.pools):
            p = self.pools[name]
            row = self.pool_table.rowCount()
            self.pool_table.insertRow(row)
            values = [name, ", ".join(p["nodes"]), f"{p['ranks']} x {p['threads']} thr",
                      p["state"] + (f": {p['error']}" if p.get("error") else ""),
                      self.jobs.get(p.get("job"), {}).get("name", p.get("job") or "")]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.UserRole, name)
                self.pool_table.setItem(row, col, item)
            if name == selected:
                self.pool_table.selectRow(row)
        self.pool_table.resizeColumnsToContents()
        self.update_buttons()

    def fill_jobs(self):
        selected = self.selected_job()
        self.job_table.setRowCount(0)
        # Newest first.
        for job in sorted(self.jobs.values(), key=lambda j: j.get("submitted") or 0, reverse=True):
            row = self.job_table.rowCount()
            self.job_table.insertRow(row)
            submitted = time.strftime("%m-%d %H:%M", time.localtime(job["submitted"])) if job.get("submitted") else ""
            values = [job["name"], job.get("assigned") or job.get("pool") or "", job["state"], "", "", submitted]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.UserRole, job["id"])
                item.setToolTip(job["id"])
                self.job_table.setItem(row, col, item)
            self.set_progress_cells(row, job)
            if job["id"] == selected:
                self.job_table.selectRow(row)
        self.job_table.resizeColumnsToContents()
        self.update_buttons()

    def set_progress_cells(self, row, job):
        progress_col, detail_col = self.JOB_COLUMNS.index("Progress"), self.JOB_COLUMNS.index("Detail")
        if job["state"] == remote.RUNNING:
            bar = self.job_table.cellWidget(row, progress_col)
            if not isinstance(bar, QProgressBar):
                bar = QProgressBar()
                bar.setRange(0, 1000)
                bar.setTextVisible(True)
                bar.setMinimumWidth(120)
                self.job_table.setCellWidget(row, progress_col, bar)
            fraction = progress_fraction(job.get("progress"))
            if fraction is None:
                bar.setRange(0, 0)  # busy indicator until the first report
            else:
                bar.setRange(0, 1000)
                bar.setValue(int(fraction * 1000))
            self.job_table.item(row, detail_col).setText(progress_text(job.get("progress")))
        else:
            self.job_table.removeCellWidget(row, progress_col)
            self.job_table.item(row, progress_col).setText("100%" if job["state"] == remote.DONE else "")
            self.job_table.item(row, detail_col).setText(job.get("error") or "")

    def job_row(self, job_id):
        for row in range(self.job_table.rowCount()):
            if self.job_table.item(row, 0).data(Qt.UserRole) == job_id:
                return row
        return None

    def selected_job(self):
        rows = self.job_table.selectionModel().selectedRows()
        return self.job_table.item(rows[0].row(), 0).data(Qt.UserRole) if rows else None

    def selected_pool(self):
        rows = self.pool_table.selectionModel().selectedRows()
        return self.pool_table.item(rows[0].row(), 0).data(Qt.UserRole) if rows else None

    def update_buttons(self):
        job = self.jobs.get(self.selected_job())
        pool = self.pools.get(self.selected_pool())
        on = self.connected
        self.new_pool_btn.setEnabled(on)
        self.delete_pool_btn.setEnabled(on and pool is not None)
        self.restart_pool_btn.setEnabled(on and pool is not None)
        self.cancel_btn.setEnabled(on and job is not None and job["state"] not in remote.FINISHED)
        self.log_btn.setEnabled(on and job is not None)
        self.fetch_btn.setEnabled(on and job is not None and job["state"] in remote.FINISHED)
        self.delete_job_btn.setEnabled(on and job is not None and job["state"] in remote.FINISHED)

    def pool_names(self):
        return sorted(self.pools)

    # --- actions ------------------------------------------------------------

    def new_pool(self):
        def got_nodes(nodes):
            dialog = PoolDialog(nodes, self)
            if dialog.exec() != QDialog.Accepted:
                return
            self.connection.request("pool.create", dialog.spec(), callback=lambda _: None, error=self.warn)

        self.connection.request("nodes.list", callback=got_nodes, error=self.warn)

    def delete_pool(self):
        name = self.selected_pool()
        pool = self.pools.get(name)
        if pool is None:
            return
        busy = f"\n\nIt is running a job, which will be cancelled." if pool.get("job") else ""
        if QMessageBox.question(self, "Delete Pool", f"Stop and delete pool {name}?{busy}") != QMessageBox.Yes:
            return
        self.connection.request("pool.delete", {"name": name, "force": True}, error=self.warn)

    def restart_pool(self):
        name = self.selected_pool()
        if name:
            self.connection.request("pool.restart", {"name": name}, error=self.warn)

    def cancel_job(self):
        job = self.jobs.get(self.selected_job())
        if job is None:
            return
        note = ("\n\nCancelling a running job restarts its pool, which then reloads its materials."
                if job["state"] == remote.RUNNING else "")
        if QMessageBox.question(self, "Cancel Job", f"Cancel {job['name']}?{note}") != QMessageBox.Yes:
            return
        self.connection.request("job.cancel", {"id": job["id"]}, error=self.warn)

    def delete_job(self):
        job = self.jobs.get(self.selected_job())
        if job is None:
            return
        if QMessageBox.question(self, "Delete Job",
                                f"Delete {job['name']} and all its files on the cluster?") != QMessageBox.Yes:
            return
        self.connection.request("job.delete", {"id": job["id"]}, error=self.warn)

    def show_log(self):
        job = self.jobs.get(self.selected_job())
        if job is not None and self.connected:
            LogDialog(self.connection, job, self).show()

    def fetch_job(self):
        job = self.jobs.get(self.selected_job())
        if job is None:
            return
        parent = QFileDialog.getExistingDirectory(self, "Download Results Into")
        if not parent:
            return
        directory = os.path.join(parent, job["name"].replace("/", "_") or job["id"])
        self.status.setText(f"Downloading {job['name']}...")
        self.connection.fetch_all(
            job["id"], directory,
            done=lambda n: self.status.setText(f"Downloaded {n} files to {directory}"),
            error=self.warn)

    def path_map(self):
        return path_map_from_settings(self.settings)

    def submit_in_place(self, workdir, name, pool, done=None, meta=None):
        """Run a job in a folder the cluster sees (workdir: its cluster path)."""
        self.connection.submit_in_place(workdir, name, pool, callback=done, error=self.warn, meta=meta)

    def submit(self, input_path, name, pool, done=None, meta=None):
        """Upload a written encore.yaml as a job. pool None: any idle pool."""
        self.connection.submit(input_path, name, pool, path_map_from_settings(self.settings),
                               callback=done, error=self.warn, meta=meta)
