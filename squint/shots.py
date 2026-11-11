"""Renders the window to a PNG, for the README. Used only when SQUINT_SCREENSHOT is set.

Also writes <name>.json with where the preview sits, so docs tooling can blur it.
"""
import json

import gi

gi.require_version('Gtk', '4.0')
from gi.repository import GLib, Graphene, Gtk  # noqa: E402


def capture(win, path):
    width, height = win.get_width(), win.get_height()
    paintable = Gtk.WidgetPaintable.new(win)
    snap = Gtk.Snapshot()
    paintable.snapshot(snap, width, height)
    node = snap.to_node()
    renderer = win.get_native().get_renderer()
    texture = renderer.render_texture(node, Graphene.Rect().init(0, 0, width, height))
    texture.save_to_png(path)
    ok, rect = win.preview_frame.compute_bounds(win)
    if ok:
        with open(path.rsplit('.', 1)[0] + '.json', 'w', encoding='utf-8') as f:
            json.dump({'preview': [rect.get_x(), rect.get_y(), rect.get_width(), rect.get_height()],
                       'size': [width, height]}, f)


def capture_later(win, path, delay_ms=4000):
    def go():
        capture(win, path)
        if not GLib.getenv('SQUINT_SCREENSHOT_KEEP'):
            win.get_application().quit()
        return False
    GLib.timeout_add(delay_ms, go)
