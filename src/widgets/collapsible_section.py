from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import *

import theme

# Qt's "no maximum": restoring this lets a splitter give the section space again.
UNLIMITED_HEIGHT = 16777215


class CollapsibleSection(QWidget):
    """A titled panel whose content folds away when the title is clicked.

    Signals:
        toggled    the section was expanded or collapsed (arg: bool)
    """

    toggled = Signal(bool)

    def __init__(self, title, content, expanded=True, parent=None):
        super().__init__(parent)
        self.content = content

        self.header = QToolButton()
        self.header.setText(title)
        self.header.setCheckable(True)
        self.header.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.header.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.header.setObjectName("sectionHeader")
        self.header.toggled.connect(self.on_toggled)

        line = QFrame()
        line.setObjectName("sectionRule")
        line.setFrameShape(QFrame.HLine)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.SPACING_XS)
        layout.addWidget(self.header)
        layout.addWidget(line)
        layout.addWidget(content)

        self.header.setChecked(expanded)
        self.on_toggled(expanded)

    def is_expanded(self):
        return self.header.isChecked()

    def collapsed_height(self):
        """The height this section takes with its content folded away."""
        return self.header.sizeHint().height() + theme.SPACING_SM

    def set_expanded(self, expanded):
        self.header.setChecked(expanded)

    def on_toggled(self, expanded):
        self.header.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self.content.setVisible(expanded)

        # Collapsing only frees space if the section stops claiming it, which
        # a splitter otherwise keeps handing back.
        self.setMaximumHeight(UNLIMITED_HEIGHT if expanded else self.collapsed_height())
        self.toggled.emit(expanded)
