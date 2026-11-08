"""Undo/redo for edits made through the bridge.

Krita's scripting API does not put most edits on Krita's undo stack (pixel writes, filters,
layer properties and moves are not recorded; layer add/remove are). So every editing command
runs inside an `Op` that
  * backs up, before the change, whatever Krita will not record (pixels, properties, selection), and
  * measures how many steps Krita itself recorded meanwhile.
Undoing an Op triggers Krita's undo that many times and then re-applies the backups wherever the
state still differs. Backups are absolute states and are only written when needed, so they never add
Krita undo steps for changes Krita already reverted.

Before undoing, the top of Krita's history must still look like it did right after the Op;
otherwise the user edited in Krita meanwhile and we refuse instead of overwriting their work.
"""
import zlib

from krita import Krita

try:
    from PyQt6.QtCore import QByteArray, QCoreApplication, QEventLoop, QRect
    from PyQt6.QtWidgets import QAbstractItemView
    _NO_INPUT = QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents
except ImportError:
    from PyQt5.QtCore import QByteArray, QCoreApplication, QEventLoop, QRect
    from PyQt5.QtWidgets import QAbstractItemView
    _NO_INPUT = QEventLoop.ExcludeUserInputEvents

from .util import BridgeError

MAX_RECORDS = 100
MAX_BYTES = 256 * 1024 * 1024  # compressed backups kept in memory
MAX_BACKUP_PIXELS = 64_000_000
TAIL = 6  # Krita history entries compared to detect outside edits

_undo, _redo = [], []
_current = None


# ------------------------------------------------------------- krita state
def history_view():
    """The History docker's list view (its model mirrors the active document's undo stack)."""
    for d in Krita.instance().dockers():
        if d.objectName() == "History":
            for v in d.findChildren(QAbstractItemView):
                if v.model() is not None:
                    return v
    return None


def krita_state():
    """(index of the current step, names of all steps) or None if the History docker is missing."""
    # Some commands (e.g. blending-mode changes) reach Krita's undo stack through queued signals;
    # deliver them first or they would be counted against the next operation.
    for _ in range(3):
        QCoreApplication.processEvents(_NO_INPUT)
    v = history_view()
    if v is None:
        return None
    m = v.model()
    names = [str(m.data(m.index(i, 0))) for i in range(m.rowCount())]
    sel = v.selectionModel()
    cur = sel.currentIndex().row() if sel is not None and sel.currentIndex().isValid() else len(names) - 1
    return cur, names


def _done(state):
    return state[1][:state[0] + 1]


def recorded_steps(before, after):
    """How many steps Krita added between two states (handles the stack dropping old entries)."""
    if before is None or after is None:
        return 0
    b, a = _done(before), _done(after)
    if len(a) >= len(b) and a[:len(b)] == b:
        return len(a) - len(b)
    for k in range(1, len(b)):  # stack is at its size limit: oldest entries fell off
        if a[:len(b) - k] == b[k:]:
            return len(a) - (len(b) - k)
    return 0


def _tail(state):
    return None if state is None else _done(state)[-TAIL:]


# ------------------------------------------------------------- backups
def _rect_union(a, b):
    if a.width() <= 0 or a.height() <= 0:
        return b
    if b.width() <= 0 or b.height() <= 0:
        return a
    return a.united(b)


class LayerBackup:
    """Pixels + offset of a layer over a rectangle (default: its content united with the canvas)."""

    def __init__(self, node, doc, rect=None):
        self.node = node
        r = rect if rect is not None else _rect_union(node.bounds(), QRect(0, 0, doc.width(), doc.height()))
        if r.width() * r.height() > MAX_BACKUP_PIXELS:
            raise BridgeError("layer too large to back up for undo")
        self.rect = (r.x(), r.y(), r.width(), r.height())
        pos = node.position()
        self.pos = (pos.x(), pos.y())
        self.data = zlib.compress(bytes(node.pixelData(*self.rect)), 1)
        self.doc = doc

    def size(self):
        return len(self.data)

    def capture(self):
        """The layer's state now, over an area that also covers this backup (for the opposite direction)."""
        if self.node.parentNode() is None:
            return self
        return LayerBackup(self.node, self.doc, _rect_union(self.node.bounds(), QRect(*self.rect)))

    def apply(self):
        n = self.node
        if n.parentNode() is None:
            return  # not in the image (Krita removed it); nothing to put back
        x, y, w, h = self.rect
        pos, now = n.position(), n.bounds()
        if (pos.x(), pos.y()) == self.pos and QRect(*self.rect).contains(now) and \
                bytes(n.pixelData(x, y, w, h)) == zlib.decompress(self.data):
            return  # already in this state (e.g. Krita's own undo restored it)
        n.move(*self.pos)
        clear = _rect_union(n.bounds(), QRect(x, y, w, h))
        n.setPixelData(QByteArray(bytes(clear.width() * clear.height() * 4)),
                       clear.x(), clear.y(), clear.width(), clear.height())
        n.setPixelData(QByteArray(zlib.decompress(self.data)), x, y, w, h)


PROPS = ("name", "visible", "locked", "opacity", "blendingMode", "alphaLocked", "inheritAlpha", "colorLabel")


class PropsBackup:
    def __init__(self, node):
        self.node = node
        self.props = {}
        for p in PROPS:
            try:
                self.props[p] = getattr(node, p)()
            except AttributeError:
                pass
        pos = node.position()
        self.pos = (pos.x(), pos.y())

    def size(self):
        return 256

    def capture(self):
        return self if self.node.parentNode() is None else PropsBackup(self.node)

    def apply(self):
        n = self.node
        if n.parentNode() is None:
            return
        # only touch what differs: some setters add Krita undo steps of their own
        for p, v in self.props.items():
            if getattr(n, p)() != v:
                getattr(n, "set" + p[0].upper() + p[1:])(v)
        pos = n.position()
        if (pos.x(), pos.y()) != self.pos:
            n.move(*self.pos)


def _same_selection(a, b):
    if a is None or b is None:
        return a is None and b is None
    box = (a.x(), a.y(), a.width(), a.height())
    if box != (b.x(), b.y(), b.width(), b.height()):
        return False
    return bytes(a.pixelData(*box)) == bytes(b.pixelData(*box))


class SelectionBackup:
    def __init__(self, doc):
        self.doc = doc
        sel = doc.selection()
        self.sel = None if sel is None else sel.duplicate()

    def size(self):
        return 4096 if self.sel is None else self.sel.width() * self.sel.height() // 4 + 64

    def capture(self):
        return SelectionBackup(self.doc)

    def apply(self):
        if not _same_selection(self.doc.selection(), self.sel):
            self.doc.setSelection(None if self.sel is None else self.sel.duplicate())


class CallbackBackup:
    """Generic: `undo()` reverts, `redo()` re-applies. Dropped when Krita recorded the change itself."""

    fallback = True

    def __init__(self, undo, redo):
        self._undo, self._redo = undo, redo

    def size(self):
        return 256

    def capture(self):
        return CallbackBackup(self._redo, self._undo)

    def apply(self):
        self._undo()


# ------------------------------------------------------------- operations
class Op:
    def __init__(self, method, doc):
        self.method, self.doc = method, doc
        self.backups = []
        self.before = krita_state()
        self.after = None
        self.krita_steps = 0

    def size(self):
        return sum(b.size() for b in self.backups) + 64

    def finish(self):
        self.after = krita_state()
        self.krita_steps = recorded_steps(self.before, self.after)
        if self.krita_steps:
            self.backups = [b for b in self.backups if not getattr(b, "fallback", False)]

    def changed(self):
        return bool(self.backups) or self.krita_steps > 0

    def describe(self):
        return {"command": self.method, "krita_steps": self.krita_steps}


def _keep(b):
    if _current is not None:
        _current.backups.append(b)


def backup_layer(node, doc, rect=None):
    """Call BEFORE changing a layer's pixels or position."""
    if _current is not None:
        _keep(LayerBackup(node, doc, rect))


def backup_props(node):
    if _current is not None:
        _keep(PropsBackup(node))


def backup_selection(doc):
    if _current is not None:
        _keep(SelectionBackup(doc))


def backup_callback(undo, redo):
    if _current is not None:
        _keep(CallbackBackup(undo, redo))


def run(method, fn, params, doc):
    """Run an editing command, recording what is needed to undo it."""
    global _current
    if doc is None or _current is not None:  # no document, or nested inside another op (batch)
        return fn(params)
    op = Op(method, doc)
    _current = op
    try:
        return fn(params)
    finally:
        _current = None
        op.finish()
        if op.changed():
            _undo.append(op)
            del _redo[:]
            _trim()


def _trim():
    total = sum(o.size() for o in _undo)
    while _undo and (len(_undo) > MAX_RECORDS or total > MAX_BYTES):
        total -= _undo.pop(0).size()


def flush_events(rounds=3):
    for _ in range(rounds):
        QCoreApplication.processEvents(_NO_INPUT)


def wait_idle(doc, timeout=120.0):
    """Wait until Krita has finished every queued operation on the image - while its event loop keeps running.

    Never block in doc.waitForDone() on this (GUI) thread: with Instant Preview (big brushes), Krita paints a
    quick low-resolution version first and starts the full-resolution stroke from a timer on this thread, so
    a blocking wait never ends ("Waiting for image operation to complete... Scripted Brush Stroke", Krita
    frozen at 0% CPU). Polling tryBarrierLock and letting timers run avoids that deadlock."""
    import time
    if not hasattr(doc, "tryBarrierLock"):  # very old builds
        doc.waitForDone()
        return
    end = time.monotonic() + timeout
    while not doc.tryBarrierLock():
        if time.monotonic() > end:
            raise BridgeError("Krita is still busy with the previous operation; try again in a moment")
        QCoreApplication.processEvents(_NO_INPUT, 20)
        time.sleep(0.003)
    doc.unlock()


def pump_for(seconds):
    """Keep Krita's event loop (incl. timers) running for a moment, without user input."""
    import time
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QCoreApplication.processEvents(_NO_INPUT, 20)
        time.sleep(0.01)


def _settle(doc, refresh):
    refresh(doc)
    flush_events()


def _krita(action, steps, doc, refresh):
    for _ in range(steps):
        a = Krita.instance().action(action)
        if a is None or not a.isEnabled():
            raise BridgeError("Krita has nothing left to %s" % action.split("_")[1])
        a.trigger()
        _settle(doc, refresh)  # a second trigger while Krita is still undoing is silently dropped


def _latest(stack, doc):
    for i in range(len(stack) - 1, -1, -1):
        if stack[i].doc == doc:
            return i
    return None


def _flip(op, action, doc, refresh):
    """Move `op` across: capture the current states (they are the way back), let Krita step its own
    recorded commands first, then put back whatever Krita does not cover."""
    opposite = [b.capture() for b in op.backups]
    refresh(doc)
    _krita(action, op.krita_steps, doc, refresh)
    refresh(doc)
    for b in (reversed(op.backups) if action == "edit_undo" else op.backups):
        b.apply()
    refresh(doc)
    op.backups = opposite
    op.after = krita_state()


def undo_one(doc, refresh):
    i = _latest(_undo, doc)
    if i is None:
        return None
    op = _undo[i]
    now = krita_state()
    if op.after is not None and now is not None and _tail(now) != _tail(op.after):
        raise BridgeError("The document was changed in Krita since the last AI edit (%s); undoing it would "
                          "overwrite those changes. Use native=true to step through Krita's own undo."
                          % op.method)
    del _undo[i]
    _flip(op, "edit_undo", doc, refresh)
    _redo.append(op)
    return op.describe()


def redo_one(doc, refresh):
    i = _latest(_redo, doc)
    if i is None:
        return None
    op = _redo[i]
    del _redo[i]
    _flip(op, "edit_redo", doc, refresh)
    _undo.append(op)
    return op.describe()


def listing(doc, limit):
    mine = [o.describe() for o in _undo if o.doc == doc]
    redo = [o.describe() for o in _redo if o.doc == doc]
    return {"ai_edits": mine[-limit:], "redoable": redo[-limit:], "undo_count": len(mine), "redo_count": len(redo)}


def forget(doc):
    """Drop records of a closed document."""
    _undo[:] = [o for o in _undo if o.doc != doc]
    _redo[:] = [o for o in _redo if o.doc != doc]
