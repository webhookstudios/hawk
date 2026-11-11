#!/bin/sh
# Builds dist/squint_<version>_all.deb. Needs only dpkg-deb.
#
#   /usr/share/squint/squint/   the Python package
#   /usr/bin/squint             launcher
#   /usr/share/applications, /usr/share/icons   apps-menu entry and icon
#   /etc/xdg/autostart          login item that puts the last preset back
set -e

APP_ID=io.github.webhookstudios.Squint
ROOT="$(cd "$(dirname "$0")" && pwd)"
VERSION="$(sed -n "s/^__version__ = '\(.*\)'/\1/p" "$ROOT/squint/__init__.py")"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

SHARE="$STAGE/usr/share/squint"
install -d "$SHARE/squint" "$STAGE/DEBIAN" "$STAGE/usr/bin" \
    "$STAGE/usr/share/applications" \
    "$STAGE/usr/share/icons/hicolor/scalable/apps" \
    "$STAGE/usr/share/doc/squint" "$STAGE/etc/xdg/autostart"

install -m 644 "$ROOT"/squint/*.py "$ROOT/squint/style.css" "$SHARE/squint/"
install -m 755 "$ROOT/bin/squint" "$STAGE/usr/bin/squint"
install -m 644 "$ROOT/data/$APP_ID.svg" "$STAGE/usr/share/icons/hicolor/scalable/apps/"
sed -e 's|@EXEC@|squint|' -e "s|@ICON@|$APP_ID|" "$ROOT/data/$APP_ID.desktop.in" \
    > "$STAGE/usr/share/applications/$APP_ID.desktop"
sed -e 's|@EXEC@|squint|' "$ROOT/data/$APP_ID.autostart.desktop.in" \
    > "$STAGE/etc/xdg/autostart/$APP_ID.autostart.desktop"
chmod 644 "$STAGE/usr/share/applications/$APP_ID.desktop" "$STAGE/etc/xdg/autostart/$APP_ID.autostart.desktop"

cat > "$STAGE/usr/share/doc/squint/copyright" <<EOT
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: Squint
Source: https://github.com/webhookstudios/squint

Files: *
Copyright: $(date +%Y) webhookstudios
License: GPL-2+
 On Debian systems, the full text of the GNU General Public License
 version 2 can be found in /usr/share/common-licenses/GPL-2.
EOT
chmod 644 "$STAGE/usr/share/doc/squint/copyright"

cat > "$STAGE/DEBIAN/control" <<EOT
Package: squint
Version: $VERSION
Architecture: all
Maintainer: webhookstudios <354619278+webhookstudios@users.noreply.github.com>
Installed-Size: $(du -sk "$STAGE/usr" | cut -f1)
Depends: python3, python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1 (>= 1.5), gir1.2-gst-plugins-base-1.0, gstreamer1.0-plugins-good, gstreamer1.0-gtk4
Section: video
Priority: optional
Homepage: https://github.com/webhookstudios/squint
Description: Webcam settings, live preview and presets
 Squint is a control panel for USB webcams. It shows a live preview and
 every setting the camera reports: pan, tilt and zoom, focus, exposure,
 gain, white balance, brightness, contrast, saturation, sharpness,
 backlight compensation and anti-flicker.
 .
 Save named presets of settings and of camera positions and recall them
 with one click. The last preset is put back at login and whenever the
 camera is plugged in. Uses the standard UVC controls (V4L2).
EOT

find "$STAGE" -type d -exec chmod 755 {} +

mkdir -p "$ROOT/dist"
dpkg-deb --root-owner-group --build "$STAGE" "$ROOT/dist/squint_${VERSION}_all.deb"
