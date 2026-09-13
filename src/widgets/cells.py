from PySide6.QtWidgets import QHeaderView, QTableWidget

# A panel should not be able to push the whole column arbitrarily wide.
MAX_REQUESTED_WIDTH = 460

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
    """Fix a table at the height of its own contents.

    Fixed rather than capped: inside a scroll area a mere maximum lets the
    layout squeeze the table, cutting a row in half, when what should give is
    the column -- by scrolling.
    """
    height = table.horizontalHeader().height() + 2 * table.frameWidth()
    for row in range(table.rowCount()):
        height += table.rowHeight(row)

    # A horizontal scrollbar takes height of its own. Asking whether it is
    # visible is too early here -- the layout has not run -- so compare the
    # columns against the space they have.
    needed = sum(table.columnWidth(column) for column in range(table.columnCount()))
    if table.verticalHeader().isVisible():
        needed += table.verticalHeader().width()
    if needed > table.viewport().width():
        height += table.horizontalScrollBar().sizeHint().height()

    table.setFixedHeight(height)


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

    # Ask for the width the contents actually need, so a panel in a splitter
    # is given room instead of collapsing to a hint that clips its columns.
    wanted = sum(needed) + 2 * table.frameWidth()
    if table.verticalHeader().isVisible():
        wanted += table.verticalHeader().width()
    table.setMinimumWidth(min(wanted, MAX_REQUESTED_WIDTH))


class FittedTable(QTableWidget):
    """A table whose columns re-fit whenever its width changes."""

    def resizeEvent(self, event):
        super().resizeEvent(event)
        fit_columns(self)
