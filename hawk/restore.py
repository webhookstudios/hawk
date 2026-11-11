"""Puts a camera back the way you left it.

Used at login, when a camera is plugged in, when another app opens the camera, when Hawk
starts, and by the command line.
"""
from . import v4l2, vendor


def apply_named(store, info, kind, name, cam=None):
    values = store.presets(info.key, kind).get(name)
    if values is None:
        return None
    own = cam is None
    cam = cam or v4l2.Camera(info.path)
    try:
        if own:
            cam.query()
        applied = cam.apply(values)
        if own:
            # Applying from outside the window (the command line): this is now how you left it.
            store.save_current(info.key, {**store.current(info.key), **cam.snapshot(set(values))})
    finally:
        if own:
            cam.close()
    store.mark_used(info.key, kind, name)
    return applied


def differences(cam, wanted):
    """Saved values the camera doesn't have right now (ignoring ones locked by an auto mode)."""
    cam.refresh()
    out = {}
    for cid, value in wanted.items():
        ctrl = cam.controls.get(cid)
        if ctrl is None or not ctrl.writable or not ctrl.readable:
            continue
        if ctrl.inactive and ctrl.type not in (v4l2.TYPE_BOOLEAN, v4l2.TYPE_MENU):
            continue
        if ctrl.value != value:
            out[cid] = value
    return out


def wanted(store, info):
    """What the camera should have: how you left it, or else the last presets."""
    current = store.current(info.key)
    if current:
        return current
    values = {}
    for kind in ('presets', 'positions'):
        name = store.last(info.key, kind)
        values.update(store.presets(info.key, kind).get(name, {}) if name else {})
    return values


def reapply_last(store, info, cam=None, only_if_changed=False):
    """Puts back how you left the camera. Returns a short description of what was put back,
    or [] if nothing needed to change."""
    own = cam is None
    cam = cam or v4l2.Camera(info.path)
    try:
        if own:
            cam.query()
        current = store.current(info.key)
        if current:
            changed = differences(cam, current)
            if changed or not only_if_changed:
                cam.apply(current)
                vendor.apply(cam, store.vendor(info.key))
            return ['your settings'] if changed else []
        # Nothing remembered yet (older settings file): fall back to the last presets.
        done = []
        for kind in ('presets', 'positions'):
            name = store.last(info.key, kind)
            values = store.presets(info.key, kind).get(name) if name else None
            if values and (not only_if_changed or differences(cam, values)):
                cam.apply(values)
                store.mark_used(info.key, kind, name)
                done.append(name)
        vendor.apply(cam, store.vendor(info.key))
        return done
    finally:
        if own:
            cam.close()
