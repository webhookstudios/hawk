"""Rows for each kind of camera control, and the names and groups they are shown under."""
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk  # noqa: E402

from . import v4l2 as V  # noqa: E402

# (group title, [control ids in order])
GROUPS = [
    ('Focus', [V.FOCUS_AUTO, V.FOCUS_ABSOLUTE]),
    ('Exposure', [V.EXPOSURE_AUTO, V.EXPOSURE_ABSOLUTE, V.IRIS_ABSOLUTE, V.GAIN, V.EXPOSURE_AUTO_PRIORITY,
                  V.POWER_LINE_FREQUENCY]),
    ('White balance', [V.AUTO_WHITE_BALANCE, V.WHITE_BALANCE_TEMPERATURE, V.RED_BALANCE,
                       V.BLUE_BALANCE]),
    ('Image', [V.BRIGHTNESS, V.CONTRAST, V.SATURATION, V.HUE_AUTO, V.HUE, V.SHARPNESS, V.GAMMA,
               V.BACKLIGHT_COMPENSATION]),
]

POSITION_GROUP = [V.PAN_ABSOLUTE, V.TILT_ABSOLUTE, V.ZOOM_ABSOLUTE]

LABELS = {
    V.FOCUS_AUTO: 'Focus mode',
    V.FOCUS_ABSOLUTE: 'Focus',
    V.EXPOSURE_AUTO: 'Exposure',
    V.EXPOSURE_ABSOLUTE: 'Shutter time',
    V.EXPOSURE_AUTO_PRIORITY: 'Lower frame rate in low light',
    V.GAIN: 'Gain',
    V.POWER_LINE_FREQUENCY: 'Anti-flicker',
    V.AUTO_WHITE_BALANCE: 'White balance',
    V.WHITE_BALANCE_TEMPERATURE: 'Temperature',
    V.RED_BALANCE: 'Red',
    V.BLUE_BALANCE: 'Blue',
    V.BRIGHTNESS: 'Brightness',
    V.CONTRAST: 'Contrast',
    V.SATURATION: 'Saturation',
    V.HUE_AUTO: 'Automatic hue',
    V.HUE: 'Hue',
    V.SHARPNESS: 'Sharpness',
    V.GAMMA: 'Gamma',
    V.BACKLIGHT_COMPENSATION: 'Backlight compensation',
    V.PAN_ABSOLUTE: 'Pan',
    V.TILT_ABSOLUTE: 'Tilt',
    V.ZOOM_ABSOLUTE: 'Zoom',
    V.PRIVACY: 'Privacy shutter',
    V.IRIS_ABSOLUTE: 'Iris',
}

SUBTITLES = {
    V.AUTO_WHITE_BALANCE: 'Manual unlocks the temperature',
    V.FOCUS_AUTO: 'Manual unlocks the focus slider',
    V.POWER_LINE_FREQUENCY: 'Match your mains power to stop lights from flickering',
    V.BACKLIGHT_COMPENSATION: 'Brightens faces against a bright window',
    V.EXPOSURE_AUTO_PRIORITY: 'Brighter picture, but motion may look choppy',
}

MENU_LABELS = {
    V.EXPOSURE_AUTO: {0: 'Automatic', 1: 'Manual', 2: 'Shutter priority', 3: 'Aperture priority'},
    V.POWER_LINE_FREQUENCY: {0: 'Off', 1: '50 Hz', 2: '60 Hz', 3: 'Automatic'},
}


# On/off automatic modes shown as an Automatic / Manual choice instead of a switch.
MODE_IDS = {V.AUTO_WHITE_BALANCE, V.FOCUS_AUTO}


def fmt(ctrl, value):
    if value is None:
        return '–'
    if ctrl.id in (V.PAN_ABSOLUTE, V.TILT_ABSOLUTE):
        return f'{value / 3600:+.0f}°'
    if ctrl.id == V.EXPOSURE_ABSOLUTE:  # units of 100 µs
        ms = value / 10
        return f'{ms:.1f} ms' if ms < 10 else f'{ms:.0f} ms'
    if ctrl.id == V.WHITE_BALANCE_TEMPERATURE:
        return f'{value} K'
    return str(value)


def label(ctrl):
    return LABELS.get(ctrl.id) or ctrl.name


class _Row:
    """Shared plumbing: a guard so updates from the camera don't write back."""

    def bind(self, ctrl, write):
        self.ctrl = ctrl
        self.write = write
        self.guard = False

    def refresh(self):
        self.guard = True
        try:
            self.show_value(self.ctrl.value)
        finally:
            self.guard = False
        self.set_sensitive(self.ctrl.writable and not self.ctrl.inactive)
        tip = 'Turn off the automatic mode to change this' if self.ctrl.inactive else None
        self.set_tooltip_text(tip)


class SliderRow(Adw.ActionRow, _Row):
    def __init__(self, ctrl, write):
        super().__init__(title=label(ctrl), subtitle=SUBTITLES.get(ctrl.id, ''))
        self.bind(ctrl, write)
        self.add_css_class('slider-row')
        self.adj = Gtk.Adjustment(lower=ctrl.minimum, upper=ctrl.maximum,
                                  step_increment=ctrl.step, page_increment=ctrl.step * 10)
        self.scale = Gtk.Scale(adjustment=self.adj, draw_value=False, hexpand=True,
                               valign=Gtk.Align.CENTER, width_request=170)
        self.scale.set_round_digits(0)
        if ctrl.minimum < ctrl.default < ctrl.maximum:
            self.scale.add_mark(ctrl.default, Gtk.PositionType.BOTTOM, None)
        self.value_label = Gtk.Label(width_chars=7, xalign=1, valign=Gtk.Align.CENTER)
        self.value_label.add_css_class('numeric')
        self.value_label.add_css_class('dim-label')
        self.reset = Gtk.Button(icon_name='edit-undo-symbolic', valign=Gtk.Align.CENTER,
                                tooltip_text='Back to default')
        self.reset.add_css_class('flat')
        self.reset.add_css_class('circular')
        self.reset.connect('clicked', lambda *_: self.adj.set_value(ctrl.default))
        for w in (self.scale, self.value_label, self.reset):
            self.add_suffix(w)
        self.adj.connect('value-changed', self._changed)

    def _changed(self, adj):
        value = self.ctrl.clamp(adj.get_value())
        self.value_label.set_label(fmt(self.ctrl, value))
        self.reset.set_opacity(0 if value == self.ctrl.default else 1)
        if not self.guard:
            self.write(self.ctrl.id, value)

    def show_value(self, value):
        if value is not None and int(self.adj.get_value()) != value:
            self.adj.set_value(value)
        self.value_label.set_label(fmt(self.ctrl, value))
        self.reset.set_opacity(0 if value == self.ctrl.default else 1)


class SwitchRow(Adw.ActionRow, _Row):
    # Adw.SwitchRow can't be subclassed, so this is an action row with a switch.
    def __init__(self, ctrl, write):
        super().__init__(title=label(ctrl), subtitle=SUBTITLES.get(ctrl.id, ''))
        self.bind(ctrl, write)
        self.switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.add_suffix(self.switch)
        self.set_activatable_widget(self.switch)
        self.switch.connect('notify::active', self._changed)

    def _changed(self, *_):
        if not self.guard:
            self.write(self.ctrl.id, 1 if self.switch.get_active() else 0)

    def show_value(self, value):
        self.switch.set_active(bool(value))


class ComboRow(Adw.ComboRow, _Row):
    def __init__(self, ctrl, write):
        super().__init__(title=label(ctrl), subtitle=SUBTITLES.get(ctrl.id, ''))
        self.bind(ctrl, write)
        if ctrl.type == V.TYPE_BOOLEAN:
            items = [(1, 'Automatic'), (0, 'Manual')]
        else:
            names = MENU_LABELS.get(ctrl.id, {})
            items = [(i, names.get(i, n)) for i, n in ctrl.menu]
        self.values = [i for i, _ in items]
        self.set_model(Gtk.StringList.new([n for _, n in items]))
        self.connect('notify::selected', self._changed)

    def _changed(self, *_):
        i = self.get_selected()
        if not self.guard and 0 <= i < len(self.values):
            self.write(self.ctrl.id, self.values[i])

    def show_value(self, value):
        if self.ctrl.type == V.TYPE_BOOLEAN and value is not None:
            value = 1 if value else 0
        if value in self.values:
            self.set_selected(self.values.index(value))


class ButtonRow(Adw.ActionRow, _Row):
    def __init__(self, ctrl, write):
        super().__init__(title=label(ctrl), activatable=True)
        self.bind(ctrl, write)
        self.add_suffix(Gtk.Image(icon_name='media-playback-start-symbolic'))
        self.connect('activated', lambda *_: write(ctrl.id, 1))

    def show_value(self, value):
        pass


def make_row(ctrl, write):
    if ctrl.type == V.TYPE_BOOLEAN and ctrl.id in MODE_IDS:
        return ComboRow(ctrl, write)
    if ctrl.type == V.TYPE_BOOLEAN:
        return SwitchRow(ctrl, write)
    if ctrl.type == V.TYPE_MENU and ctrl.menu:
        return ComboRow(ctrl, write)
    if ctrl.type == V.TYPE_BUTTON:
        return ButtonRow(ctrl, write)
    if ctrl.type == V.TYPE_INTEGER and ctrl.maximum > ctrl.minimum:
        return SliderRow(ctrl, write)
    return None
