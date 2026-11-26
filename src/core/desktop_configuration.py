"""Cinnamon/Nemo desktop settings, independent of files on the desktop."""
from pathlib import Path
import re
import tempfile

from gi.repository import GLib

from savedesktop.core.backup_input import restore_file

NEMO_FILE = "desktop-metadata"
ARCHIVE_FILE = "General/NemoDesktop.ini"
SYSTEM_ICONS = {"home", "computer", "trash", "network"}
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


def read_keyfile(path):
    data = GLib.KeyFile()
    if path.is_file() and not path.is_symlink():
        data.load_from_file(str(path), GLib.KeyFileFlags.KEEP_COMMENTS)
    return data


def merge_desktop_metadata(source, destination):
    for group in source.get_groups()[0]:
        if group in SYSTEM_ICONS or re.fullmatch(r"desktop-monitor-[0-9]+", group):
            for key in source.get_keys(group)[0]:
                destination.set_value(group, key, source.get_value(group, key))


def save_desktop_configuration(nemo_directory):
    source = nemo_directory / NEMO_FILE
    if source.is_file():
        data = GLib.KeyFile()
        merge_desktop_metadata(read_keyfile(source), data)
        if data.get_groups()[0]:
            data.save_to_file(ARCHIVE_FILE)


def restore_desktop_configuration(nemo_directory):
    for filename in (ARCHIVE_FILE, f"DE/nemo/{NEMO_FILE}", f"nemo/{NEMO_FILE}"):
        source = Path(filename)
        if source.is_symlink() or not source.is_file():
            continue
        destination = nemo_directory / NEMO_FILE
        data = read_keyfile(destination)
        merge_desktop_metadata(read_keyfile(source), data)
        with tempfile.TemporaryDirectory(prefix="nemo-desktop-") as temporary:
            staged = Path(temporary) / NEMO_FILE
            data.save_to_file(str(staged))
            restore_file(staged, destination)
        return
