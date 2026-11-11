"""A small V4L2 client: finds cameras and reads and writes their standard UVC controls.

Talks to the kernel directly with ioctls, so it needs nothing beyond Python.
Only standard controls are used. Vendor extension units are not touched.
"""
import ctypes
import errno
import fcntl
import glob
import os


def _ioc(direction, nr, size):
    return (direction << 30) | (size << 16) | (ord('V') << 8) | nr


class _Capability(ctypes.Structure):
    _fields_ = [('driver', ctypes.c_char * 16), ('card', ctypes.c_char * 32),
                ('bus_info', ctypes.c_char * 32), ('version', ctypes.c_uint32),
                ('capabilities', ctypes.c_uint32), ('device_caps', ctypes.c_uint32),
                ('reserved', ctypes.c_uint32 * 3)]


class _QueryExtCtrl(ctypes.Structure):
    _fields_ = [('id', ctypes.c_uint32), ('type', ctypes.c_uint32), ('name', ctypes.c_char * 32),
                ('minimum', ctypes.c_int64), ('maximum', ctypes.c_int64), ('step', ctypes.c_uint64),
                ('default_value', ctypes.c_int64), ('flags', ctypes.c_uint32),
                ('elem_size', ctypes.c_uint32), ('elems', ctypes.c_uint32),
                ('nr_of_dims', ctypes.c_uint32), ('dims', ctypes.c_uint32 * 4),
                ('reserved', ctypes.c_uint32 * 32)]


class _QueryMenu(ctypes.Structure):
    _layout_ = 'ms'
    _pack_ = 1
    _fields_ = [('id', ctypes.c_uint32), ('index', ctypes.c_uint32),
                ('name', ctypes.c_char * 32), ('reserved', ctypes.c_uint32)]


class _Control(ctypes.Structure):
    _fields_ = [('id', ctypes.c_uint32), ('value', ctypes.c_int32)]


VIDIOC_QUERYCAP = _ioc(2, 0, ctypes.sizeof(_Capability))
VIDIOC_G_CTRL = _ioc(3, 27, ctypes.sizeof(_Control))
VIDIOC_S_CTRL = _ioc(3, 28, ctypes.sizeof(_Control))
VIDIOC_QUERYMENU = _ioc(3, 37, ctypes.sizeof(_QueryMenu))
VIDIOC_QUERY_EXT_CTRL = _ioc(3, 103, ctypes.sizeof(_QueryExtCtrl))

CAP_VIDEO_CAPTURE = 0x1
NEXT_CTRL = 0x80000000 | 0x40000000  # FLAG_NEXT_CTRL | FLAG_NEXT_COMPOUND

TYPE_INTEGER, TYPE_BOOLEAN, TYPE_MENU, TYPE_BUTTON, TYPE_CLASS = 1, 2, 3, 4, 6

FLAG_DISABLED, FLAG_READ_ONLY, FLAG_INACTIVE, FLAG_WRITE_ONLY = 0x1, 0x4, 0x10, 0x40

# Standard control ids (linux/v4l2-controls.h)
BRIGHTNESS = 0x00980900
CONTRAST = 0x00980901
SATURATION = 0x00980902
HUE = 0x00980903
AUTO_WHITE_BALANCE = 0x0098090c
RED_BALANCE = 0x0098090e
BLUE_BALANCE = 0x0098090f
GAMMA = 0x00980910
GAIN = 0x00980913
POWER_LINE_FREQUENCY = 0x00980918
HUE_AUTO = 0x00980919
WHITE_BALANCE_TEMPERATURE = 0x0098091a
SHARPNESS = 0x0098091b
BACKLIGHT_COMPENSATION = 0x0098091c
EXPOSURE_AUTO = 0x009a0901
EXPOSURE_ABSOLUTE = 0x009a0902
EXPOSURE_AUTO_PRIORITY = 0x009a0903
PAN_RELATIVE = 0x009a0904
TILT_RELATIVE = 0x009a0905
PAN_RESET = 0x009a0906
TILT_RESET = 0x009a0907
PAN_ABSOLUTE = 0x009a0908
TILT_ABSOLUTE = 0x009a0909
FOCUS_ABSOLUTE = 0x009a090a
FOCUS_RELATIVE = 0x009a090b
FOCUS_AUTO = 0x009a090c
ZOOM_ABSOLUTE = 0x009a090d
ZOOM_RELATIVE = 0x009a090e
ZOOM_CONTINUOUS = 0x009a090f
PRIVACY = 0x009a0910
IRIS_ABSOLUTE = 0x009a0911
IRIS_RELATIVE = 0x009a0912
PAN_SPEED = 0x009a0920
TILT_SPEED = 0x009a0921

POSITION_IDS = (PAN_ABSOLUTE, TILT_ABSOLUTE, ZOOM_ABSOLUTE)

# Motion controls that only make sense while held, or that move the camera relative to where
# it is. They are never saved in a preset.
MOTION_IDS = {PAN_RELATIVE, TILT_RELATIVE, PAN_RESET, TILT_RESET, FOCUS_RELATIVE,
              ZOOM_RELATIVE, ZOOM_CONTINUOUS, PAN_SPEED, TILT_SPEED, IRIS_RELATIVE}


class Control:
    def __init__(self, q, menu):
        self.id = q.id
        self.type = q.type
        self.name = q.name.decode(errors='replace')
        self.minimum = q.minimum
        self.maximum = q.maximum
        self.step = max(1, q.step)
        self.default = q.default_value
        self.flags = q.flags
        self.menu = menu  # [(index, label)] for menu controls
        self.value = None

    @property
    def inactive(self):
        return bool(self.flags & FLAG_INACTIVE)

    @property
    def writable(self):
        return not self.flags & (FLAG_READ_ONLY | FLAG_DISABLED)

    @property
    def readable(self):
        return not self.flags & FLAG_WRITE_ONLY

    def clamp(self, value):
        value = int(round(value))
        value = max(self.minimum, min(self.maximum, value))
        if self.step > 1:
            value = self.minimum + round((value - self.minimum) / self.step) * self.step
            value = max(self.minimum, min(self.maximum, value))
        return int(value)

    def __repr__(self):
        return f'<Control {self.id:08x} {self.name!r} = {self.value}>'


class Camera:
    """One video capture node, opened for controls only (not for streaming)."""

    def __init__(self, path):
        self.path = path
        self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)
        cap = _Capability()
        fcntl.ioctl(self.fd, VIDIOC_QUERYCAP, cap)
        self.card = cap.card.decode(errors='replace')
        self.bus_info = cap.bus_info.decode(errors='replace')
        self.device_caps = cap.device_caps
        self.controls = {}

    def alive(self):
        try:
            fcntl.ioctl(self.fd, VIDIOC_QUERYCAP, _Capability())
            return True
        except OSError:
            return False

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    def query(self):
        """Lists every control the camera reports, with current values."""
        found = {}
        q = _QueryExtCtrl()
        q.id = NEXT_CTRL
        while True:
            try:
                fcntl.ioctl(self.fd, VIDIOC_QUERY_EXT_CTRL, q)
            except OSError:
                break
            if q.type in (TYPE_INTEGER, TYPE_BOOLEAN, TYPE_MENU, TYPE_BUTTON) \
                    and not q.flags & FLAG_DISABLED:
                menu = self._menu(q) if q.type == TYPE_MENU else None
                ctrl = Control(q, menu)
                found[ctrl.id] = ctrl
            q.id |= NEXT_CTRL
        self.controls = found
        self.refresh()
        return found

    def refresh(self):
        """Re-reads values and flags; flags change when an automatic mode is toggled."""
        for ctrl in self.controls.values():
            q = _QueryExtCtrl()
            q.id = ctrl.id
            try:
                fcntl.ioctl(self.fd, VIDIOC_QUERY_EXT_CTRL, q)
                ctrl.flags = q.flags
            except OSError:
                pass
            if ctrl.readable and ctrl.type != TYPE_BUTTON:
                try:
                    ctrl.value = self.get(ctrl.id)
                except OSError:
                    ctrl.value = None

    def _menu(self, q):
        items = []
        for index in range(int(q.minimum), int(q.maximum) + 1):
            m = _QueryMenu(id=q.id, index=index)
            try:
                fcntl.ioctl(self.fd, VIDIOC_QUERYMENU, m)
            except OSError:
                continue
            items.append((index, m.name.decode(errors='replace')))
        return items

    def get(self, cid):
        c = _Control(id=cid)
        fcntl.ioctl(self.fd, VIDIOC_G_CTRL, c)
        return c.value

    def set(self, cid, value):
        ctrl = self.controls.get(cid)
        if ctrl is not None:
            value = ctrl.clamp(value)
        c = _Control(id=cid, value=int(value))
        fcntl.ioctl(self.fd, VIDIOC_S_CTRL, c)
        if ctrl is not None:
            ctrl.value = int(value)
        return int(value)

    def try_set(self, cid, value):
        """Sets a control and returns True, or False if the camera refused it right now."""
        try:
            self.set(cid, value)
            return True
        except OSError as e:
            if e.errno in (errno.EACCES, errno.EBUSY, errno.EINVAL, errno.ERANGE, errno.EIO,
                           errno.EPERM, errno.EPROTO):
                return False
            raise

    def snapshot(self, ids=None):
        """Current values of every savable control, or only the ones in ids."""
        values = {}
        for ctrl in self.controls.values():
            if ids is not None and ctrl.id not in ids:
                continue
            if ctrl.id in MOTION_IDS or ctrl.type == TYPE_BUTTON:
                continue
            if not ctrl.writable or not ctrl.readable or ctrl.value is None:
                continue
            values[ctrl.id] = ctrl.value
        return values

    def apply(self, values):
        """Applies saved values. Automatic modes go first, so the manual values they unlock
        are accepted. Returns the number of values that took."""
        values = {int(k): v for k, v in values.items()}
        known = [cid for cid in values if cid in self.controls]
        switches = [c for c in known if self.controls[c].type in (TYPE_BOOLEAN, TYPE_MENU)]
        numbers = [c for c in known if c not in switches]
        applied = 0
        for cid in switches:
            applied += self.try_set(cid, values[cid])
        self.refresh()
        for cid in numbers:
            if self.controls[cid].inactive:
                continue
            applied += self.try_set(cid, values[cid])
        self.refresh()
        return applied

    def reset(self):
        defaults = {c.id: c.default for c in self.controls.values()
                    if c.writable and c.id not in MOTION_IDS and c.type != TYPE_BUTTON}
        return self.apply(defaults)


def _sysfs_usb_device(path):
    node = os.path.basename(path)
    dev = os.path.realpath(f'/sys/class/video4linux/{node}/device')
    for candidate in (dev, os.path.dirname(dev)):
        if os.path.isfile(os.path.join(candidate, 'idVendor')):
            return candidate
    return None


def _read(path):
    try:
        with open(path, encoding='utf-8') as f:
            return f.read().strip()
    except OSError:
        return ''


class CameraInfo:
    def __init__(self, path, card, bus_info):
        self.path = path
        self.bus_info = bus_info
        # UVC cards are often "Name: Name truncated"; keep the first half.
        self.name = card.split(':')[0].strip() if ':' in card else card
        usb = _sysfs_usb_device(path)
        vendor = _read(os.path.join(usb, 'idVendor')) if usb else ''
        product = _read(os.path.join(usb, 'idProduct')) if usb else ''
        serial = _read(os.path.join(usb, 'serial')) if usb else ''
        self.usb_id = f'{vendor}:{product}' if vendor else ''
        # Presets follow the camera: its serial number if it has one, otherwise its model.
        self.key = '-'.join(x for x in (vendor, product, serial) if x) or self.name

    def __repr__(self):
        return f'<CameraInfo {self.path} {self.name!r} {self.key}>'


def list_cameras():
    """Video capture nodes, one per physical camera (metadata nodes are skipped)."""
    cameras = []
    seen = set()
    nodes = sorted(glob.glob('/dev/video*'), key=lambda p: int(p[10:]) if p[10:].isdigit() else 999)
    for path in nodes:
        try:
            with Camera(path) as cam:
                if not cam.device_caps & CAP_VIDEO_CAPTURE:
                    continue
                if cam.bus_info in seen:
                    continue
                seen.add(cam.bus_info)
                cameras.append(CameraInfo(path, cam.card, cam.bus_info))
        except OSError:
            continue
    return cameras
