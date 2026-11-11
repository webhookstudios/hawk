"""Settings and presets, kept as one plain JSON file in ~/.config/squint."""
import json
import os

CONFIG = os.path.join(os.environ.get('XDG_CONFIG_HOME', os.path.expanduser('~/.config')), 'squint')

DEFAULTS = {
    'camera': '',          # key of the camera picked last
    'preview': True,       # show the live preview when the window opens
    'reapply': True,       # keep the camera set the way you left it: at login, plug-in and app start
    'cameras': {},         # camera key -> {'name', 'presets', 'positions', 'last_preset',
                           #                'last_position', 'current', 'vendor'}
}


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


class Store:
    def __init__(self, path=None):
        self.path = path or os.path.join(CONFIG, 'settings.json')
        self.load()

    def load(self):
        self.data = json.loads(json.dumps(DEFAULTS))
        try:
            with open(self.path, encoding='utf-8') as f:
                self.data.update(json.load(f))
        except (OSError, ValueError):
            pass

    def save(self):
        _write(self.path, self.data)

    def __getitem__(self, key):
        return self.data.get(key, DEFAULTS.get(key))

    def __setitem__(self, key, value):
        self.data[key] = value
        self.save()

    def camera(self, key, name=''):
        cams = self.data.setdefault('cameras', {})
        entry = cams.setdefault(key, {})
        entry.setdefault('name', name)
        entry.setdefault('presets', {})      # name -> {control id: value}
        entry.setdefault('positions', {})    # name -> {control id: value}
        entry.setdefault('last_preset', '')
        entry.setdefault('last_position', '')
        entry.setdefault('current', {})      # every setting as you last left it in Squint
        entry.setdefault('vendor', {})       # camera-specific choices, e.g. {'tracking': 'fixed'}
        if name and entry['name'] != name:
            entry['name'] = name
        return entry

    # Presets are stored with string keys (JSON); callers get int keys back.

    def presets(self, key, kind):
        return {n: {int(k): v for k, v in vals.items()}
                for n, vals in self.camera(key)[kind].items()}

    def put(self, key, kind, name, values):
        self.camera(key)[kind][name] = {str(k): int(v) for k, v in values.items()}
        self.save()

    def remove(self, key, kind, name):
        entry = self.camera(key)
        entry[kind].pop(name, None)
        last = 'last_preset' if kind == 'presets' else 'last_position'
        if entry[last] == name:
            entry[last] = ''
        self.save()

    def rename(self, key, kind, old, new):
        entry = self.camera(key)
        if old not in entry[kind] or not new or new == old:
            return
        entry[kind] = {(new if n == old else n): v for n, v in entry[kind].items()}
        last = 'last_preset' if kind == 'presets' else 'last_position'
        if entry[last] == old:
            entry[last] = new
        self.save()

    def mark_used(self, key, kind, name):
        last = 'last_preset' if kind == 'presets' else 'last_position'
        self.camera(key)[last] = name
        self.save()

    def last(self, key, kind):
        entry = self.camera(key)
        name = entry['last_preset' if kind == 'presets' else 'last_position']
        return name if name in entry[kind] else ''

    def current(self, key):
        return {int(k): v for k, v in self.camera(key)['current'].items()}

    def save_current(self, key, values):
        self.camera(key)['current'] = {str(k): int(v) for k, v in values.items()}
        self.save()

    def vendor(self, key):
        return dict(self.camera(key)['vendor'])

    def set_vendor(self, key, name, value):
        self.camera(key)['vendor'][name] = value
        self.save()
