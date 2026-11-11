#!/bin/sh
# Installs Hawk for the current user, without root. Run ./install.sh --uninstall to remove it.
set -e

APP_ID=io.github.webhookstudios.Hawk
ROOT="$(cd "$(dirname "$0")" && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}"
DEST="$DATA/hawk"
BIN="$HOME/.local/bin/hawk"

if [ "$1" = "--uninstall" ]; then
    pkill -f "$DEST/hawk-launcher --background" 2>/dev/null || true
    rm -rf "$DEST" "$BIN" \
        "$DATA/applications/$APP_ID.desktop" \
        "$DATA/icons/hicolor/scalable/apps/$APP_ID.svg" \
        "$CONFIG/autostart/$APP_ID.autostart.desktop"
    update-desktop-database "$DATA/applications" 2>/dev/null || true
    echo "Hawk is removed. Your presets in $CONFIG/hawk were kept."
    exit 0
fi

python3 - <<'PY' || { echo "Hawk needs these packages: sudo apt install python3-gi gir1.2-adw-1 gir1.2-gst-plugins-base-1.0 gstreamer1.0-plugins-good gstreamer1.0-gtk4"; exit 1; }
import gi
gi.require_version('Adw', '1')
gi.require_version('Gst', '1.0')
from gi.repository import Adw, Gst
PY

rm -rf "$DEST"
mkdir -p "$DEST" "$DATA/applications" "$DATA/icons/hicolor/scalable/apps" "$CONFIG/autostart" "$(dirname "$BIN")"
cp -r "$ROOT/hawk" "$DEST/hawk"
find "$DEST" -name __pycache__ -type d -exec rm -rf {} +
install -m 755 "$ROOT/bin/hawk" "$DEST/hawk-launcher"
ln -sf "$DEST/hawk-launcher" "$BIN"
cp "$ROOT/data/$APP_ID.svg" "$DATA/icons/hicolor/scalable/apps/"
sed -e "s|@EXEC@|$DEST/hawk-launcher|" -e "s|@ICON@|$DATA/icons/hicolor/scalable/apps/$APP_ID.svg|" \
    "$ROOT/data/$APP_ID.desktop.in" > "$DATA/applications/$APP_ID.desktop"
sed -e "s|@EXEC@|$DEST/hawk-launcher|" \
    "$ROOT/data/$APP_ID.autostart.desktop.in" > "$CONFIG/autostart/$APP_ID.autostart.desktop"
update-desktop-database "$DATA/applications" 2>/dev/null || true

echo "Hawk is installed. Open it from the apps menu, or run: hawk"

python3 - <<'PY' 2>/dev/null || echo "
The live preview needs the GStreamer GTK 4 plugin. Install it with:
  sudo apt install gstreamer1.0-gtk4"
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst
Gst.init(None)
assert Gst.ElementFactory.find('gtk4paintablesink') and Gst.ElementFactory.find('v4l2src')
PY
case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *) echo "Note: $HOME/.local/bin is not on your PATH yet; log out and back in, or use $BIN." ;;
esac
