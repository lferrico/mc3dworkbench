from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import *

import theme
import tools
from values import format_value_for_table, parse_single_value
from widgets.cells import FittedTable, clear_cell_widgets, fit_columns, fit_height

SWEPT = "(swept)"
INDENT = "    "


def display_value(value, unit):
    """A value with its unit alongside it, which is how it is edited too."""
    text = format_value_for_table(value)
    return f"{text} {unit}" if unit else text


def strip_unit(text, unit):
    """Take the unit back off, so a value can be edited with it left in place."""
    text = text.strip()
    if unit and text.endswith(unit):
        text = text[: -len(unit)].strip()
    return text


class ToolPanel(QWidget):
    """One tool's settings: every field it has, and the value it will be given.

    A field carries its type's default until the project overrides it, and a
    field the sweep table varies is shown as swept rather than as one value,
    because the row decides it.

    The first row is the tool's name, which is both its `sim:` entry name in
    the generated input and the prefix of its sweep variables, so renaming it
    is the window's business rather than the panel's.

    Signals:
        valuesChanged     a setting was edited (arg: the tool dict)
        renameRequested   the name row was edited (args: the tool, the new name)
        removeRequested   the tool should be removed (arg: the tool dict)
    """

    valuesChanged = Signal(object)
    renameRequested = Signal(object, str)
    removeRequested = Signal(object)

    def __init__(self, tool, parent=None):
        super().__init__(parent)
        self.tool = tool
        self.swept = set()
        self.material_names = []
        # Row number -> field. Group headers occupy rows of their own, so the
        # two no longer line up.
        self.row_fields = {}
        self._filling = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, theme.SPACING_XS)
        layout.setSpacing(theme.SPACING_SM)

        self.table = FittedTable()
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["Setting", "Value"])
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(theme.ROW_HEIGHT)
        self.table.setProperty("cornerless", True)
        # Not "textcells": this table puts dropdowns in cells, and the item
        # padding that property adds would inset them.
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_context_menu)
        self.table.itemChanged.connect(self.handle_item_changed)

        layout.addWidget(self.table)
        self.show_fields()

    # --- Public API ---------------------------------------------------------

    def set_materials(self, names):
        """Offer these materials wherever a setting names one."""
        self.material_names = list(names)

        # A setting pointing at a material the project no longer has would
        # generate an input Encore cannot resolve, so it is cleared instead.
        values = self.tool.get("values") or {}
        for key in list(values):
            if tools.is_material_field(key) and values[key] not in self.material_names:
                values.pop(key)

        self.show_fields()

    def set_swept(self, swept_keys):
        """Which of this tool's fields the sweep table is varying."""
        self.swept = set(swept_keys)
        self.show_fields()

    # --- Editing ------------------------------------------------------------

    def handle_item_changed(self, item):
        if self._filling or item.column() != 1:
            return

        if item.row() == 0:
            self.renameRequested.emit(self.tool, item.text().strip())
            return

        field = self.field_at(item.row())
        if field is None:
            return

        if field["value_type"] == "bool":
            self.store(field, item.checkState() == Qt.Checked)
            return

        text = strip_unit(item.text(), field["unit"])

        if not text:
            # Cleared means "back to the default".
            self.tool.get("values", {}).pop(field["key"], None)
            self.show_fields()
            self.valuesChanged.emit(self.tool)
            return

        value, ok = parse_single_value(text, field["value_type"])
        if not ok:
            self.show_fields()
            QMessageBox.warning(self, "Invalid Value",
                                f"'{text}' is not a valid {field['value_type']} "
                                f"for {field['label']}.")
            return

        self.store(field, value)

    def store(self, field, value):
        """Record a setting, or drop it when it matches the type's default."""
        if value == field["default"]:
            self.tool.get("values", {}).pop(field["key"], None)
        else:
            self.tool.setdefault("values", {})[field["key"]] = value

        self.show_fields()
        self.valuesChanged.emit(self.tool)

    def value_combo(self, field, value):
        """A chooser for a setting whose values are fixed or come from the project."""
        if tools.is_material_field(field["key"]):
            # Blank is meaningful here: a material has no sensible default.
            choices = [""] + self.material_names
        else:
            choices = list(field["choices"])

        combo = QComboBox()
        for choice in choices:
            combo.addItem(format_value_for_table(choice), choice)

        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

        font = combo.font()
        font.setBold(not tools.is_default(self.tool, field["key"]))
        combo.setFont(font)

        combo.currentIndexChanged.connect(
            lambda position, key=field["key"], default=field["default"]:
            self.on_choice_picked(key, default, combo.itemData(position)))
        return combo

    def on_choice_picked(self, key, default, value):
        if self._filling:
            return

        # Choosing what the field would have been anyway leaves it inherited
        # rather than recording a redundant override.
        if value == default or value in ("", None):
            self.tool.get("values", {}).pop(key, None)
        else:
            self.tool.setdefault("values", {})[key] = value

        self.show_fields()
        self.valuesChanged.emit(self.tool)

    def field_at(self, row):
        """The field a table row shows, or None for the name and group rows."""
        return self.row_fields.get(row)

    def show_context_menu(self, pos):
        row = self.table.rowAt(pos.y())
        menu = QMenu(self)
        reset_action = None
        field = self.field_at(row)
        if field and not tools.is_default(self.tool, field["key"]):
            reset_action = menu.addAction(f"Reset '{field['label']}' to default")

        remove_action = menu.addAction(f"Remove '{self.tool['name']}'")
        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen == remove_action:
            self.removeRequested.emit(self.tool)
        elif chosen == reset_action:
            self.tool.get("values", {}).pop(field["key"], None)
            self.show_fields()
            self.valuesChanged.emit(self.tool)

    # --- Display ------------------------------------------------------------

    def show_fields(self):
        fields = tools.fields(self.tool["type"])
        values = tools.effective_values(self.tool)

        # A nested setting -- a vector, sim_time, band_model -- reads as one
        # thing with parts, so it gets a heading and its members are indented
        # under it, mirroring the shape it takes in the generated file.
        layout = []
        group = None
        for field in fields:
            parts = field["key"].split(".")
            if len(parts) > 1:
                if parts[0] != group:
                    group = parts[0]
                    layout.append((None, group))
                layout.append((field, parts[-1]))
            else:
                group = None
                layout.append((field, field["label"]))

        self._filling = True
        try:
            clear_cell_widgets(self.table, 1)
            self.row_fields = {}
            self.table.setRowCount(len(layout) + 1)

            name_label = QTableWidgetItem("name")
            name_label.setFlags(name_label.flags() & ~Qt.ItemIsEditable)
            name_label.setToolTip("The sim entry's name in the generated input,\n"
                                  "and the prefix of this tool's sweep variables")
            self.table.setItem(0, 0, name_label)
            name_value = QTableWidgetItem(self.tool["name"])
            font = name_value.font()
            font.setBold(True)
            name_value.setFont(font)
            self.table.setItem(0, 1, name_value)

            for index, (field, label) in enumerate(layout):
                row = index + 1

                if field is None:
                    heading = QTableWidgetItem(label)
                    heading.setFlags(heading.flags() & ~Qt.ItemIsEditable)
                    font = heading.font()
                    font.setBold(True)
                    heading.setFont(font)
                    self.table.setItem(row, 0, heading)
                    blank = QTableWidgetItem()
                    blank.setFlags(blank.flags() & ~Qt.ItemIsEditable)
                    self.table.setItem(row, 1, blank)
                    continue

                self.row_fields[row] = field
                key = field["key"]

                name_item = QTableWidgetItem(f"{INDENT}{label}" if "." in key else label)
                name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
                detail = [f"default: {display_value(field['default'], field['unit'])}",
                          "required" if field["required"] else "optional"]
                if key in self.swept:
                    detail.append("varied by the sweep table")
                name_item.setToolTip("\n".join(detail))
                self.table.setItem(row, 0, name_item)

                if key in self.swept:
                    value_item = QTableWidgetItem(SWEPT)
                    value_item.setFlags(value_item.flags() & ~Qt.ItemIsEditable)
                elif tools.is_material_field(key) or field["choices"]:
                    self.table.setItem(row, 1, QTableWidgetItem())
                    self.table.setCellWidget(row, 1, self.value_combo(field, values[key]))
                    continue
                elif field["value_type"] == "bool":
                    # A checkable item rather than a checkbox widget: it needs
                    # no cleaning up between rebuilds.
                    value_item = QTableWidgetItem()
                    value_item.setFlags((value_item.flags() | Qt.ItemIsUserCheckable)
                                        & ~Qt.ItemIsEditable)
                    value_item.setCheckState(Qt.Checked if values[key] else Qt.Unchecked)
                else:
                    value_item = QTableWidgetItem(display_value(values[key], field["unit"]))
                    font = value_item.font()
                    # A value the project set reads differently from one it inherited.
                    font.setBold(not tools.is_default(self.tool, key))
                    value_item.setFont(font)
                self.table.setItem(row, 1, value_item)
        finally:
            self._filling = False

        fit_columns(self.table)
        fit_height(self.table)
