import multiprocessing
import os
import sys

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import *

import geo
import theme
from project import Project, ProjectError, SUFFIX as PROJECT_SUFFIX
from values import format_value_for_table, parse_values_input
from widgets import (CollapsibleSection, EnterKeyButton, GeoPanel, MaterialsPanel,
                     ParameterGrid)


class Workbench(QMainWindow):
    """Builds an Encore device: geometry, materials, and mesh sweeps over them."""

    def __init__(self):
        super().__init__()
        self.geo_data = None
        self.project = None
        self._loading_project = False

        project_menu = self.menuBar().addMenu("Project")
        project_menu.addAction("New Project...", self.new_project)
        project_menu.addAction("Open Project...", self.open_project)

        self.settings = QSettings("Encore", "Workbench")
        self.theme_name = self.settings.value("theme", theme.SYSTEM_THEME)
        self.build_theme_menu()
        self.apply_theme()

        # While following the desktop, track its light/dark changes live.
        QApplication.instance().styleHints().colorSchemeChanged.connect(self.apply_theme)
        self.update_title()

        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        layout = QVBoxLayout(self.central_widget)
        layout.setContentsMargins(theme.SPACING_MD, theme.SPACING_MD,
                                  theme.SPACING_MD, theme.SPACING_MD)
        layout.setSpacing(theme.SPACING_MD)

        # Sweep bar: pick a constant, give it values, add it as a column.
        input_layout = QHBoxLayout()
        input_layout.setSpacing(theme.SPACING_SM)
        self.variable_input = QComboBox()
        self.variable_input.setEnabled(False)
        self.variable_input.setMinimumWidth(200)
        self.variable_input.setPlaceholderText("Variable")
        self.variable_input.currentIndexChanged.connect(self.on_variable_selection_changed)
        self.values_input = QLineEdit(placeholderText="Values (0.01, 0.02 or 0.01:0.01:0.05)")
        self.values_input.setMinimumWidth(260)
        self.values_input.setEnabled(False)
        self.add_btn = EnterKeyButton("Add Variable")
        self.add_btn.clicked.connect(self.add_variable)
        self.add_btn.setEnabled(False)
        self.generate_btn = EnterKeyButton("Generate all")
        self.generate_btn.clicked.connect(self.generate_all_meshes)
        self.generate_btn.setEnabled(False)
        # The one action that produces output, so the only filled button.
        self.generate_btn.setProperty("class", "primary")
        input_layout.addWidget(QLabel("Sweep"))
        input_layout.addWidget(self.variable_input)
        input_layout.addWidget(self.values_input, 1)
        input_layout.addWidget(self.add_btn)
        input_layout.addWidget(self.generate_btn)

        # Project column: materials above geometry.
        self.materials_panel = MaterialsPanel()
        self.materials_panel.materialsChanged.connect(self.on_materials_changed)
        self.geo_panel = GeoPanel()
        self.geo_panel.geometryLoaded.connect(self.on_geometry_loaded)
        self.geo_panel.assignmentsChanged.connect(self.on_assignments_changed)

        self.materials_section = CollapsibleSection("Materials", self.materials_panel)
        self.geo_section = CollapsibleSection("Geometry", self.geo_panel)

        # Each section takes exactly the height its content needs; the trailing
        # stretch absorbs the rest, so folding one leaves the others in place
        # and nothing floats in the middle of the column.
        self.sections = [self.materials_section, self.geo_section]
        column = QWidget()
        column_layout = QVBoxLayout(column)
        column_layout.setContentsMargins(0, 0, 0, 0)
        column_layout.setSpacing(theme.SPACING_MD)
        for section in self.sections:
            column_layout.addWidget(section)
        column_layout.addStretch(1)

        # The column is as tall as its content, so it scrolls rather than
        # squeezing the tables when there is more than the window can show.
        project_column = QScrollArea()
        project_column.setWidget(column)
        project_column.setWidgetResizable(True)
        project_column.setFrameShape(QFrame.NoFrame)
        project_column.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.grid = ParameterGrid()
        self.grid.rowExportRequested.connect(self.generate_row_mesh)
        self.grid.gridChanged.connect(self.save_project)

        sweep_column = QWidget()
        sweep_layout = QVBoxLayout(sweep_column)
        sweep_layout.setContentsMargins(0, 0, 0, 0)
        sweep_layout.setSpacing(theme.SPACING_SM)
        sweep_layout.addLayout(input_layout)
        sweep_layout.addWidget(self.grid)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(project_column)
        splitter.addWidget(sweep_column)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([320, 680])
        layout.addWidget(splitter)

        self.resize(1100, 620)

    # --- Project -----------------------------------------------------------

    def new_project(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Create Project", f"project{PROJECT_SUFFIX}",
            f"Encore Project (*{PROJECT_SUFFIX})")
        if not path:
            return

        try:
            self.project = Project.create(path)
        except ProjectError as exc:
            QMessageBox.warning(self, "Create Failed", str(exc))
            return

        self.update_title()
        # A new project keeps whatever is already loaded, so record it.
        self.save_project()

    def open_project(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Project", "", f"Encore Project (*{PROJECT_SUFFIX})")
        if not path:
            return

        self.open_project_file(path)

    def open_project_file(self, path):
        try:
            project = Project.open(path)
        except ProjectError as exc:
            QMessageBox.warning(self, "Open Failed", str(exc))
            return False

        self.project = project
        self.update_title()

        # Restoring triggers the same signals as editing, so hold off saving
        # until everything is back -- otherwise loading the geometry would
        # write its empty assignments over the ones still to be restored.
        self._loading_project = True
        try:
            materials = [project.resolve(path) for path in project.get("materials", [])]
            self.materials_panel.set_paths(materials, show_errors=False)
            self.geo_panel.set_materials(self.material_names())

            geometry = project.resolve(project.get("geometry", ""))
            if geometry and self.geo_panel.load(geometry, show_errors=False):
                # Loading clears assignments and the sweep, so both are put
                # back afterwards.
                self.geo_panel.set_assignments(project.get("volumes", {}))
                self.restore_sweep(project)
        finally:
            self._loading_project = False

        return True

    def save_project(self):
        if self.project is None or self._loading_project:
            return

        self.project.set("geometry",
                         self.project.relative(self.geo_data["path"]) if self.geo_data else "")
        self.project.set("materials",
                         [self.project.relative(path) for path in self.materials_panel.paths()])
        self.project.set("volumes", dict(self.geo_panel.assignments))
        self.project.set("sweep", self.grid.parameters)
        self.project.set("hidden_rows", sorted(self.grid.hidden_row_keys))
        try:
            self.project.save()
        except ProjectError as exc:
            QMessageBox.warning(self, "Save Failed", str(exc))

    def restore_sweep(self, project):
        """Put back the saved sweep columns, minus any the geometry has lost."""
        saved = project.get("sweep", [])
        if not isinstance(saved, list):
            return

        known = {constant["name"] for constant in self.geo_data["constants"]} if self.geo_data else set()
        kept, dropped = [], []
        for param in saved:
            if not isinstance(param, dict) or not param.get("name") or not param.get("values"):
                continue
            (kept if param["name"] in known else dropped).append(param)

        self.grid.set_state(kept, project.get("hidden_rows", []))

        if dropped:
            # Keeping them would be worse than dropping them: gmsh ignores a
            # constant the .geo does not declare, so every row of that sweep
            # would quietly mesh identically.
            names = ", ".join(geo.short_name(param["name"]) for param in dropped)
            QMessageBox.information(
                self, "Sweep Variables Dropped",
                f"{os.path.basename(self.geo_data['path'])} no longer declares "
                f"these constants, so they were removed from the sweep:\n\n{names}")

    def build_theme_menu(self):
        themes = theme.available_themes()
        view_menu = self.menuBar().addMenu("View")
        theme_menu = view_menu.addMenu("Theme")

        group = QActionGroup(self)
        group.setExclusive(True)

        # System first, then the two built-ins, then everything Omarchy offers.
        names = [theme.SYSTEM_THEME, "Light", "Dark"]
        names += [name for name in sorted(themes) if name not in names]

        for index, name in enumerate(names):
            if index == 3:
                theme_menu.addSeparator()
            action = theme_menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(name == self.theme_name)
            action.setData(name)
            group.addAction(action)
            action.triggered.connect(lambda checked=False, chosen=name: self.choose_theme(chosen))

    def choose_theme(self, name):
        self.theme_name = name
        self.settings.setValue("theme", name)
        self.apply_theme()

    def apply_theme(self, *_):
        theme.apply(QApplication.instance(), self.theme_name)

    def update_title(self):
        name = self.project.name if self.project else "no project"
        self.setWindowTitle(f"Encore Workbench - {name}")

    def material_names(self):
        return [material["name"] for material in self.materials_panel.materials]

    def on_materials_changed(self, paths):
        self.geo_panel.set_materials(self.material_names())
        self.save_project()

    def on_assignments_changed(self, assignments):
        self.save_project()

    # --- Geometry ----------------------------------------------------------

    def on_geometry_loaded(self, geo_data):
        self.geo_data = geo_data
        self.geo_panel.set_materials(self.material_names())

        # A new geometry has its own constants, so previous sweep columns are
        # meaningless: start the table over.
        self.grid.set_state([])

        self.variable_input.clear()
        for constant in geo_data["constants"]:
            if constant["read_only"]:
                continue
            self.variable_input.addItem(geo.short_name(constant["name"]), constant["name"])

        has_constants = self.variable_input.count() > 0
        self.variable_input.setEnabled(has_constants)
        self.values_input.setEnabled(has_constants)
        self.add_btn.setEnabled(has_constants)
        self.generate_btn.setEnabled(True)
        self.on_variable_selection_changed()
        self.save_project()

    def constant(self, name):
        for constant in self.geo_data["constants"]:
            if constant["name"] == name:
                return constant
        return None

    def on_variable_selection_changed(self, index=None):
        constant = self.constant(self.variable_input.currentData())
        if constant is None:
            self.values_input.setPlaceholderText("Values")
            return

        current = format_value_for_table(constant["value"])
        self.values_input.setPlaceholderText(f"Values (default {current})")

    # --- Sweep -------------------------------------------------------------

    def add_variable(self):
        if self.geo_data is None:
            QMessageBox.information(self, "Select Geometry", "Please select a .geo file first.")
            return

        name = self.variable_input.currentData()
        constant = self.constant(name)
        if constant is None:
            return

        if self.grid.has_parameter(name):
            QMessageBox.information(self, "Already Added",
                                    f"'{geo.short_name(name)}' is already in the table.")
            return

        value_type = constant["value_type"]
        typed_text = self.values_input.text().strip()
        if typed_text:
            values, error = parse_values_input(typed_text, value_type)
            if error:
                QMessageBox.warning(self, "Invalid Values", error)
                return
            if not values:
                QMessageBox.warning(self, "No Values", "At least one value is required.")
                return
        else:
            # No input means sweep the value the .geo already declares.
            values = [constant["value"]]

        self.values_input.clear()
        self.grid.add_parameter(name, values, value_type, label=geo.short_name(name))

    # --- Meshing -----------------------------------------------------------

    def generate_row_mesh(self, row_data):
        if self.geo_data is None:
            return

        default_name = f"{self.mesh_name(row_data)}.msh"
        path, _ = QFileDialog.getSaveFileName(self, "Write Mesh", default_name, "Gmsh Mesh (*.msh)")
        if not path:
            return

        if not path.lower().endswith(".msh"):
            path = f"{path}.msh"

        try:
            result = geo.generate_mesh(self.geo_data["path"], row_data, path)
        except geo.GeoError as exc:
            QMessageBox.warning(self, "Meshing Failed", f"Could not mesh this geometry:\n\n{exc}")
            return

        QMessageBox.information(
            self, "Mesh Written",
            f"{os.path.basename(result['path'])}\n"
            f"{result['nodes']} nodes, {result['elements']} elements")

    def generate_all_meshes(self):
        if self.geo_data is None:
            return

        rows = self.grid.rows()
        if not rows:
            QMessageBox.information(self, "No Rows", "There are no sweep points to mesh.")
            return

        directory = QFileDialog.getExistingDirectory(self, "Select Output Folder")
        if not directory:
            return

        progress = QProgressDialog(f"Meshing {len(rows)} geometries...", "Cancel", 0, len(rows), self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)

        written = 0
        for index, row_data in enumerate(rows):
            progress.setValue(index)
            progress.setLabelText(f"Meshing {index + 1} of {len(rows)}...")
            QApplication.processEvents()
            if progress.wasCanceled():
                break

            path = self.unique_mesh_path(directory, self.mesh_name(row_data))
            try:
                geo.generate_mesh(self.geo_data["path"], row_data, path)
            except geo.GeoError as exc:
                progress.close()
                QMessageBox.warning(
                    self, "Meshing Failed",
                    f"Stopped after {written} mesh(es).\n\n{os.path.basename(path)}:\n{exc}")
                return
            written += 1

        progress.setValue(len(rows))
        QMessageBox.information(self, "Meshing Complete", f"Wrote {written} mesh file(s).")

    def unique_mesh_path(self, directory, base_name):
        candidate = os.path.join(directory, f"{base_name}.msh")
        if not os.path.exists(candidate):
            return candidate

        idx = 2
        while True:
            candidate = os.path.join(directory, f"{base_name}_{idx}.msh")
            if not os.path.exists(candidate):
                return candidate
            idx += 1

    def mesh_name(self, row_data):
        parts = [os.path.splitext(os.path.basename(self.geo_data["path"]))[0]]
        for param in self.grid.parameters:
            name = param["name"]
            if name in row_data:
                value_text = format_value_for_table(row_data[name])
                parts.append(f"{geo.short_name(name)}{value_text}")

        return "_".join(self.sanitize_name_token(part) for part in parts if part)

    def sanitize_name_token(self, token):
        allowed = "-_."
        cleaned = []
        for ch in str(token):
            if ch.isalnum() or ch in allowed:
                cleaned.append(ch)
            else:
                cleaned.append("-")

        sanitized = "".join(cleaned).strip("-_")
        return sanitized or "value"


def main(argv):
    # geo calls spawn a child process; PyInstaller builds need this first.
    multiprocessing.freeze_support()

    app = QApplication(argv)
    theme.load_fonts()
    window = Workbench()
    window.show()

    # An optional project to open: encore [project.encore.yaml]
    arguments = [a for a in argv[1:] if not a.startswith("-")]
    if arguments:
        window.open_project_file(os.path.abspath(arguments[0]))

    return app.exec()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
