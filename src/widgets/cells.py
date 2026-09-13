from PySide6.QtWidgets import QHeaderView, QTableWidget

"""Helpers for sizing tables and for tables that put widgets in their cells."""


def clear_cell_widgets(table, column):
    """Drop every widget in `column`, immediately.

    setCellWidget and removeCellWidget only schedule the previous widget for
    deletion, and until that happens it stays parented to the viewport and
    keeps painting -- at its default position, on top of whatever column
    happens to be there. Unparenting first makes the removal take effect now.
    """
    for row in range(table.rowCount()):
        widget = table.cellWidget(row, column)
        if widget is None:
            continue
        table.removeCellWidget(row, column)
        widget.setParent(None)
        widget.deleteLater()


def fit_height(table):
    """Cap a table at the height of its own contents.

    A maximum rather than a fixed height: the table still shrinks, and shows a
    scrollbar, when the panel is too small for all its rows.
    """
    height = table.horizontalHeader().height() + 2 * table.frameWidth()
    for row in range(table.rowCount()):
        height += table.rowHeight(row)

    scrollbar = table.horizontalScrollBar()
    if scrollbar.isVisible():
        height += scrollbar.height()

    table.setMaximumHeight(height)


def fit_columns(table):
    """Give each column what it needs, and split anything left over evenly.

    Columns never fall below their contents, so nothing is clipped; when the
    table is wider than that, the rows still fill it instead of trailing off
    into empty space.
    """
    columns = table.columnCount()
    if not columns or not table.rowCount():
        return

    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.Interactive)

    # sectionSizeHint covers the header text but not a cell widget;
    # sizeHintForColumn covers the widget but not the header.
    needed = [max(header.sectionSizeHint(column), table.sizeHintForColumn(column))
              for column in range(columns)]

    available = table.viewport().width()
    if sum(needed) >= available:
        widths = needed
    else:
        share = available // columns
        widths = [share] * columns
        widths[-1] = available - share * (columns - 1)

    for column, width in enumerate(widths):
        table.setColumnWidth(column, width)


class FittedTable(QTableWidget):
    """A table whose columns re-fit whenever its width changes."""

    def resizeEvent(self, event):
        super().resizeEvent(event)
        fit_columns(self)
