"""Cinnamon background settings and their associated image."""
from gettext import translation
import os
import json
from pathlib import Path
import re
import shutil
import sys
from urllib.parse import unquote, urlsplit

from gi.repository import GLib

from savedesktop.core.custom_dirs import check_destination, original_path

_ = translation("savedesktop", localedir=os.getenv("SAVEDESKTOP_LOCALE"), fallback=True).gettext

SECTION = "[org/cinnamon/desktop/background]"
SECTION_PREFIX = "[org/cinnamon/desktop/background/"


def split_wallpaper(dump):
    other, wallpaper = [], []
    block = other
    for line in dump.splitlines(keepends=True):
        header = line.strip()
        if header.startswith("["):
            block = wallpaper if header == SECTION or header.startswith(SECTION_PREFIX) else other
        block.append(line)
    return "".join(other), "".join(wallpaper)


def image_path(settings):
    match = re.search(r"(?m)^picture-uri=(.+)$", settings)
    if match:
        try:
            uri = GLib.Variant.parse(GLib.VariantType.new("s"), match[1], None, None).unpack()
            parsed = urlsplit(uri)
            if parsed.scheme == "file" and parsed.netloc in ("", "localhost"):
                path = Path(unquote(parsed.path))
                if path.is_absolute() and ".." not in path.parts:
                    return path
        except (GLib.Error, ValueError):
            pass
    return None


def save_wallpaper(dump, folders):
    other_settings, settings = split_wallpaper(dump.read_text(encoding="utf-8"))
    image = image_path(settings)
    if image is None:
        return
    matches = []
    for folder in folders:
        original = folder.get("original_path")
        if original and image.is_relative_to(original):
            relative = image.relative_to(original)
            if (Path(folder["archive_path"]) / relative).is_file():
                matches.append((len(Path(original).parts), folder["archive_path"], str(relative)))
    if matches:
        depth, folder, relative = max(matches)
        location = {"folder": folder, "relative": relative}
    else:
        try:
            source = image.open("rb")
        except FileNotFoundError:
            print(_("Wallpaper image is missing: {path}").format(path=image), file=sys.stderr)
            return
        except PermissionError:
            print(_("No permission to read wallpaper image: {path}").format(path=image), file=sys.stderr)
            return
        except OSError as error:
            print(_("Cannot read wallpaper image {path}: {reason}").format(path=image, reason=error.strerror), file=sys.stderr)
            return
        with source:
            directory = Path("General/Wallpaper")
            directory.mkdir(exist_ok=True)
            with (directory / image.name).open("wb") as destination:
                shutil.copyfileobj(source, destination)
        location = {"file": image.name}
    Path("General/Wallpaper.json").write_text(json.dumps(location, ensure_ascii=False), encoding="utf-8")


def restore_wallpaper(root, settings, folders, plan, home, flatpak, data_dir):
    source = image_path(settings)
    if source is None:
        if "picture-uri=" in settings:
            print(_("Wallpaper was kept because the saved image is unavailable."), file=sys.stderr)
            return ""
        return settings

    path = None
    copied = False
    metadata = root / "General/Wallpaper.json"
    if metadata.is_file() and not metadata.is_symlink():
        data = json.loads(metadata.read_text(encoding="utf-8"))
        if "file" in data:
            name = data["file"]
            if not isinstance(name, str) or Path(name).name != name or name in ("", ".", ".."):
                raise ValueError("Invalid wallpaper file in backup.")
            image = root / "General/Wallpaper" / name
            if image.is_file() and not image.is_symlink():
                target = home / ".local/share/backgrounds/SaveDesktopPlus"
                try:
                    check_destination(str(target))
                except ValueError:
                    target = check_destination(str(Path(data_dir) / "wallpapers"))
                from savedesktop.core.backup_input import restore_file
                restore_file(image, target / name)
                path = target / name
        elif data.get("folder") in plan:
            relative = Path(data["relative"])
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Invalid wallpaper path in backup.")
            access = Path(plan[data["folder"]])
            host = original_path(str(access), flatpak=flatpak)
            if host and (access / relative).is_file():
                path = Path(host) / relative
                copied = True
    else:
        # Legacy backups can be mapped if their selected folder name and file agree.
        for folder in folders:
            key = folder["archive_path"]
            if key in plan:
                original = folder.get("original_path")
                if original and source.is_relative_to(original):
                    relative = source.relative_to(original)
                elif not original and folder["name"] in source.parts[:-1]:
                    index = source.parts.index(folder["name"])
                    relative = Path(*source.parts[index + 1:])
                else:
                    continue
                access = Path(plan[key])
                host = original_path(str(access), flatpak=flatpak)
                if host and (access / relative).is_file():
                    path = Path(host) / relative
                    copied = True
                    break
        if path is None:
            parts = source.parts
            if parts[1:2] == ("home",) and len(parts) > 3:
                path = home.joinpath(*parts[3:])
            elif parts[1:3] == ("var", "home") and len(parts) > 4:
                path = home.joinpath(*parts[4:])
            else:
                path = source

    if path is None or not (copied or path.is_file()):
        print(_("Wallpaper was kept because the saved image is unavailable."), file=sys.stderr)
        return ""
    uri = GLib.Variant("s", path.as_uri()).print_(False)
    return re.sub(r"(?m)^picture-uri=.+$", lambda _: f"picture-uri={uri}", settings, count=1)
