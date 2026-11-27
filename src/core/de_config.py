import os, shutil, subprocess, json, glob, tempfile
from gi.repository import GLib
from savedesktop.globals import *
from savedesktop.core.custom_dirs import enabled_folders, original_path, save_custom_folders, read_folder_metadata, validate_folder_plan
from savedesktop.core.backup_input import publish_directory, restore_file, restore_tar, restore_tree
from savedesktop.core.restore_completion import affects_cinnamon_shell
from savedesktop.core.wallpaper import save_wallpaper, restore_wallpaper
from savedesktop.core.desktop_configuration import NEMO_FILE, save_desktop_configuration, queue_desktop_configuration
from pathlib import Path
from datetime import date

# ------------------------
# Helpers
# ------------------------

def safe_copy(src: str, dst: str):
    if os.path.isfile(src):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        try:
            shutil.copy2(src, dst)
            print(f"[OK] Copied file: {src} → {dst}")
        except Exception as e:
            print(f"[ERR] File copy failed {src} → {dst}: {e}")
    else:
        print(f"[SKIP] File not found: {src}")

def safe_copytree(src: str, dst: str):
    if os.path.isdir(src):
        try:
            shutil.copytree(src, dst, dirs_exist_ok=True, symlinks=True)
            print(f"[OK] Copied dir: {src} → {dst}")
        except Exception as e:
            print(f"[ERR] Dir copy failed {src} → {dst}: {e}")
    else:
        print(f"[SKIP] Directory not found: {src}")

# ------------------------
# Special handlers
# ------------------------

def save_icons():
    xdg_path = f"{home}/.local/share"
    legacy_path = f"{home}"

    if os.path.isdir(f"{xdg_path}/icons"):
        subprocess.run(["tar", "-czf", "General/icon-themes.tgz", "-C", xdg_path, "icons"], check=True)

    if os.path.isdir(f"{legacy_path}/.icons"):
        subprocess.run(["tar", "-czf", "General/icon-themes-legacy.tgz", "-C", legacy_path, ".icons"], check=True)

    print("[OK] Saving icons")

def import_icons(root):
    for name, destination in (
        ("icon-themes.tgz", home / ".local/share/icons"),
        ("icon-themes-legacy.tgz", home / ".icons"),
    ):
        archive = root / name
        if archive.is_file() and not archive.is_symlink():
            restore_tar(archive, destination, single_folder=True)


def save_flatpak_data():
    blacklist = settings["disabled-flatpak-apps-data"]

    cmd = ["tar", "-czf", "Flatpak_Apps/flatpak-apps-data.tgz", "--exclude=*/cache"]

    for app in blacklist:
        cmd.append(f"--exclude={app}")

    cmd.extend(["-C", f"{home}/.var", "app"])

    subprocess.run(cmd, check=True)
    print("[OK] Saving Flatpak app data")

def export_flatpaks(path, output_file, install_type):
    if os.path.exists(path):
        apps = [d for d in os.listdir(path) if os.path.isdir(os.path.join(path, d))]
        with open(output_file, 'w') as f:
            for app in apps:
                if not app in settings["disabled-flatpak-apps-data"]:
                    f.write(f"flatpak install --{install_type} {app} -y\n")

def create_flatpak_list():
    os.makedirs("Flatpak_Apps", exist_ok=True)
    export_flatpaks('/var/lib/flatpak/app/', 'Flatpak_Apps/installed_flatpaks.sh', 'system')
    export_flatpaks(f'{home}/.local/share/flatpak/app', 'Flatpak_Apps/installed_user_flatpaks.sh', 'user')
    print("[OK] Saving Flatpak app list")

def create_flatpak_autostart(root):
    lists = ("installed_flatpaks.sh", "installed_user_flatpaks.sh")
    data = ("flatpak-apps-data.tgz",)
    names = (lists if settings["save-installed-flatpaks"] else ()) + (data if settings["save-flatpak-data"] else ())
    flatpak_section = root / "Flatpak_Apps"
    sources = flatpak_section if flatpak_section.is_dir() and not flatpak_section.is_symlink() else root
    legacy_app = root / "app"
    files = [sources / name for name in names if (sources / name).is_file() and not (sources / name).is_symlink()]
    copy_legacy = settings["save-flatpak-data"] and legacy_app.is_dir() and not legacy_app.is_symlink()
    if not files and not copy_legacy:
        return
    workspace = Path(CACHE) / "workspace"
    with tempfile.TemporaryDirectory(prefix="flatpak-import-", dir=CACHE) as directory:
        staging = Path(directory) / "new"
        staging.mkdir()
        shutil.copy2(Path(__file__).with_name("flatpaks_installer.py"), staging)
        shutil.copy2(Path(__file__).with_name("backup_input.py"), staging)
        destination = staging / "Flatpak_Apps" if sources is flatpak_section else staging
        for source in files:
            restore_file(source, destination / source.name)
        if copy_legacy:
            restore_tree(legacy_app, staging / "app")
        with (staging / "flatpak-prefs.json").open("w") as prefs:
            json.dump({
                "keep-flatpaks": settings["keep-flatpaks"],
                "disabled-flatpaks": settings["disabled-flatpak-apps-data"],
                "install-flatpaks": settings["save-installed-flatpaks"],
                "copy-data": settings["save-flatpak-data"],
            }, prefs)
        publish_directory(staging, workspace, Path(directory) / "previous")

    # Create an autostart file for post-login Flatpak installation
    os.makedirs(f"{home}/.config/autostart", exist_ok=True)
    if not os.path.exists(f"{DATA}/savedesktop-synchronization.sh"):
        with open(f"{home}/.config/autostart/{APP_ID}.Flatpak.desktop", "w") as f:
            f.write(
                "[Desktop Entry]\n"
                f"Name={APP_NAME} (Flatpak Apps installer)\n"
                "Type=Application\n"
                f"Exec=sh -c \"flatpak run {APP_ID} --show-flatpaks-installer & python3 -u {CACHE}/workspace/flatpaks_installer.py > {CACHE}/workspace/log.pipe 2>&1\""
            )

    print("[OK] Created Flatpak autostart")
    return True

def save_bookmarks():
    safe_copytree(f"{home}/.config/gtk-4.0", "General/gtk-4.0")
    if settings["save-bookmarks"]:
        safe_copy(f"{home}/.config/gtk-3.0/bookmarks", "General/gtk-3.0/bookmarks")

def import_bookmarks(root):
    restore_optional(root / "gtk-4.0", home / ".config/gtk-4.0")
    source = root / "gtk-3.0/bookmarks"
    if settings["save-bookmarks"] and source.is_file():
        restore_file(source, home / ".config/gtk-3.0/bookmarks")


# ------------------------
# Desktop folder
# ------------------------

desktop_dir = Path(GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DESKTOP))

def save_desktop_folder():
    if desktop_dir == home:
        raise ValueError("The desktop location is the whole home directory.")
    subprocess.run([
        "tar", "-czf", "General/desktop-folder.tgz",
        "-C", str(desktop_dir.parent),
        desktop_dir.name
    ], check=True)

    if not environment or environment["de_name"] != "Cinnamon":
        safe_copytree(f"{home}/.local/share/gvfs-metadata", "General/gvfs-metadata")
    print("[OK] Saving Desktop folder")

def import_desktop_folder(root):
    archive = root / "desktop-folder.tgz"
    if archive.is_file() and not archive.is_symlink():
        if desktop_dir == home:
            raise ValueError("The desktop location is the whole home directory.")
        restore_tar(archive, desktop_dir, single_folder=True)
        if not environment or environment["de_name"] != "Cinnamon":
            restore_optional(root / "gvfs-metadata", home / ".local/share/gvfs-metadata")
        print("[OK] Restored Desktop folder")


# ------------------------
# GENERAL ITEMS (DATA MODEL)
# ------------------------

GENERAL_ITEMS = {

    "fonts": {
        "key": "save-fonts",
        "dirs": [
            (f"{home}/.fonts", ".fonts"),
            (f"{home}/.local/share/fonts", "fonts"),
        ],
    },

    "themes": {
        "key": "save-themes",
        "dirs": [
            (f"{home}/.themes", ".themes"),
            (f"{home}/.local/share/themes", "themes"),
        ],
    },

    "backgrounds": {
        "key": "save-backgrounds",
        "dirs": [
            (f"{home}/.local/share/backgrounds", "backgrounds"),
            (f"{home}/.local/share/wallpapers", "wallpapers"),
        ],
    },

    "extensions": {
        "key": "save-extensions",
        "dirs": [
            (f"{home}/.local/share/gnome-shell", "gnome-shell"),
            (f"{home}/.local/share/cinnamon", "cinnamon"),
            (f"{home}/.local/share/plasma", "plasma"),
        ],
    },

}

# ------------------------
# SPECIAL ITEMS (CUSTOM LOGIC)
# ------------------------

SPECIAL_ITEMS = {

    "icons": {
        "key": "save-icons",
        "save": save_icons,
        "import": import_icons,
    },

    "bookmarks": {
        "key": "save-bookmarks",
        "save": save_bookmarks,
        "import": import_bookmarks,
    },

    "flatpaks": {
        "key": "save-installed-flatpaks",
        "save": create_flatpak_list,
        "import": create_flatpak_autostart,
    },

    "flatpak-data": {
        "key": "save-flatpak-data",
        "save": save_flatpak_data,
        "import": create_flatpak_autostart,
    },

    "desktop": {
        "key": "save-desktop-folder",
        "save": save_desktop_folder,
        "import": import_desktop_folder,
    },

}

# Directories for KDE Plasma DE
KDE_DIRS_SAVE = [
    (f"{home}/.config/plasmarc", "xdg-config/plasmarc"),
    (f"{home}/.config/plasmashellrc", "xdg-config/plasmashellrc"),
    (f"{home}/.config/plasma-org.kde.plasma.desktop-appletsrc", "xdg-config/plasma-org.kde.plasma.desktop-appletsrc"),
    (f"{home}/.config/dolphinrc", "xdg-config/dolphinrc"),
    (f"{home}/.config/gtkrc", "xdg-config/gtkrc"),
    (f"{home}/.config/Kvantum", "xdg-config/Kvantum"),
    (f"{home}/.config/latte", "xdg-config/latte"),
    (f"{home}/.local/share/dolphin", "xdg-data/dolphin"),
    (f"{home}/.local/share/sddm", "xdg-data/sddm"),
    (f"{home}/.local/share/aurorae", "xdg-data/aurorae"),
    (f"{home}/.local/share/plasma-systemmonitor", "xdg-data/plasma-systemmonitor"),
    (f"{home}/.local/share/color-schemes", "xdg-data/color-schemes"),
]

KDE_DIRS_IMPORT = [
    ("xdg-config", f"{home}/.config"),
    ("xdg-data", f"{home}/.local/share"),
]

# ------------------------
# Save pipeline
# ------------------------

class Save:

    def __init__(self):
        os.makedirs("General", exist_ok=True)
        os.makedirs("DE", exist_ok=True)

        # General dirs
        for item in GENERAL_ITEMS.values():
            if settings[item["key"]]:
                for src, dst in item["dirs"]:
                    safe_copytree(src, os.path.join("General", dst))

        # Special handlers
        for item in SPECIAL_ITEMS.values():
            if settings[item["key"]]:
                item["save"]()

        self.folders = []
        if settings["enable-custom-dirs"]:
            selected_folders = enabled_folders(settings)
            self.folders = save_custom_folders(selected_folders, Path("Custom_Dirs"), flatpak=flatpak)
            known_paths = settings["custom-dir-original-paths"]
            for source, entry in zip(selected_folders, self.folders):
                original = entry["original_path"] or known_paths.get(source)
                if original:
                    entry["original_path"] = original
                    path = Path(original)
                    if path != home and path.is_relative_to(home):
                        entry["home_relative"] = str(path.relative_to(home))
            Path("custom-dirs.json").write_text(
                json.dumps({"version": 1, "folders": self.folders}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        # DE configuration
        if environment:
            if environment["de_name"] == "KDE Plasma":
                print("Saving KDE Plasma configuration...")
                os.makedirs("DE/xdg-config", exist_ok=True)
                os.makedirs("DE/xdg-data", exist_ok=True)
                self.save_kde_config_data_dirs()
                for src, dst in KDE_DIRS_SAVE:
                    if os.path.isfile(src):
                        safe_copy(src, os.path.join("DE", dst))
                    else:
                        safe_copytree(src, os.path.join("DE", dst))
            else:
                print(f"Saving environment-specific config for: {environment['de_name']}")
                for src, dst in environment["dirs"]:
                    safe_copytree(src, os.path.join("DE", dst))
        else:
            print(f"[WARN] Unknown DE: {environment_key}")

        self.save_dconf()
        if environment and environment["de_name"] == "Cinnamon":
            save_desktop_configuration(home / ".config/nemo")
            if settings["save-backgrounds"]:
                save_wallpaper(Path("General/dconf-settings.ini"), self.folders)

    def save_dconf(self):
        with open("General/dconf-settings.ini", "wb") as dump:
            subprocess.run(["dconf", "dump", "/"], stdout=dump, check=True)
        print("[OK] Saved dconf")

    def save_kde_config_data_dirs(self):
        # Target dirs
        target_base_dir = "./DE"
        mapping = [
            (f"{home}/.config", Path(target_base_dir) / "xdg-config"),
            (f"{home}/.local/share", Path(target_base_dir) / "xdg-data"),
        ]

        for src_dir, dst_path in mapping:
            # Creating destination dir
            dst_path.mkdir(parents=True, exist_ok=True)

            # Find folders begining with 'k' or 'K'
            for path_str in glob.glob(f"{src_dir}/[kK]*"):
                src_path = Path(path_str)

                # Ignore kdeconnect
                if src_path.name.lower() == "kdeconnect":
                    continue

                target = dst_path / src_path.name

                # Copying folders and files
                if src_path.is_dir():
                    if target.exists():
                        shutil.rmtree(target)
                    shutil.copytree(src_path, target)
                else:
                    shutil.copy2(src_path, target)

# ------------------------
# Import pipeline
# ------------------------

def restore_optional(source, destination, *, exclude=()):
    if source.is_dir():
        restore_tree(source, destination, exclude=exclude)


class Import:

    def __init__(self, root, options, dump, wallpaper=""):
        general = root / "General" if (root / "General").is_dir() else root
        folders = read_folder_metadata(root)
        plan = options.get("folders", {})
        plan = validate_folder_plan(folders, plan, probe_portal=True)
        cinnamon = bool(environment and environment["de_name"] == "Cinnamon")
        restore_background = options.get("wallpaper", settings["save-backgrounds"])
        restore_contents = options.get("desktop_contents", settings["save-desktop-folder"])
        restore_configuration = options.get("desktop_configuration", True)
        self.cinnamon_shell = cinnamon and affects_cinnamon_shell(dump)
        self.nemo_pending = False
        self.flatpaks_pending = False
        protected = []
        if not restore_contents and desktop_dir != home:
            protected.append(desktop_dir)
        if cinnamon:
            protected.append(home / ".local/share/gvfs-metadata")
            protected.append(home / ".config/nemo" / NEMO_FILE)

        host_destinations = {key: original_path(value, flatpak=flatpak) for key, value in plan.items()}
        for location in host_destinations.values():
            if location and any(Path(location).is_relative_to(path) or path.is_relative_to(location)
                                for path in protected):
                raise ValueError(f"Custom restore destination overlaps a protected desktop location: {location}")

        # General dirs (reverse copy)
        for name, item in GENERAL_ITEMS.items():
            enabled = restore_background if name == "backgrounds" else settings[item["key"]]
            if enabled:
                for src, dst in item["dirs"]:
                    source = general / dst
                    if cinnamon and name in ("themes", "extensions") and source.is_dir():
                        self.cinnamon_shell = True
                    restore_optional(source, src)

        for name, item in SPECIAL_ITEMS.items():
            if name in ("flatpaks", "flatpak-data"):
                continue
            enabled = restore_contents if name == "desktop" else settings[item["key"]]
            if enabled:
                item["import"](general)
        for source, destination in plan.items():
            restore_tree(root / source, Path(destination))
            location = host_destinations[source]
            spices = home / ".config/cinnamon/spices"
            if cinnamon and location and (Path(location).is_relative_to(spices) or
                                           spices.is_relative_to(location)):
                self.cinnamon_shell = True
            print(f"[OK] Restored custom folder: {destination}")

        # DE configuration
        if environment:
            if environment["de_name"] == "KDE Plasma":
                print("Importing KDE Plasma configuration...")
                for src, dst in KDE_DIRS_IMPORT:
                    source = root / "DE" / src if (root / "DE").is_dir() else root / src
                    restore_optional(source, dst)
            else:
                print(f"Importing environment-specific config for: {environment['de_name']}")
                for src, dst in environment["dirs"]:
                    source = root / "DE" / dst if (root / "DE").is_dir() else root / dst
                    excluded = (NEMO_FILE,) if cinnamon and dst == "nemo" else ()
                    if cinnamon and dst in ("cinnamon", ".cinnamon") and source.is_dir():
                        self.cinnamon_shell = True
                    restore_optional(source, src, exclude=excluded)
        else:
            print(f"[WARN] Unknown DE: {environment_key}")

        if wallpaper and restore_background:
            restored = restore_wallpaper(root, wallpaper, folders, plan, host_destinations, home, DATA)
            if restored:
                dump += "\n" + restored
        self.import_dconf(dump)
        if settings["save-installed-flatpaks"] or settings["save-flatpak-data"]:
            self.flatpaks_pending = bool(create_flatpak_autostart(root))
        if cinnamon and restore_configuration:
            from gi.repository import Gdk
            display = Gdk.Display.get_default() or Gdk.Display.open(None)
            geometries = [monitor.get_geometry() for monitor in display.get_monitors()] if display else []
            monitors = [(geometry.width, geometry.height) for geometry in geometries]
            self.nemo_pending = queue_desktop_configuration(root, home / ".config", APP_ID, monitors)

    def import_dconf(self, dump):
        env = os.environ.copy()
        payload = dump.encode("utf-8")
        if flatpak:
            subprocess.run(["dconf", "load", "/"], input=payload, env=env, check=True)
        else:
            with tempfile.TemporaryDirectory(prefix="dconf-profile-") as directory:
                profile = Path(directory) / "profile"
                profile.write_text("user-db:user\n")
                env["DCONF_PROFILE"] = str(profile)
                subprocess.run(["dconf", "load", "/"], input=payload, env=env, check=True)
        print("[OK] Imported dconf")
