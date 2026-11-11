"""Notices when any app opens a camera, using inotify on its /dev/video node.

Opening a device node raises IN_OPEN for every process, so Hawk learns that Zoom, a browser
or OBS just started the camera, and can check that your settings are still in place.
"""
import ctypes
import ctypes.util
import os
import struct

from gi.repository import GLib

IN_OPEN = 0x20
IN_NONBLOCK = 0o4000
IN_CLOEXEC = 0o2000000

_libc = ctypes.CDLL(ctypes.util.find_library('c') or 'libc.so.6', use_errno=True)
_libc.inotify_init1.argtypes = [ctypes.c_int]
_libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
_libc.inotify_rm_watch.argtypes = [ctypes.c_int, ctypes.c_int]


class Guard:
    def __init__(self, on_open):
        self.on_open = on_open
        self.watches = {}  # wd -> path
        self.fd = _libc.inotify_init1(IN_NONBLOCK | IN_CLOEXEC)
        if self.fd < 0:
            self.fd = None
            return
        GLib.unix_fd_add_full(GLib.PRIORITY_DEFAULT, self.fd, GLib.IOCondition.IN, self._readable)

    def watch(self, paths):
        """Watches exactly these device nodes."""
        if self.fd is None:
            return
        for wd, path in list(self.watches.items()):
            if path not in paths or not os.path.exists(path):
                _libc.inotify_rm_watch(self.fd, wd)
                del self.watches[wd]
        for path in paths:
            if path in self.watches.values():
                continue
            wd = _libc.inotify_add_watch(self.fd, path.encode(), IN_OPEN)
            if wd >= 0:
                self.watches[wd] = path

    def _readable(self, *_):
        try:
            data = os.read(self.fd, 64 * 1024)
        except BlockingIOError:
            return True
        except OSError:
            return True
        seen = set()
        offset = 0
        while offset + 16 <= len(data):
            wd, mask, _cookie, length = struct.unpack_from('iIII', data, offset)
            offset += 16 + length
            path = self.watches.get(wd)
            if path and mask & IN_OPEN and path not in seen:
                seen.add(path)
                self.on_open(path)
        return True
