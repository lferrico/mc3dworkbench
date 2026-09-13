import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import *

import materials
import theme
from values import format_value_for_table
from widgets.cells import FittedTable, fit_columns, fit_height
from widgets.enter_key_button import EnterKeyButton


class MaterialsPanel(QWidget):
    """The material YAML files a project uses.

    Standalone: give it nothing and it manages its own file dialog, or call
    set_paths(paths) to load a saved list.

    Signals:
        materialsChanged    a material was added or removed (arg: list of paths)
    """

    materialsChanged = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.materials = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, theme.SPACING_XS)
        layout.setSpacing(theme.SPACING_SM)

        add_btn = EnterKeyButton("Add material...")
        add_btn.clicked.connect(self.add_material_files)

        self.table = FittedTable()
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["Material", "Gap [eV]"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        # Nothing acts on a selection here, so a clicked row should not stay lit.
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.verticalHeader().setVisible(False)
        # No vertical header, so the first column header is the top-left corner.
        self.table.setProperty("cornerless", True)
        self.table.setProperty("textcells", True)
        self.table.verticalHeader().setDefaultSectionSize(theme.ROW_HEIGHT)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_context_menu)
        # Columns are sized to their contents by fit_columns after filling.

        # With nothing to list there is no table to show, just an invitation.
        self.empty_label = QLabel(
            "No materials yet. Add the YAML file of each material this device uses.")
        self.empty_label.setWordWrap(True)
        self.empty_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)

        layout.addWidget(add_btn)
        layout.addWidget(self.empty_label)
        layout.addWidget(self.table)

        self.show_materials()

    # --- Public API ---------------------------------------------------------

    def paths(self):
        return [material["path"] for material in self.materials]

    def set_paths(self, paths, show_errors=True):
        """Load a saved list. Programmatic, so no materialsChanged is emitted."""
        self.materials = []
        rejected = []
        for path in paths:
            material = materials.read_material(path)
            if material:
                self.materials.append(material)
            else:
                rejected.append(path)

        self.show_materials()
        if rejected and show_errors:
            self.report_rejected(rejected)
        return not rejected

    # --- Editing ------------------------------------------------------------

    def add_material_files(self):
        start = os.path.dirname(self.materials[-1]["path"]) if self.materials else ""
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add Material", start, "Material YAML (*.yaml *.yml)")
        if not paths:
            return

        added = 0
        rejected = []
        for path in paths:
            if path in self.paths():
                continue
            material = materials.read_material(path)
            if not material:
                rejected.append(path)
                continue
            self.materials.append(material)
            added += 1

        if added:
            self.show_materials()
            self.materialsChanged.emit(self.paths())
        if rejected:
            self.report_rejected(rejected)

    def show_context_menu(self, pos):
        row = self.table.rowAt(pos.y())
        if row < 0 or row >= len(self.materials):
            return

        menu = QMenu(self)
        remove_action = menu.addAction(f"Remove '{self.materials[row]['name']}'")
        if menu.exec(self.table.viewport().mapToGlobal(pos)) == remove_action:
            self.remove_material(row)

    def remove_material(self, row):
        if 0 <= row < len(self.materials):
            self.materials.pop(row)
            self.show_materials()
            self.materialsChanged.emit(self.paths())

    def report_rejected(self, rejected):
        names = "\n".join(os.path.basename(path) for path in rejected)
        QMessageBox.warning(
            self, "Not a Material",
            f"These files are not Encore material files:\n\n{names}\n\n"
            "A material is a YAML file with a parameters: section, either on "
            "its own or under a material: key.")

    # --- Display ------------------------------------------------------------

    def show_materials(self):
        self.table.setVisible(bool(self.materials))
        self.empty_label.setVisible(not self.materials)
        if not self.materials:
            self.table.setRowCount(0)
            return

        self.table.setRowCount(len(self.materials))
        for row, material in enumerate(self.materials):
            self.table.setItem(row, 0, self.material_item(material))
            gap = material["egap"]
            gap_item = QTableWidgetItem(format_value_for_table(gap) if gap is not None else "")
            gap_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(row, 1, gap_item)

        fit_columns(self.table)
        fit_height(self.table)

    def material_item(self, material):
        item = QTableWidgetItem(material["name"])

        detail = [material["path"]]
        if material["crystal_structure"]:
            detail.append(material["crystal_structure"])
        extras = []
        if material["has_analytic_band"]:
            extras.append("analytic bands")
        if material["has_epm"]:
            extras.append("EPM")
        detail.append(", ".join(extras) if extras else "parameters only")

        # Encore's own AlN/Params.yaml is named "Al2O3"; say so rather than
        # quietly showing one or the other.
        declared = material["declared_name"]
        if declared and declared != material["name"]:
            detail.append(f'declares name: "{declared}"')

        item.setToolTip("\n".join(detail))
        return item
