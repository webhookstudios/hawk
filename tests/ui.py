#!/usr/bin/python3
"""Drives the real window against the real camera: sliders, presets, positions.

    XDG_CONFIG_HOME=$(mktemp -d) python3 tests/ui.py [screenshot-dir]

Uses a throwaway config folder; puts the camera's settings back when done.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gi.repository import Gio, GLib  # noqa: E402

from squint import v4l2 as V  # noqa: E402
from squint.app import SquintApp  # noqa: E402

SHOTS = sys.argv[1] if len(sys.argv) > 1 else None
failures = []


def check(what, ok):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


def row_for(win, cid):
    return next(r for r in win.rows if r.ctrl.id == cid)


def read(cid):
    with V.Camera(win.info.path) as cam:
        return cam.get(cid)


def save_named(kind, name):
    win._ask_name(kind)
    dialog = win.get_visible_dialog()
    dialog.get_extra_child().set_text(name)
    dialog.emit('response', 'ok')
    dialog.force_close()


def shot(name):
    if SHOTS:
        from squint.shots import capture
        capture(win, os.path.join(SHOTS, name + '.png'))


steps = []


def step(fn):
    steps.append(fn)
    return fn


@step
def sliders():
    row_for(win, V.BRIGHTNESS).adj.set_value(70)
    return 300


@step
def sliders_check():
    check('brightness slider writes to the camera', read(V.BRIGHTNESS) == 70)
    save_named('presets', 'Bright')
    check('preset saved', 'Bright' in win.store.presets(win.info.key, 'presets'))
    # A second preset: dim, manual white balance
    row_for(win, V.BRIGHTNESS).adj.set_value(35)
    if V.AUTO_WHITE_BALANCE in win.cam.controls:
        row_for(win, V.AUTO_WHITE_BALANCE).set_selected(1)  # Manual
    return 300


@step
def manual_wb():
    if V.WHITE_BALANCE_TEMPERATURE in win.cam.controls:
        temp = row_for(win, V.WHITE_BALANCE_TEMPERATURE)
        check('temperature unlocks when auto white balance is off', temp.get_sensitive())
        temp.adj.set_value(4200)
    return 300


@step
def wb_auto_again():
    wb = row_for(win, V.AUTO_WHITE_BALANCE) if V.AUTO_WHITE_BALANCE in win.cam.controls else None
    if wb:
        check('white balance offers Automatic and Manual',
              [wb.get_model().get_string(i) for i in range(2)] == ['Automatic', 'Manual'])
        check('manual white balance reaches the camera', read(V.AUTO_WHITE_BALANCE) == 0)
        wb.set_selected(0)
    return 300


@step
def wb_manual_again():
    if V.AUTO_WHITE_BALANCE in win.cam.controls:
        check('automatic white balance reaches the camera', read(V.AUTO_WHITE_BALANCE) == 1)
        check('temperature locks in automatic',
              not row_for(win, V.WHITE_BALANCE_TEMPERATURE).get_sensitive())
        row_for(win, V.AUTO_WHITE_BALANCE).set_selected(1)
    return 300


@step
def save_dim():
    if V.WHITE_BALANCE_TEMPERATURE in win.cam.controls:
        row_for(win, V.WHITE_BALANCE_TEMPERATURE).adj.set_value(4200)
    return 300


@step
def save_dim2():
    if V.WHITE_BALANCE_TEMPERATURE in win.cam.controls:
        check('temperature written', read(V.WHITE_BALANCE_TEMPERATURE) == 4200)
    save_named('presets', 'Warm and dim')
    win.activate_action('win.apply-preset', GLib.Variant.new_string('Bright'))
    return 400


@step
def recall_bright():
    check('recalling a preset restores brightness', read(V.BRIGHTNESS) == 70)
    check('recalling a preset restores auto white balance', read(V.AUTO_WHITE_BALANCE) == 1)
    check('slider follows the recalled preset', row_for(win, V.BRIGHTNESS).adj.get_value() == 70)
    win.activate_action('win.apply-preset', GLib.Variant.new_string('Warm and dim'))
    return 400


@step
def recall_dim():
    check('recalling the other preset', read(V.BRIGHTNESS) == 35)
    check('manual white balance comes back', read(V.AUTO_WHITE_BALANCE) == 0)
    check('temperature comes back', read(V.WHITE_BALANCE_TEMPERATURE) == 4200)
    if V.ZOOM_ABSOLUTE not in win.cam.controls:
        return 10
    row_for(win, V.ZOOM_ABSOLUTE).adj.set_value(30)
    row_for(win, V.PAN_ABSOLUTE).adj.set_value(-36000)
    return 300


@step
def positions():
    if V.ZOOM_ABSOLUTE in win.cam.controls:
        save_named('positions', 'Desk')
        row_for(win, V.ZOOM_ABSOLUTE).adj.set_value(80)
        row_for(win, V.PAN_ABSOLUTE).adj.set_value(72000)
        save_named('positions', 'Whiteboard')
        win._start_hold((0, 0, -1))  # hold "zoom out" for a moment
    return 400


@step
def position_recall():
    win._stop_hold()
    if V.ZOOM_ABSOLUTE in win.cam.controls:
        check('holding zoom out lowers zoom', read(V.ZOOM_ABSOLUTE) < 80)
        win.activate_action('win.apply-position', GLib.Variant.new_string('Desk'))
    return 400


@step
def position_check():
    if V.ZOOM_ABSOLUTE in win.cam.controls:
        check('position preset restores zoom', read(V.ZOOM_ABSOLUTE) == 30)
        check('position preset restores pan', read(V.PAN_ABSOLUTE) == -36000)
        check('position preset did not touch brightness', read(V.BRIGHTNESS) == 35)
    win.activate_action('win.apply-preset', GLib.Variant.new_string('Bright'))
    return 1500


@step
def screenshot_main():
    shot('window')
    win._ask_name('presets')
    return 600


@step
def screenshot_dialog():
    shot('save-preset')
    win.get_visible_dialog().force_close()
    win.preview_button.set_active(False)
    return 500


@step
def screenshot_off():
    shot('preview-off')
    return 10


@step
def finish():
    with V.Camera(win.info.path) as cam:
        cam.query()
        cam.apply(ORIGINAL)
    app.quit()
    return None


def run_steps():
    def go(i):
        if i >= len(steps):
            return False
        try:
            delay = steps[i]()
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            failures.append(f'{steps[i].__name__}: {e}')
            app.quit()
            return False
        if delay is not None:
            GLib.timeout_add(delay, go, i + 1)
        return False
    GLib.timeout_add(2500, go, 0)


app = SquintApp()
app.set_flags(app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)  # runs beside an open Squint
win = None
ORIGINAL = {}


def on_activate(*_):
    global win, ORIGINAL
    win = app.props.active_window
    ORIGINAL = win.cam.snapshot()
    run_steps()


app.connect_after('activate', on_activate)
app.run([sys.argv[0]])
print(f'{len(failures)} failure(s)')
sys.exit(1 if failures else 0)
