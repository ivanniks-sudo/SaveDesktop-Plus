"""Cinnamon/Nemo desktop settings, independent of files on the desktop."""
import json
from pathlib import Path
import tempfile

from gi.repository import GLib

from savedesktop.core.backup_input import restore_file
from savedesktop.core import nemo_session_restore
from savedesktop.core.nemo_session_restore import NEMO_FILE, merge_desktop_metadata, read_keyfile

ARCHIVE_FILE = "General/NemoDesktop.ini"
SHARED_SETTINGS = {
    "org/nemo/preferences": {"default-sort-order", "default-sort-in-reverse-order"},
    "org/nemo/icon-view": {"default-zoom-level", "captions"},
}


def filter_desktop_settings(dump, enabled):
    section = ""
    lines = []
    for line in dump.splitlines(keepends=True):
        text = line.strip()
        if text.startswith("[") and text.endswith("]"):
            section = text[1:-1]
        if section == "org/nemo/desktop" or section.startswith("org/nemo/desktop/"):
            if not enabled:
                continue
        key = text.partition("=")[0].strip()
        if section == "org/nemo/preferences" and key == "desktop-is-home-dir":
            # Keep the destination user's desktop location.
            continue
        if not enabled and key in SHARED_SETTINGS.get(section, ()) and "=" in text:
            continue
        lines.append(line)
    return "".join(lines)


def save_desktop_configuration(nemo_directory):
    source = nemo_directory / NEMO_FILE
    if source.is_file():
        data = GLib.KeyFile()
        merge_desktop_metadata(read_keyfile(source), data)
        if data.get_groups()[0]:
            data.save_to_file(ARCHIVE_FILE)


def queue_desktop_configuration(root, config, app_id, monitors):
    for filename in (ARCHIVE_FILE, f"DE/nemo/{NEMO_FILE}", f"nemo/{NEMO_FILE}"):
        source = root / filename
        if source.is_symlink() or not source.is_file():
            continue
        data = GLib.KeyFile()
        merge_desktop_metadata(read_keyfile(source), data)
        if not data.get_groups()[0]:
            continue
        state = config / app_id
        helper = state / "nemo_session_restore.py"
        autostart = config / "autostart" / f"{app_id}.NemoDesktop.desktop"
        entry = GLib.KeyFile()
        section = "Desktop Entry"
        entry.set_string(section, "Type", "Application")
        entry.set_string(section, "Name", "SaveDesktop Plus (Nemo desktop configuration)")
        entry.set_string(section, "OnlyShowIn", "X-Cinnamon;")
        entry.set_string(section, "X-GNOME-Autostart-Phase", "Initialization")
        entry.set_boolean(section, "NoDisplay", True)
        # Desktop Exec quoting is distinct from shell quoting and keyfile escaping.
        argument = str(helper).replace("%", "%%")
        for char in ('\\', '"', '`', '$'):
            argument = argument.replace(char, '\\' + char)
        entry.set_string(section, "Exec", f'/usr/bin/python3 -I "{argument}"')
        with tempfile.TemporaryDirectory(prefix="nemo-desktop-") as temporary:
            staging = Path(temporary)
            desktop_file = staging / autostart.name
            entry.save_to_file(str(desktop_file))
            payload = staging / "nemo-desktop-pending.json"
            payload.write_text(json.dumps({"metadata": data.to_data()[0], "monitors": monitors}), encoding="utf-8")
            restore_file(Path(nemo_session_restore.__file__), helper)
            with nemo_session_restore.pending_lock(state):
                restore_file(desktop_file, autostart)
                restore_file(payload, state / payload.name)
        return True
    return False
