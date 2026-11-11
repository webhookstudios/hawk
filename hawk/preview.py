"""The live preview: a GStreamer pipeline drawn into a Gtk.Picture."""
import gi

gi.require_version('Gst', '1.0')
from gi.repository import GLib, Gst  # noqa: E402

Gst.init(None)

# Tried in order until one plays. MJPEG is decoded with jpegdec directly: decodebin would add
# jpegparse, which rejects the frames of some webcams (the OBSBOT Meet 2 among them).
ATTEMPTS = [
    ('image/jpeg,width=1280,height=720', 'jpegdec'),
    ('image/jpeg', 'jpegdec'),
    ('video/x-raw', None),
    (None, 'decodebin'),
]


def available():
    return all(Gst.ElementFactory.find(n) for n in ('v4l2src', 'decodebin', 'gtk4paintablesink'))


class Preview:
    """Streams one camera. on_state(state, message) is called with 'playing', 'busy',
    'error' or 'stopped'."""

    def __init__(self, on_state):
        self.on_state = on_state
        self.pipeline = None
        self.sink = Gst.ElementFactory.make('gtk4paintablesink', None) if available() else None
        self.paintable = self.sink.props.paintable if self.sink else None
        self.path = None

    def start(self, path, attempt=0):
        self.stop(notify=False)
        if not self.sink:
            self.on_state('error', 'The GStreamer GTK 4 plugin is not installed '
                                   '(gstreamer1.0-gtk4).')
            return
        self.path = path
        self.attempt = attempt
        self.reached_playing = False
        self.failed = False
        caps_text, decoder = ATTEMPTS[attempt]
        pipeline = Gst.Pipeline.new('preview')
        src = Gst.ElementFactory.make('v4l2src', None)
        src.props.device = path
        chain = [src]
        if caps_text:
            caps = Gst.ElementFactory.make('capsfilter', None)
            caps.props.caps = Gst.Caps.from_string(caps_text)
            chain.append(caps)
        decode = Gst.ElementFactory.make(decoder, None) if decoder else None
        convert = Gst.ElementFactory.make('videoconvert', None)
        for el in chain + ([decode] if decode else []) + [convert, self.sink]:
            pipeline.add(el)
        for a, b in zip(chain, chain[1:]):
            a.link(b)
        if decoder == 'decodebin':
            chain[-1].link(decode)

            def pad_added(_decode, pad):
                target = convert.get_static_pad('sink')
                if not target.is_linked():
                    pad.link(target)
            decode.connect('pad-added', pad_added)
        elif decode:
            chain[-1].link(decode)
            decode.link(convert)
        else:
            chain[-1].link(convert)
        convert.link(self.sink)

        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect('message', self._on_message)
        self.pipeline = pipeline
        pipeline.set_state(Gst.State.PLAYING)

    def _on_message(self, bus, msg):
        if msg.type == Gst.MessageType.ERROR:
            err, debug = msg.parse_error()
            text = f'{err.message} {debug or ""}'
            busy = 'busy' in text.lower() or 'EBUSY' in text
            if self.pipeline is None or self.failed:
                return
            self.failed = True  # one error is enough; ignore the ones that follow it
            if not busy and not self.reached_playing and self.attempt + 1 < len(ATTEMPTS):
                GLib.idle_add(self._retry, self.path, self.attempt + 1)
                return
            GLib.idle_add(self._stop_with, 'busy' if busy else 'error', err.message)
        elif msg.type == Gst.MessageType.STATE_CHANGED and msg.src == self.pipeline:
            _old, new, _pending = msg.parse_state_changed()
            if new == Gst.State.PLAYING:
                self.reached_playing = True
                self.on_state('playing', '')

    def _retry(self, path, attempt):
        self.start(path, attempt)
        return False

    def _stop_with(self, state, message):
        self.stop(notify=False)
        self.on_state(state, message)
        return False

    def stop(self, notify=True):
        if self.pipeline:
            self.pipeline.get_bus().remove_signal_watch()
            self.pipeline.set_state(Gst.State.NULL)
            # The sink is reused by the next pipeline, so take it out first.
            self.pipeline.remove(self.sink)
            self.pipeline = None
            if notify:
                self.on_state('stopped', '')

    @property
    def playing(self):
        return self.pipeline is not None
