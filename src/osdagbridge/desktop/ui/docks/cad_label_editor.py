"""
In-place editing of 2D CAD dimension labels.

The CAD views paint their dimension labels directly with QPainter, so there is
no widget to click on. While painting, each view registers the rectangle of the
labels it draws (``register_cad_label``). A double-click inside an editable
label covers it with a temporary QLineEdit; Enter or clicking elsewhere commits
the value and emits ``label_value_edited(key, value)``, Esc cancels.

The view itself never writes inputs: whoever owns the input dictionaries
(the template page) validates the value, stores it and triggers the redraw.
"""

from PySide6.QtCore import Qt, QEvent, QRectF, QLocale
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import QLineEdit, QToolTip

from osdagbridge.core.utils.common import (
    KEY_SPAN, KEY_CARRIAGEWAY_WIDTH, KEY_TS_GIRDER_SPACING, KEY_TS_DECK_THICKNESS,
    KEY_TS_DECK_OVERHANG, KEY_TS_OVERALL_WIDTH, KEY_MP_CB_SPACING,
)

# Editable labels: input key -> (CAD param name, CAD units (mm) -> input units factor, decimals, unit)
CAD_EDITABLE_LABELS = {
    KEY_CARRIAGEWAY_WIDTH:  ("carriageway_width", 1 / 1000, 2, "m"),
    KEY_TS_GIRDER_SPACING:  ("girder_spacing",    1 / 1000, 2, "m"),
    KEY_TS_DECK_THICKNESS:  ("deck_thickness",    1,        0, "mm"),
    KEY_SPAN:               ("span_length",       1 / 1000, 2, "m"),
}

# Derived labels stay read-only so a redraw can never get an inconsistent layout
CAD_LOCKED_LABELS = {
    KEY_TS_DECK_OVERHANG: "Overhang is derived from the overall width, girder spacing and number of girders.",
    KEY_TS_OVERALL_WIDTH: "Overall bridge width is derived from the carriageway, footpaths, barriers and median.",
    KEY_MP_CB_SPACING:    "Bracing spacing is derived from the span and the number of cross bracings.",
}

TOOLTIP_EDITABLE = "Double-click to edit"
TOOLTIP_LOCKED = "Read-only (derived value). {}"

_EDITOR_MIN_WIDTH = 90
_EDITOR_STYLE = (
    "QLineEdit { border: 1px solid #90AF13; background: #ffffff; "
    "padding: 0px 2px; font: 9pt 'Arial'; }"
)


class CadLabelEditMixin:
    """Mixin for QPainter-based CAD widgets. The widget class must define
    ``label_value_edited = Signal(str, float)`` and a ``params`` dict (in mm)."""

    def _init_label_editing(self):
        self.label_editing_enabled = False   # turned on only for the main template page views
        self._label_zones = []               # [(QRectF, key)] in widget coordinates
        self._label_editor = None
        self._label_editor_key = None
        self._label_hover_key = None

    # ── Registration (called while painting) ─────────────────────────────────

    def _reset_label_zones(self):
        self._label_zones = []

    def register_cad_label(self, painter, rect, key):
        """Remember where the label for ``key`` was drawn in this paint pass."""
        if rect is None or key not in CAD_EDITABLE_LABELS and key not in CAD_LOCKED_LABELS:
            return
        self._label_zones.append((painter.worldTransform().mapRect(QRectF(rect)), key))

    def _label_key_at(self, pos):
        for rect, key in reversed(self._label_zones):
            if rect.contains(pos):
                return key, rect
        return None, None

    # ── Mouse handling ───────────────────────────────────────────────────────

    def _update_label_hover(self, pos):
        """Hand cursor + tooltip over editable labels, tooltip only over locked ones."""
        if not self.label_editing_enabled:
            return
        key, _ = self._label_key_at(pos)
        if key == self._label_hover_key:
            return
        self._label_hover_key = key
        if key in CAD_EDITABLE_LABELS:
            self.setCursor(Qt.PointingHandCursor)
            self.setToolTip(TOOLTIP_EDITABLE)
        elif key in CAD_LOCKED_LABELS:
            self.unsetCursor()
            self.setToolTip(TOOLTIP_LOCKED.format(CAD_LOCKED_LABELS[key]))
        else:
            self.unsetCursor()
            self.setToolTip("")

    def mouseDoubleClickEvent(self, event):
        pos = event.position() if hasattr(event, "position") else event.pos()
        key, rect = self._label_key_at(pos) if self.label_editing_enabled else (None, None)
        if key is None:
            super().mouseDoubleClickEvent(event)
            return
        event.accept()
        if key in CAD_LOCKED_LABELS:
            QToolTip.showText(
                event.globalPosition().toPoint(),
                TOOLTIP_LOCKED.format(CAD_LOCKED_LABELS[key]), self,
            )
            return
        self.open_label_editor(key, rect)

    # ── Inline editor ────────────────────────────────────────────────────────

    def cad_label_value(self, key):
        """Current value of an editable label, in the units shown on the label."""
        param, factor, decimals, _ = CAD_EDITABLE_LABELS[key]
        value = self.params.get(param)
        if value is None:
            return None
        return round(float(value) * factor, decimals)

    def open_label_editor(self, key, rect):
        self.close_label_editor()
        _, _, decimals, _ = CAD_EDITABLE_LABELS[key]

        editor = QLineEdit(self)
        validator = QDoubleValidator(0.0, 1e9, max(decimals, 2), editor)
        validator.setNotation(QDoubleValidator.StandardNotation)
        validator.setLocale(QLocale.c())
        editor.setValidator(validator)
        editor.setStyleSheet(_EDITOR_STYLE)
        editor.setAlignment(Qt.AlignCenter)

        value = self.cad_label_value(key)
        if value is not None:
            editor.setText(f"{value:.{decimals}f}")

        geo = rect.toAlignedRect()
        if geo.width() < _EDITOR_MIN_WIDTH:
            geo.setX(geo.center().x() - _EDITOR_MIN_WIDTH // 2)
            geo.setWidth(_EDITOR_MIN_WIDTH)
        geo.setHeight(max(geo.height(), editor.sizeHint().height()))
        editor.setGeometry(geo)

        editor.editingFinished.connect(self._commit_label_editor)   # Enter or focus out
        editor.installEventFilter(self)

        self._label_editor = editor
        self._label_editor_key = key
        editor.show()
        editor.setFocus(Qt.MouseFocusReason)
        editor.selectAll()

    def close_label_editor(self):
        editor, self._label_editor, self._label_editor_key = self._label_editor, None, None
        if editor is not None:
            editor.blockSignals(True)
            editor.hide()
            editor.deleteLater()
            self.update()

    def _commit_label_editor(self):
        editor, key = self._label_editor, self._label_editor_key
        if editor is None:
            return
        text = editor.text().strip()
        old_value = self.cad_label_value(key)
        self.close_label_editor()
        try:
            value = float(text)
        except ValueError:
            return   # empty or incomplete entry: keep the current value
        _, _, decimals, _ = CAD_EDITABLE_LABELS[key]
        if old_value is not None and round(value, decimals) == old_value:
            return
        self.label_value_edited.emit(key, value)

    def eventFilter(self, obj, event):
        if obj is self._label_editor and event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape:
            self.close_label_editor()
            return True
        return super().eventFilter(obj, event)
