"""Apply queued desktop metadata before Cinnamon starts nemo-desktop."""
import json
from pathlib import Path
import re
from tempfile import NamedTemporaryFile

from gi.repository import GLib

NEMO_FILE = "desktop-metadata"
SYSTEM_ICONS = {"home", "computer", "trash", "network"}


def read_keyfile(path):
    data = GLib.KeyFile()
    if path.is_file() and not path.is_symlink():
        contents = path.read_text(encoding="utf-8")
        data.load_from_data(contents, len(contents.encode("utf-8")), GLib.KeyFileFlags.KEEP_COMMENTS)
    return data


def merge_desktop_metadata(source, destination):
    for group in source.get_groups()[0]:
        if group in SYSTEM_ICONS or re.fullmatch(r"desktop-monitor-[0-9]+", group):
            for key in source.get_keys(group)[0]:
                destination.set_value(group, key, source.get_value(group, key))


def fit_system_icons(data, monitors):
    if not monitors:
        return
    for group in SYSTEM_ICONS.intersection(data.get_groups()[0]):
        keys = data.get_keys(group)[0]
        position = re.fullmatch(r"(-?\d+),(-?\d+)", data.get_value(group, "nemo-icon-position")) if "nemo-icon-position" in keys else None
        try:
            monitor = data.get_integer(group, "monitor")
        except GLib.Error:
            monitor = 0
        if 0 <= monitor < len(monitors):
            width, height = monitors[monitor]
            if position and 0 <= int(position[1]) < width and 0 <= int(position[2]) < height:
                continue
        else:
            data.set_integer(group, "monitor", 0)
        # Let Nemo place out-of-bounds icons in free slots on startup.
        for key in ("nemo-icon-position", "nemo-icon-position-timestamp"):
            if key in keys:
                data.remove_key(group, key)


def apply_pending(pending, destination):
    if not pending.exists():
        return
    if pending.is_symlink() or any(path.is_symlink() for path in (destination, *destination.parents)):
        raise ValueError("Symbolic link in Nemo desktop metadata restore")

    request = json.loads(pending.read_text(encoding="utf-8"))
    source = GLib.KeyFile()
    contents = request["metadata"]
    source.load_from_data(contents, len(contents.encode("utf-8")), GLib.KeyFileFlags.NONE)
    destination_data = read_keyfile(destination)
    merge_desktop_metadata(source, destination_data)
    fit_system_icons(destination_data, request["monitors"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=destination.parent, delete=False) as output:
        temporary = Path(output.name)
    try:
        destination_data.save_to_file(str(temporary))
        temporary.replace(destination)
        pending.unlink()
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    config = Path(__file__).parent.parent
    apply_pending(Path(__file__).with_name("nemo-desktop-pending.json"), config / "nemo" / NEMO_FILE)
