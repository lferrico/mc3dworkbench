from PySide6.QtCore import Qt
from PySide6.QtWidgets import *


class EnterKeyButton(QPushButton):
    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.click()
            event.accept()
            return
        super().keyPressEvent(event)
