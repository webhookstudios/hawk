import os
import sys
import time

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import APP_ID, __version__, restore  # noqa: E402
from . import v4l2 as V  # noqa: E402
from .guard import Guard  # noqa: E402
from .store import Store  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PLUG_DELAY_MS = 1500  # give a new camera time to start and get its permissions
OPEN_DELAY_MS = 2000  # after an app opens the camera, let it finish setting up before checking
REPEAT_S = 15.0       # the same fix twice within this long means the camera won't keep it


class SquintApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.store = Store()
        self.background = False
        self.known = set()
        self.plug_id = 0
        self.monitor = None
        self.guard = None
        self.open_ids = {}
        self.last_fix = {}
        self.held = False
        self.add_main_option('background', 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             'Run without a window: reapply presets when a camera is plugged in', None)
        self.add_main_option('list', 'l'.encode()[0], GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             'List cameras and their saved presets', None)
        self.add_main_option('apply', 'a'.encode()[0], GLib.OptionFlags.NONE, GLib.OptionArg.STRING,
                             'Apply a saved preset or position by name', 'NAME')
        self.add_main_option('camera', 'c'.encode()[0], GLib.OptionFlags.NONE, GLib.OptionArg.STRING,
                             'Camera for --apply: a /dev/video path or part of its name', 'CAMERA')
        self.connect('handle-local-options', self._on_options)

    # ---- command line ---------------------------------------------------------------------

    def _on_options(self, _app, options):
        if options.contains('list'):
            return self._cli_list()
        if options.contains('apply'):
            camera = options.lookup_value('camera')
            return self._cli_apply(options.lookup_value('apply').get_string(),
                                   camera.get_string() if camera else '')
        if options.contains('background'):
            self.register(None)
            if self.get_is_remote():
                return 0  # already running
            self.background = True
        return -1

    def _cli_list(self):
        cams = V.list_cameras()
        if not cams:
            print('No cameras found.')
        for info in cams:
            print(f'{info.path}  {info.name}  [{info.usb_id}]')
            for kind, title in (('presets', 'Presets'), ('positions', 'Positions')):
                names = list(self.store.presets(info.key, kind))
                last = self.store.last(info.key, kind)
                if names:
                    shown = ', '.join(n + (' (last)' if n == last else '') for n in names)
                    print(f'    {title}: {shown}')
        return 0

    def _cli_apply(self, name, which):
        cams = V.list_cameras()
        if which:
            cams = [c for c in cams if c.path == which or which.lower() in c.name.lower()]
        for info in cams:
            for kind in ('presets', 'positions'):
                if name in self.store.presets(info.key, kind):
                    restore.apply_named(self.store, info, kind, name)
                    print(f'{info.name}: applied “{name}”')
                    return 0
        print(f'No preset or position called “{name}” for the connected camera.', file=sys.stderr)
        return 1

    # ---- life cycle -----------------------------------------------------------------------

    def do_startup(self):
        Adw.Application.do_startup(self)
        provider = Gtk.CssProvider()
        provider.load_from_path(os.path.join(HERE, 'style.css'))
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        for name, cb in (('about', self._about), ('quit', lambda *_: self.quit())):
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', cb)
            self.add_action(action)
        self.set_accels_for_action('app.quit', ['<Control>q'])
        self.set_accels_for_action('win.toggle-preview', ['<Control>p'])
        self.set_accels_for_action('win.save-preset', ['<Control>s'])

        # Watch /dev for cameras coming and going.
        self.monitor = Gio.File.new_for_path('/dev').monitor_directory(Gio.FileMonitorFlags.NONE, None)
        self.monitor.connect('changed', self._on_dev_changed)
        cams = V.list_cameras()
        self.known = {c.bus_info for c in cams}
        if self.store['reapply']:
            for info in cams:
                self._restore(info)
        # Watch each camera for other apps opening it.
        self.guard = Guard(self._on_camera_opened)
        self.guard.watch([c.path for c in cams])
        if self.background:
            self._stay()

    def _stay(self):
        """Keeps running without a window, so your settings hold while other apps use the camera."""
        if not self.held:
            self.hold()
            self.held = True

    def _on_window_closed(self, *_):
        if self.store['reapply']:
            self._stay()
        return False

    # ---- other apps opening the camera ----------------------------------------------------

    def _on_camera_opened(self, path):
        if not self.store['reapply']:
            return
        if path in self.open_ids:
            GLib.source_remove(self.open_ids[path])
        self.open_ids[path] = GLib.timeout_add(OPEN_DELAY_MS, self._check_after_open, path)

    def _check_after_open(self, path):
        self.open_ids.pop(path, None)
        win = self.props.active_window
        if win and getattr(win, 'recently_touched', lambda: False)():
            return False  # you are changing things in Squint right now
        info = next((c for c in V.list_cameras() if c.path == path), None)
        if not info:
            return False
        self.store.load()
        try:
            with V.Camera(path) as cam:
                cam.query()
                diff = restore.differences(cam, restore.wanted(self.store, info))
                if not diff:
                    return False
                # Squint's own check opens the camera too. If the last fix was this same one,
                # the camera isn't keeping it; don't loop.
                fix = tuple(sorted(diff.items()))
                last, when = self.last_fix.get(path, (None, 0))
                if fix == last and time.monotonic() - when < REPEAT_S:
                    return False
                self.last_fix[path] = (fix, time.monotonic())
                restored = restore.reapply_last(self.store, info, cam)
        except OSError:
            return False
        if restored and win:
            win.camera_plugged(info, restored)
        return False

    def do_activate(self):
        if self.background:
            self.background = False  # only the first, windowless start
            return
        win = self.props.active_window
        if not win:
            from .window import SquintWindow
            win = SquintWindow(self, self.store)
            win.connect('close-request', self._on_window_closed)
            shot = os.environ.get('SQUINT_SCREENSHOT')
            if shot:
                from .shots import capture_later
                capture_later(win, shot)
        win.present()

    def _on_dev_changed(self, _monitor, file, _other, event):
        if not file.get_basename().startswith('video'):
            return
        if event not in (Gio.FileMonitorEvent.CREATED, Gio.FileMonitorEvent.DELETED,
                         Gio.FileMonitorEvent.ATTRIBUTE_CHANGED):
            return
        if self.plug_id:
            GLib.source_remove(self.plug_id)
        self.plug_id = GLib.timeout_add(PLUG_DELAY_MS, self._after_plug)

    def _after_plug(self):
        self.plug_id = 0
        cams = V.list_cameras()
        new = [c for c in cams if c.bus_info not in self.known]
        self.known = {c.bus_info for c in cams}
        win = self.props.active_window
        if self.guard:
            self.guard.watch([c.path for c in cams])
        if win:
            win.rescan()
        for info in new:
            restored = self._restore(info) if self.store['reapply'] else []
            if win:
                win.camera_plugged(info, restored)
        return False

    def _restore(self, info):
        self.store.load()  # another Squint (the command line) may have changed it
        try:
            return restore.reapply_last(self.store, info)
        except OSError as e:
            print(f'squint: could not restore {info.name}: {e}', file=sys.stderr)
            return []

    def _about(self, *_args):
        Adw.AboutDialog(application_name='Squint', application_icon=APP_ID, version=__version__,
                        developer_name='webhookstudios', license_type=Gtk.License.GPL_2_0,
                        website='https://github.com/webhookstudios/squint',
                        comments='Webcam settings and presets for Ubuntu.').present(self.props.active_window)


def main(argv):
    GLib.set_prgname(APP_ID)
    return SquintApp().run(argv)


if __name__ == '__main__':
    sys.exit(main(sys.argv))
