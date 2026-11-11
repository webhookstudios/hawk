"""The main window: live preview on the left, the camera's controls on the right."""
import time

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import restore, vendor  # noqa: E402
from . import v4l2 as V  # noqa: E402
from .controls import GROUPS, POSITION_GROUP, make_row  # noqa: E402
from .preview import Preview  # noqa: E402

POLL_MS = 1500     # how often values are re-read, for things the camera changes itself
TICK_MS = 40       # how often a held arrow button moves the camera
HOLD_STEPS = 100   # a held arrow crosses the whole range in this many ticks
OBSBOT_VENDOR = '3564:'  # Remo Tech, the maker of OBSBOT cameras
OBSBOT_NOTE = ('OBSBOT cameras ignore pan, tilt and zoom while AI framing or tracking is on. '
               'Turn it off with the camera’s gesture or button first. Hawk can’t switch it on '
               'this model, because OBSBOT keeps that part of the camera private.')
OBSBOT_SWITCHABLE_NOTE = ('Pan, tilt and zoom only take effect while AI tracking is set to Fixed.')

# Each axis, by how the camera can move it: an absolute position, a relative step per press,
# or a speed (move until told to stop). Cameras differ; the first one the camera has is used.
AXES = [
    (V.PAN_ABSOLUTE, V.PAN_RELATIVE, V.PAN_SPEED),
    (V.TILT_ABSOLUTE, V.TILT_RELATIVE, V.TILT_SPEED),
    (V.ZOOM_ABSOLUTE, V.ZOOM_RELATIVE, V.ZOOM_CONTINUOUS),
]
REMEMBER_MS = 600  # settle time before the current settings are saved as how you left them


class HawkWindow(Adw.ApplicationWindow):
    def __init__(self, app, store):
        super().__init__(application=app, title='Hawk', default_width=1180, default_height=760)
        self.set_size_request(760, 520)
        self.app = app
        self.store = store
        self.cameras = []
        self.info = None
        self.cam = None
        self.rows = []
        self.pending = {}
        self.flush_id = 0
        self.touched = 0.0
        self.hold_id = 0
        self.remember_id = 0
        self.hold_speeds = []
        self.preview = Preview(self._on_preview_state)

        self._actions()
        self._build()
        self.poll_id = GLib.timeout_add(POLL_MS, self._poll)
        self.connect('close-request', self._on_close)
        self.connect('notify::is-active', self._on_active)
        self.rescan()

    # ---- layout ---------------------------------------------------------------------------

    def _build(self):
        header = Adw.HeaderBar()
        self.camera_list = Gtk.StringList()
        self.camera_pick = Gtk.DropDown(model=self.camera_list, tooltip_text='Camera')
        self.camera_pick.add_css_class('flat')
        self.camera_pick_id = self.camera_pick.connect('notify::selected', self._on_pick)
        header.set_title_widget(self.camera_pick)

        self.preview_button = Gtk.ToggleButton(icon_name='camera-video-symbolic',
                                               tooltip_text='Live preview (Ctrl+P)',
                                               active=self.store['preview'])
        self.preview_button.connect('toggled', self._on_preview_toggled)
        header.pack_start(self.preview_button)

        menu = Gio.Menu()
        section = Gio.Menu()
        section.append('Reset the camera', 'win.reset')
        section.append('Keep my settings after closing', 'win.reapply')
        menu.append_section(None, section)
        about = Gio.Menu()
        about.append('About Hawk', 'app.about')
        menu.append_section(None, about)
        header.pack_end(Gtk.MenuButton(icon_name='open-menu-symbolic', menu_model=menu,
                                       tooltip_text='Main menu', primary=True))

        # Left: preview and position presets
        self.picture = Gtk.Picture(paintable=self.preview.paintable,
                                   content_fit=Gtk.ContentFit.CONTAIN, hexpand=True, vexpand=True)
        self.picture.add_css_class('preview')
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect('scroll', self._on_preview_scroll)
        self.picture.add_controller(scroll)

        self.preview_status = Adw.StatusPage(icon_name='camera-video-symbolic')
        self.preview_status.add_css_class('compact')
        self.preview_status.add_css_class('preview-status')
        self.retry = Gtk.Button(label='Show preview', halign=Gtk.Align.CENTER)
        self.retry.add_css_class('pill')
        self.retry.connect('clicked', lambda *_: self._start_preview(force=True))
        self.preview_status.set_child(self.retry)

        self.preview_frame = Gtk.Overlay(child=self.picture)
        self.preview_frame.add_overlay(self.preview_status)
        self.preview_frame.add_css_class('preview-frame')
        self.preview_frame.set_overflow(Gtk.Overflow.HIDDEN)

        self.positions_box = Gtk.Box(spacing=6)
        positions_scroll = Gtk.ScrolledWindow(child=self.positions_box,
                                              vscrollbar_policy=Gtk.PolicyType.NEVER)
        positions_scroll.set_propagate_natural_height(True)
        self.positions_title = Gtk.Label(label='Positions', xalign=0)
        self.positions_title.add_css_class('heading')
        self.positions_area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.positions_area.append(self.positions_title)
        self.positions_area.append(positions_scroll)

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, hexpand=True)
        left.add_css_class('left-pane')
        left.append(self.preview_frame)
        left.append(self.positions_area)

        # Right: controls
        self.page = Adw.PreferencesPage()
        self.page.add_css_class('controls')
        self.page.set_size_request(460, -1)
        self.page.set_hexpand(False)

        content = Gtk.Box()
        content.append(left)
        content.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        content.append(self.page)

        self.empty = Adw.StatusPage(icon_name='camera-disabled-symbolic', title='No camera found',
                                    description='Plug in a webcam. Hawk finds it right away.')
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(content, 'camera')
        self.stack.add_named(self.empty, 'empty')

        self.toasts = Adw.ToastOverlay(child=self.stack)
        view = Adw.ToolbarView(content=self.toasts)
        view.add_top_bar(header)
        self.set_content(view)

    def _actions(self):
        def add(name, cb, param=None):
            action = Gio.SimpleAction.new(name, GLib.VariantType.new(param) if param else None)
            action.connect('activate', cb)
            self.add_action(action)
        add('reset', self._on_reset)
        add('save-preset', lambda *_: self._ask_name('presets'))
        add('save-position', lambda *_: self._ask_name('positions'))
        add('apply-preset', lambda _a, p: self.apply_preset('presets', p.get_string()), 's')
        add('apply-position', lambda _a, p: self.apply_preset('positions', p.get_string()), 's')
        for kind, word in (('presets', 'preset'), ('positions', 'position')):
            add(f'update-{word}', lambda _a, p, k=kind: self._update(k, p.get_string()), 's')
            add(f'rename-{word}', lambda _a, p, k=kind: self._ask_name(k, p.get_string()), 's')
            add(f'delete-{word}', lambda _a, p, k=kind: self._delete(k, p.get_string()), 's')
        reapply = Gio.SimpleAction.new_stateful('reapply', None,
                                                GLib.Variant.new_boolean(self.store['reapply']))
        reapply.connect('change-state', self._on_reapply)
        self.add_action(reapply)
        preview = Gio.SimpleAction.new('toggle-preview', None)
        preview.connect('activate', lambda *_: self.preview_button.set_active(
            not self.preview_button.get_active()))
        self.add_action(preview)

    # ---- cameras --------------------------------------------------------------------------

    def rescan(self):
        """Re-reads the list of cameras, keeping the current one if it is still there."""
        cameras = V.list_cameras()
        current = self.info.key if self.info else self.store['camera']
        old_paths = [c.path for c in self.cameras]
        if [c.path for c in cameras] == old_paths and self.cam and self.cam.alive():
            return
        if self.cam and not self.cam.alive():
            self._close_camera()
        self.cameras = cameras
        self.camera_pick.handler_block(self.camera_pick_id)
        self.camera_list.splice(0, self.camera_list.get_n_items(), [self._camera_title(c) for c in cameras])
        index = next((i for i, c in enumerate(cameras) if c.key == current), 0)
        if cameras:
            self.camera_pick.set_selected(index)
        self.camera_pick.handler_unblock(self.camera_pick_id)
        self.camera_pick.set_visible(bool(cameras))
        if not cameras:
            self.set_title('Hawk')
            self._close_camera()
            self.stack.set_visible_child_name('empty')
            return
        self.stack.set_visible_child_name('camera')
        if not self.info or self.info.path != cameras[index].path:
            self.open_camera(cameras[index])

    def _camera_title(self, info):
        same = [c for c in self.cameras if c.name == info.name]
        return info.name if len(same) < 2 else f'{info.name} ({info.path})'

    def _on_pick(self, *_):
        i = self.camera_pick.get_selected()
        if 0 <= i < len(self.cameras):
            self.open_camera(self.cameras[i])

    def _close_camera(self):
        self.preview.stop(notify=False)
        self._stop_hold()
        if self.cam:
            self.cam.close()
        self.cam = None
        self.info = None

    def open_camera(self, info):
        self._close_camera()
        try:
            self.cam = V.Camera(info.path)
            self.cam.query()
        except OSError as e:
            self.cam = None
            self.toast(f'Could not open {info.name}: {e.strerror}')
            return
        self.info = info
        self.store['camera'] = info.key
        self.store.camera(info.key, info.name)
        self.set_title(f'{info.name} – Hawk')
        self._build_controls()
        self._build_positions()
        self._start_preview()

    def camera_plugged(self, info, restored):
        """Called by the app when a camera appears and its presets were put back."""
        if self.info and self.info.key == info.key and self.cam:
            self.cam.refresh()
            self._refresh_rows()
        if restored:
            self.toast(f'{info.name}: put back {" and ".join(restored)}')

    def recently_touched(self, seconds=3.0):
        return bool(self.pending) or time.monotonic() - self.touched < seconds

    # ---- controls -------------------------------------------------------------------------

    def _clear_page(self):
        for group in getattr(self, '_groups', []):
            self.page.remove(group)
        self._groups = []

    def _add_group(self, group):
        self.page.add(group)
        self._groups.append(group)

    def _build_controls(self):
        self._clear_page()
        self.rows = []
        controls = self.cam.controls

        # Presets
        self.presets_group = Adw.PreferencesGroup(title='Presets')
        save = Gtk.Button(icon_name='list-add-symbolic', valign=Gtk.Align.CENTER,
                          tooltip_text='Save the current settings as a preset',
                          action_name='win.save-preset')
        save.add_css_class('flat')
        self.presets_group.set_header_suffix(save)
        self._add_group(self.presets_group)
        self.preset_rows = []
        self._fill_presets()

        # Position
        pos = [controls[c] for c in POSITION_GROUP if c in controls]
        self.extra = vendor.detect(self.cam)
        movable = any(self._axis(i) for i in range(3))
        if pos or movable or self.extra:
            group = Adw.PreferencesGroup(title='Position')
            if self.extra:
                group.set_description(OBSBOT_SWITCHABLE_NOTE)
                group.add(self._build_tracking())
            elif self.info.usb_id.startswith(OBSBOT_VENDOR):
                group.set_description(OBSBOT_NOTE)
            if movable:
                group.add(self._build_pad())
            for ctrl in pos:
                self._add_row(group, ctrl)
            self._add_group(group)

        used = set(POSITION_GROUP) | V.MOTION_IDS
        for title, ids in GROUPS:
            present = [controls[c] for c in ids if c in controls]
            used.update(ids)
            if not present:
                continue
            group = Adw.PreferencesGroup(title=title)
            for ctrl in present:
                self._add_row(group, ctrl)
            self._add_group(group)

        others = [c for c in controls.values() if c.id not in used]
        if others:
            group = Adw.PreferencesGroup(title='Other')
            for ctrl in others:
                self._add_row(group, ctrl)
            self._add_group(group)

        if not self.rows:
            group = Adw.PreferencesGroup(
                title='No adjustable settings',
                description='This camera does not report any standard controls.')
            self._add_group(group)
        self._refresh_rows()

    def _add_row(self, group, ctrl):
        row = make_row(ctrl, self.write)
        if row:
            group.add(row)
            self.rows.append(row)

    def _refresh_rows(self):
        for row in self.rows:
            row.refresh()
        self._mark_presets()

    def write(self, cid, value):
        """Queues a write; quick drags are coalesced into one write per frame or so."""
        self.touched = time.monotonic()
        self.pending[cid] = value
        if not self.flush_id:
            self.flush_id = GLib.timeout_add(20, self._flush)

    def _flush(self):
        self.flush_id = 0
        if not self.cam:
            self.pending.clear()
            return False
        pending, self.pending = self.pending, {}
        switched = False
        for cid, value in pending.items():
            ctrl = self.cam.controls.get(cid)
            try:
                ok = self.cam.try_set(cid, value)
            except OSError as e:
                self.toast(f'The camera stopped answering ({e.strerror}).')
                GLib.idle_add(self.rescan)
                return False
            if ctrl and ctrl.type in (V.TYPE_BOOLEAN, V.TYPE_MENU):
                switched = True
            if not ok:
                switched = True  # re-read, so the row shows what the camera really has
        if switched:
            self.cam.refresh()
            self._refresh_rows()
        else:
            self._mark_presets()
        self._remember()
        return False

    def _remember(self):
        """Saves every setting as how you left the camera, a moment after you stop changing it."""
        if self.remember_id:
            GLib.source_remove(self.remember_id)
        self.remember_id = GLib.timeout_add(REMEMBER_MS, self._remember_now)

    def _remember_now(self):
        self.remember_id = 0
        if self.cam and self.info:
            self.store.save_current(self.info.key, self.cam.snapshot())
        return False

    def _poll(self):
        if not self.cam or not self.get_visible():
            return True
        if self.pending or self.hold_id or time.monotonic() - self.touched < 1.0:
            return True
        try:
            self.cam.refresh()
        except OSError:
            self.rescan()
            return True
        self._refresh_rows()
        return True

    def _on_reset(self, *_):
        if not self.cam:
            return
        self.cam.reset()
        self._refresh_rows()
        self._remember()
        self.toast('Camera settings are back to their defaults.')

    def _on_reapply(self, action, value):
        action.set_state(value)
        self.store['reapply'] = value.get_boolean()

    # ---- position pad ---------------------------------------------------------------------

    def _build_pad(self):
        controls = self.cam.controls
        grid = Gtk.Grid(row_spacing=6, column_spacing=6, halign=Gtk.Align.CENTER)
        grid.add_css_class('pad')

        def pad_button(icon, tip, move, col, row):
            b = Gtk.Button(icon_name=icon, tooltip_text=tip)
            b.add_css_class('circular')
            b.add_css_class('pad-button')
            press = Gtk.GestureClick(propagation_phase=Gtk.PropagationPhase.CAPTURE)
            press.connect('pressed', lambda *_: self._start_hold(move))
            for sig in ('released', 'stopped', 'cancel'):
                press.connect(sig, lambda *_: self._stop_hold())
            b.add_controller(press)
            grid.attach(b, col, row, 1, 1)
            return b

        has_pan, has_tilt, has_zoom = (bool(self._axis(i)) for i in range(3))
        if has_tilt:
            pad_button('go-up-symbolic', 'Tilt up', (0, 1, 0), 1, 0)
            pad_button('go-down-symbolic', 'Tilt down', (0, -1, 0), 1, 2)
        if has_pan:
            pad_button('go-previous-symbolic', 'Pan left', (-1, 0, 0), 0, 1)
            pad_button('go-next-symbolic', 'Pan right', (1, 0, 0), 2, 1)
        can_center = (V.PAN_ABSOLUTE in controls or V.TILT_ABSOLUTE in controls
                      or V.PAN_RESET in controls or V.TILT_RESET in controls)
        if (has_pan or has_tilt) and can_center:
            home = Gtk.Button(icon_name='find-location-symbolic', tooltip_text='Center')
            home.add_css_class('circular')
            home.add_css_class('pad-button')
            home.connect('clicked', self._on_home)
            grid.attach(home, 1, 1, 1, 1)
        if has_zoom:
            pad_button('zoom-in-symbolic', 'Zoom in', (0, 0, 1), 4, 0)
            pad_button('zoom-out-symbolic', 'Zoom out', (0, 0, -1), 4, 2)
            grid.attach(Gtk.Label(label='', width_request=10), 3, 0, 1, 1)

        row = Gtk.ListBoxRow(activatable=False, selectable=False, child=grid)
        row.add_css_class('pad-row')
        return row

    def _axis(self, i):
        """How axis i (pan, tilt, zoom) can be moved: ('abs'|'rel'|'speed', control) or None."""
        if not self.cam:
            return None
        for mode, cid in zip(('abs', 'rel', 'speed'), AXES[i]):
            ctrl = self.cam.controls.get(cid)
            if ctrl and ctrl.writable and ctrl.maximum > ctrl.minimum:
                return mode, ctrl
        return None

    def _on_home(self, *_):
        if not self.cam:
            return
        for cid, reset in ((V.PAN_ABSOLUTE, V.PAN_RESET), (V.TILT_ABSOLUTE, V.TILT_RESET)):
            ctrl = self.cam.controls.get(cid)
            if ctrl:
                self.write(cid, ctrl.default)
                ctrl.value = ctrl.default
            elif reset in self.cam.controls:
                self.write(reset, 1)
        self._refresh_rows()

    def _start_hold(self, move):
        self._stop_hold()
        self.hold_move = move
        self.hold_speeds = []
        for i, direction in enumerate(move):
            axis = self._axis(i) if direction else None
            if axis and axis[0] == 'speed':
                ctrl = axis[1]
                speed = max(1, int(max(abs(ctrl.minimum), abs(ctrl.maximum)) * 0.6))
                self.write(ctrl.id, direction * speed)
                self.hold_speeds.append(ctrl.id)
        self._hold_tick()
        self.hold_id = GLib.timeout_add(TICK_MS, self._hold_tick)

    def _stop_hold(self):
        if self.hold_id:
            GLib.source_remove(self.hold_id)
            self.hold_id = 0
        for cid in self.hold_speeds:
            self.write(cid, 0)
        self.hold_speeds = []

    def _hold_tick(self):
        if not self.cam:
            self.hold_id = 0
            return False
        for i, direction in enumerate(self.hold_move):
            axis = self._axis(i) if direction else None
            if not axis:
                continue
            mode, ctrl = axis
            if mode == 'abs':
                self.nudge(ctrl.id, direction)
            elif mode == 'rel':
                self.write(ctrl.id, direction * max(ctrl.step, (ctrl.maximum - ctrl.minimum) // 40))
        return True

    def _build_tracking(self):
        row = Adw.ComboRow(title='AI tracking',
                           subtitle='Fixed keeps the camera still, so pan, tilt and zoom work')
        options = [('', 'Camera decides')] + [(v, label) for v, label, _c in self.extra.TRACKING]
        row.set_model(Gtk.StringList.new([label for _v, label in options]))
        saved = self.store.vendor(self.info.key).get('tracking', '')
        values = [v for v, _l in options]
        row.set_selected(values.index(saved) if saved in values else 0)

        def changed(*_):
            value = values[row.get_selected()]
            self.store.set_vendor(self.info.key, 'tracking', value)
            if value and not self.extra.set_tracking(self.cam, value):
                self.toast('The camera did not accept that.')
        row.connect('notify::selected', changed)
        return row

    def nudge(self, cid, direction, steps=HOLD_STEPS):
        ctrl = self.cam.controls.get(cid) if self.cam else None
        if not ctrl or ctrl.value is None:
            return
        delta = max(ctrl.step, (ctrl.maximum - ctrl.minimum) / steps)
        value = ctrl.clamp(ctrl.value + direction * delta)
        if value != ctrl.value:
            ctrl.value = value
            self.write(cid, value)
            for row in self.rows:
                if row.ctrl.id == cid:
                    row.refresh()

    def _on_preview_scroll(self, _ctl, _dx, dy):
        if dy and V.ZOOM_ABSOLUTE in (self.cam.controls if self.cam else {}):
            self.nudge(V.ZOOM_ABSOLUTE, -1 if dy > 0 else 1, steps=40)
        return True

    # ---- presets --------------------------------------------------------------------------

    def _settings_ids(self):
        return {c for c in self.cam.controls if c not in V.POSITION_IDS}

    def _fill_presets(self):
        for row in self.preset_rows:
            self.presets_group.remove(row)
        self.preset_rows = []
        presets = self.store.presets(self.info.key, 'presets')
        if not presets:
            row = Adw.ActionRow(title='No presets yet',
                                subtitle='Set the picture up the way you like, then click + to save it')
            row.add_css_class('dim-label')
            self.presets_group.add(row)
            self.preset_rows.append(row)
            return
        for name in presets:
            row = Adw.ActionRow(title=GLib.markup_escape_text(name), activatable=True)
            row.set_action_name('win.apply-preset')
            row.set_action_target_value(GLib.Variant.new_string(name))
            row.check = Gtk.Image(icon_name='object-select-symbolic', tooltip_text='Last applied')
            row.check.add_css_class('accent')
            row.add_suffix(row.check)
            row.add_suffix(self._preset_menu('preset', name))
            row.preset = name
            self.presets_group.add(row)
            self.preset_rows.append(row)
        self._mark_presets()

    def _preset_menu(self, word, name):
        menu = Gio.Menu()
        target = GLib.Variant.new_string(name)
        for label, action in (('Update to current settings' if word == 'preset'
                               else 'Update to current position', 'update'),
                              ('Rename…', 'rename'), ('Delete', 'delete')):
            item = Gio.MenuItem.new(label, None)
            item.set_action_and_target_value(f'win.{action}-{word}', target)
            menu.append_item(item)
        button = Gtk.MenuButton(icon_name='view-more-symbolic', menu_model=menu,
                                valign=Gtk.Align.CENTER, tooltip_text='More')
        button.add_css_class('flat')
        return button

    def _mark_presets(self):
        if not self.info:
            return
        last = self.store.last(self.info.key, 'presets')
        presets = self.store.presets(self.info.key, 'presets')
        current = self.cam.snapshot(self._settings_ids()) if self.cam else {}
        for row in self.preset_rows:
            name = getattr(row, 'preset', None)
            if name is None:
                continue
            row.check.set_visible(name == last)
            if name == last:
                saved = presets.get(name, {})
                changed = any(current.get(k) != v for k, v in saved.items()
                              if k in current and not self.cam.controls[k].inactive)
                row.set_subtitle('Applied, with changes' if changed else 'Applied')
            else:
                row.set_subtitle('')
        last_pos = self.store.last(self.info.key, 'positions')
        for button in getattr(self, 'position_buttons', []):
            if button.preset == last_pos:
                button.add_css_class('suggested-action')
            else:
                button.remove_css_class('suggested-action')

    def _build_positions(self):
        for button in getattr(self, 'position_buttons', []):
            button.popover.unparent()
        child = self.positions_box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.positions_box.remove(child)
            child = nxt
        self.position_buttons = []
        has_position = any(c in self.cam.controls for c in POSITION_GROUP)
        self.positions_area.set_visible(has_position)
        if not has_position:
            return
        for name in self.store.presets(self.info.key, 'positions'):
            button = Gtk.Button(label=name, action_name='win.apply-position',
                                action_target=GLib.Variant.new_string(name),
                                tooltip_text='Click to go there. Right-click for more.')
            button.add_css_class('pill')
            button.add_css_class('position')
            button.preset = name
            popover = Gtk.PopoverMenu.new_from_model(self._preset_menu('position', name).get_menu_model())
            popover.set_parent(button)
            popover.set_has_arrow(True)
            button.popover = popover
            right = Gtk.GestureClick(button=3)
            right.connect('pressed', lambda *_a, p=popover: p.popup())
            button.add_controller(right)
            self.positions_box.append(button)
            self.position_buttons.append(button)
        add = Gtk.Button(tooltip_text='Save where the camera points now, and its zoom',
                         action_name='win.save-position')
        add.set_child(Adw.ButtonContent(icon_name='list-add-symbolic', label='Save position'))
        add.add_css_class('pill')
        add.add_css_class('flat')
        self.positions_box.append(add)
        self._mark_presets()

    def _rebuild_presets(self, kind):
        if kind == 'presets':
            self._fill_presets()
        else:
            self._build_positions()

    def _values_for(self, kind):
        self.cam.refresh()
        if kind == 'presets':
            return self.cam.snapshot(self._settings_ids())
        return self.cam.snapshot(set(V.POSITION_IDS))

    def apply_preset(self, kind, name):
        if not self.cam:
            return
        self._stop_hold()
        restore.apply_named(self.store, self.info, kind, name, self.cam)
        self._refresh_rows()
        self._remember()

    def _update(self, kind, name):
        self.store.put(self.info.key, kind, name, self._values_for(kind))
        self.store.mark_used(self.info.key, kind, name)
        self._mark_presets()
        self.toast(f'“{name}” now has the current {"settings" if kind == "presets" else "position"}.')

    def _delete(self, kind, name):
        values = self.store.presets(self.info.key, kind).get(name)
        self.store.remove(self.info.key, kind, name)
        self._rebuild_presets(kind)
        toast = Adw.Toast(title=f'Deleted “{name}”', button_label='Undo')
        toast.connect('button-clicked', lambda *_: (self.store.put(self.info.key, kind, name, values),
                                                    self._rebuild_presets(kind)))
        self.toasts.add_toast(toast)

    def _ask_name(self, kind, old=None):
        if not self.cam:
            return
        what = 'Preset' if kind == 'presets' else 'Position'
        existing = self.store.presets(self.info.key, kind)
        dialog = Adw.AlertDialog(heading=f'Rename {what}' if old else f'Save {what}',
                                 body=None if old else (
                                     'Saves focus, exposure, white balance and picture settings.'
                                     if kind == 'presets' else
                                     'Saves where the camera points and how far it is zoomed.'))
        entry = Gtk.Entry(text=old or self._suggest_name(kind, existing), activates_default=True)
        dialog.set_extra_child(entry)
        dialog.add_response('cancel', 'Cancel')
        dialog.add_response('ok', 'Rename' if old else 'Save')
        dialog.set_response_appearance('ok', Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response('ok')
        dialog.set_close_response('cancel')

        def check(*_):
            name = entry.get_text().strip()
            taken = name in existing and name != old
            dialog.set_response_label('ok', 'Replace' if taken and not old else ('Rename' if old else 'Save'))
            dialog.set_response_enabled('ok', bool(name) and not (taken and old))
        entry.connect('changed', check)
        check()

        def done(_d, response):
            name = entry.get_text().strip()
            if response != 'ok' or not name:
                return
            if old:
                self.store.rename(self.info.key, kind, old, name)
            else:
                self.store.put(self.info.key, kind, name, self._values_for(kind))
                self.store.mark_used(self.info.key, kind, name)
            self._rebuild_presets(kind)
        dialog.connect('response', done)
        dialog.present(self)
        entry.grab_focus()

    @staticmethod
    def _suggest_name(kind, existing):
        base = 'Preset' if kind == 'presets' else 'Position'
        n = 1
        while f'{base} {n}' in existing:
            n += 1
        return f'{base} {n}'

    # ---- preview --------------------------------------------------------------------------

    def _on_preview_toggled(self, button):
        self.store['preview'] = button.get_active()
        if button.get_active():
            self._start_preview(force=True)
        else:
            self.preview.stop()

    def _start_preview(self, force=False):
        if not self.info:
            return
        if not self.preview_button.get_active() and not force:
            self._on_preview_state('stopped', '')
            return
        if not self.preview_button.get_active():
            self.preview_button.set_active(True)  # starts it through the toggled handler
            return
        self.preview.start(self.info.path)

    def _on_preview_state(self, state, message):
        status = self.preview_status
        if state == 'playing':
            status.set_visible(False)
            return
        status.set_visible(True)
        self.picture.set_paintable(None)
        self.picture.set_paintable(self.preview.paintable)
        if state == 'busy':
            status.set_icon_name('camera-video-symbolic')
            status.set_title('Camera in use')
            status.set_description('Another app is showing this camera, so there is no preview here. '
                                   'The settings still work and show up in that app right away.')
            self.retry.set_label('Try again')
        elif state == 'error':
            status.set_icon_name('dialog-warning-symbolic')
            status.set_title('No preview')
            status.set_description(GLib.markup_escape_text(message or 'The preview could not start.'))
            self.retry.set_label('Try again')
        else:
            status.set_icon_name('camera-disabled-symbolic')
            status.set_title('Preview off')
            status.set_description('The settings still work. Other apps can use the camera '
                                   'while the preview is off.')
            self.retry.set_label('Show preview')

    def _on_active(self, *_):
        pass

    def _on_close(self, *_):
        if self.remember_id:
            GLib.source_remove(self.remember_id)
            self._remember_now()
        self._close_camera()
        if self.poll_id:
            GLib.source_remove(self.poll_id)
            self.poll_id = 0
        return False

    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=3))
