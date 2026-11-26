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


class CustomFolderCopyError(OSError):
    pass


def save_custom_folders(folders, target, *, flatpak):
    target.mkdir(exist_ok=True)
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
