"""Camera-specific extras that standard UVC controls don't cover.

Only protocols that are published and were used on that exact model are included here.
Nothing is guessed: a camera that isn't recognized gets no vendor writes at all.

OBSBOT Meet 4K: AI tracking on/off, from the open-source meet4k tool
(https://github.com/samliddicott/meet4k, LGPL-2.1), which was written for and used on that
camera: extension unit 6, selector 6, 60-byte commands of the form [field, length, value...].
Other OBSBOT models (Meet 2, Tiny, Tiny 2, Tail) use different units or layouts that are not
published, so Squint leaves their AI modes alone.
"""
import ctypes
import fcntl

UVC_SET_CUR = 0x01
UVC_GET_LEN = 0x85


class _XuQuery(ctypes.Structure):
    _fields_ = [('unit', ctypes.c_uint8), ('selector', ctypes.c_uint8), ('query', ctypes.c_uint8),
                ('size', ctypes.c_uint16), ('data', ctypes.c_void_p)]


UVCIOC_CTRL_QUERY = (3 << 30) | (ctypes.sizeof(_XuQuery) << 16) | (ord('u') << 8) | 0x21


def _query(fd, unit, selector, query, buf):
    q = _XuQuery(unit, selector, query, ctypes.sizeof(buf), ctypes.addressof(buf))
    fcntl.ioctl(fd, UVCIOC_CTRL_QUERY, q)


class Meet4K:
    """OBSBOT Meet 4K. Each option is (value, label, command bytes)."""
    UNIT = 6
    SELECTOR = 6
    LENGTH = 60
    TRACKING = [
        ('fixed', 'Fixed', [0x00, 0x01, 0x00]),
        ('track', 'Track me', [0x00, 0x01, 0x02]),
    ]

    @classmethod
    def detect(cls, cam):
        if 'meet 4k' not in cam.card.lower():
            return None
        try:
            buf = (ctypes.c_uint8 * 2)()
            _query(cam.fd, cls.UNIT, cls.SELECTOR, UVC_GET_LEN, buf)
        except OSError:
            return None
        return cls() if buf[0] | buf[1] << 8 == cls.LENGTH else None

    def set_tracking(self, cam, value):
        command = next((c for v, _l, c in self.TRACKING if v == value), None)
        if command is None:
            return False
        buf = (ctypes.c_uint8 * self.LENGTH)(*command)
        try:
            _query(cam.fd, self.UNIT, self.SELECTOR, UVC_SET_CUR, buf)
            return True
        except OSError:
            return False


def detect(cam):
    """The vendor extras for this camera, or None."""
    return Meet4K.detect(cam)


def apply(cam, values):
    """Puts saved vendor choices back ({'tracking': 'fixed'} and so on)."""
    extra = detect(cam)
    if not extra or not values:
        return
    if values.get('tracking'):
        extra.set_tracking(cam, values['tracking'])
