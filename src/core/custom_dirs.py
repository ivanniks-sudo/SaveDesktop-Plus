import os
from pathlib import Path
import re
import shutil
import sys


def portal_parts(path, mount):
    """Return the document ID, exported root and relative path in the grant."""
    try:
        parts = Path(path).relative_to(mount).parts
    except ValueError:
        return None
    if len(parts) >= 2 and ".." not in parts:
        return parts[0], mount.joinpath(*parts[:2]), parts[2:]
    return None


def access_path(saved_path):
    # Recover only the old SaveDesktop highlight around a document-portal path.
    # Ordinary absolute paths may contain literal angle brackets and ampersands.
    old_row = re.fullmatch(
        r"(<span color='orange'>)+(/run/user/\d+/doc/[^/]+/.+?)(</span>)+",
        saved_path,
        flags=re.DOTALL,
    )
    return old_row[2] if old_row else saved_path


def original_path(path, *, flatpak):
    if not flatpak:
        return path

    from gi.repository import Gio, GLib

    mount = Path(GLib.get_user_runtime_dir()) / "doc"
    document = portal_parts(path, mount)
    if not document:
        return path

    doc_id, exported_root, relative = document
    try:
        host = os.getxattr(exported_root, "user.document-portal.host-path")
    except OSError:
        host = b""
    if not host:
        interface = "org.freedesktop.portal.Documents"
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            reply = bus.call_sync(
                interface, "/org/freedesktop/portal/documents", interface, "GetHostPaths",
                GLib.Variant("(as)", ([doc_id],)), GLib.VariantType.new("(a{say})"),
                Gio.DBusCallFlags.NONE, 1000, None,
            )
            host = reply.unpack()[0].get(doc_id, b"")
        except GLib.Error:
            return None

    resolved = Path(os.fsdecode(bytes(host).rstrip(b"\0")))
    if resolved.is_absolute():
        return str(resolved.joinpath(*relative))
    return None


def enabled_folders(settings):
    disabled = {access_path(path) for path in settings.get_strv("disabled-custom-dirs")}
    return [path for path in settings.get_strv("custom-dirs") if access_path(path) not in disabled]


class CustomFolderCopyError(OSError):
    pass


def save_custom_folders(folders, target, *, flatpak):
    target.mkdir(exist_ok=True)
    entries = []
    for saved_path in folders:
        source = Path(access_path(saved_path))
        if not source.is_absolute() or not source.name or ".." in source.parts:
            raise CustomFolderCopyError(f"Cannot back up custom folder: {saved_path!r}. Select it again.")
        host = original_path(str(source), flatpak=flatpak)
        if host is None:
            print(
                f"Original path unavailable for {source}. Using the folder granted by Document Portal.",
                file=sys.stderr,
            )
        try:
            # Avoid copying the staging directory into itself.
            staging = target.parent.resolve()
            if staging.is_relative_to(source.resolve()) or (
                host is not None and staging.is_relative_to(host)
            ):
                raise OSError("The selected folder contains the backup workspace.")

            destination = target / source.name
            index = 2
            while destination.exists():
                destination = target / f"{source.name}_{index}"
                index += 1
            shutil.copytree(source, destination, symlinks=True)
        except (OSError, RuntimeError) as error:
            raise CustomFolderCopyError(
                f"Cannot back up custom folder {host or source}: {error}. "
                "Check access or select the folder again. No new backup was written."
            ) from None
        print(f"[OK] Copied custom folder: {host or source} → {destination}")
        entries.append({"name": source.name, "original_path": host,
                        "archive_path": f"{target.name}/{destination.name}"})
    return entries


def read_folder_metadata(root):
    import json
    from savedesktop.core.backup_input import member_path

    directory = root / "Custom_Dirs"
    if directory.is_symlink():
        raise ValueError("Custom_Dirs cannot be a symbolic link.")
    if not directory.is_dir():
        return []
    entries = {
        f"Custom_Dirs/{path.name}": {"name": path.name, "archive_path": f"Custom_Dirs/{path.name}"}
        for path in sorted(directory.iterdir()) if path.is_dir() and not path.is_symlink()
    }
    metadata = root / "custom-dirs.json"
    if metadata.is_file() and not metadata.is_symlink():
        data = json.loads(metadata.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("folders"), list):
            raise ValueError("Unsupported custom-dirs.json metadata.")
        seen = set()
        for entry in data["folders"]:
            if not isinstance(entry, dict) or not isinstance(entry.get("archive_path"), str):
                raise ValueError("Invalid custom folder metadata.")
            key = entry["archive_path"]
            if key not in entries or key in seen:
                raise ValueError(f"Invalid or duplicate custom folder: {key}")
            seen.add(key)
            relative = entry.get("home_relative")
            if relative is not None:
                if not isinstance(relative, str):
                    raise ValueError("Invalid home-relative path.")
                member_path(relative)
            original = entry.get("original_path")
            if original is not None and (not isinstance(original, str) or not Path(original).is_absolute()
                                         or ".." in Path(original).parts or re.match(r"^/run/user/\d+/doc/", original)):
                raise ValueError("Invalid original folder path.")
            entries[key].update({
                "name": entry["name"] if isinstance(entry.get("name"), str) else entries[key]["name"],
                "original_path": original, "home_relative": relative,
            })
    return list(entries.values())


def folder_destination(entry, home, cinnamon):
    relative = entry.get("home_relative")
    if relative:
        return str(home / relative)
    if cinnamon and entry["archive_path"] == "Custom_Dirs/spices" and not entry.get("original_path"):
        return str(home / ".config/cinnamon/spices")
    return entry.get("original_path") or ""


def check_destination(value):
    path = Path(value)
    if not path.is_absolute() or not path.name or ".." in path.parts:
        raise ValueError("Enter an absolute destination folder path.")
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink():
            raise ValueError(f"Destination contains a symbolic link: {ancestor}")
    for ancestor in (path, *path.parents):
        if ancestor.exists():
            if not ancestor.is_dir() or not os.access(ancestor, os.W_OK | os.X_OK):
                raise ValueError(f"Destination is not writable: {ancestor}")
            return path
    raise ValueError("Destination is not accessible.")


def validate_folder_plan(entries, plan):
    known = {entry["archive_path"] for entry in entries}
    destinations = set()
    for key, destination in plan.items():
        if key not in known:
            raise ValueError(f"Unknown custom folder: {key}")
        path = check_destination(destination)
        if path in destinations:
            raise ValueError(f"Multiple folders use the same destination: {path}")
        destinations.add(path)
