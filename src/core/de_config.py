import os, shutil, subprocess, json, glob
from gi.repository import GLib
from savedesktop.globals import *
from savedesktop.core.custom_dirs import enabled_folders, save_custom_folders, read_folder_metadata, validate_folder_plan
from savedesktop.core.backup_input import restore_file, restore_tar, restore_tree
from savedesktop.core.wallpaper import save_wallpaper, restore_wallpaper
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

def import_icons():
    if os.path.exists("General"):
        if os.path.exists("General/icon-themes.tgz"):
            restore_tar("General/icon-themes.tgz", home / ".local/share/icons", single_folder=True)

        if os.path.exists("General/icon-themes-legacy.tgz"):
            restore_tar("General/icon-themes-legacy.tgz", home / ".icons", single_folder=True)

        print("[OK] Importing icons")
    else:
        if os.path.exists("icon-themes.tgz"):
            restore_tar("icon-themes.tgz", home / ".local/share/icons", single_folder=True)

        if os.path.exists("icon-themes-legacy.tgz"):
            restore_tar("icon-themes-legacy.tgz", home / ".icons", single_folder=True)

        print("[OK] Importing icons")

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

def create_flatpak_autostart():
    if not any(Path(name).exists() for name in (
        "Flatpak_Apps", "installed_flatpaks.sh", "installed_user_flatpaks.sh", "flatpak-apps-data.tgz", "app",
    )):
        return
    workspace = Path(CACHE) / "workspace"
    workspace.mkdir(exist_ok=True)
    shutil.copy2(Path(app_prefix) / "savedesktop/core/flatpaks_installer.py", workspace)
    shutil.copy2(Path(app_prefix) / "savedesktop/core/backup_input.py", workspace)
    if (workspace / "Flatpak_Apps").exists():
        shutil.rmtree(workspace / "Flatpak_Apps")
    if Path("Flatpak_Apps").is_dir():
        shutil.copytree("Flatpak_Apps", workspace / "Flatpak_Apps", symlinks=True)
    for name in ("installed_flatpaks.sh", "installed_user_flatpaks.sh", "flatpak-apps-data.tgz", "app"):
        target = workspace / name
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        else:
            target.unlink(missing_ok=True)
        source = Path(name)
        if source.is_symlink():
            raise ValueError(f"Unexpected symbolic link: {name}")
        if source.is_dir():
            shutil.copytree(source, target, symlinks=True)
        elif source.is_file():
            shutil.copy2(source, target)

    # Create a JSON file with the user's preferences for Flatpak apps
    with open(f"{CACHE}/workspace/flatpak-prefs.json", "w") as fl:
        json.dump({"keep-flatpaks": settings["keep-flatpaks"], "disabled-flatpaks": settings["disabled-flatpak-apps-data"], "install-flatpaks": settings["save-installed-flatpaks"], "copy-data": settings["save-flatpak-data"]}, fl)

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

def save_bookmarks():
    safe_copytree(f"{home}/.config/gtk-4.0", "General/gtk-4.0")
    if settings["save-bookmarks"]:
        safe_copy(f"{home}/.config/gtk-3.0/bookmarks", "General/gtk-3.0/bookmarks")

def import_bookmarks():
    root = Path("General") if Path("General").is_dir() else Path(".")
    restore_optional(root / "gtk-4.0", home / ".config/gtk-4.0")
    source = root / "gtk-3.0/bookmarks"
    if settings["save-bookmarks"] and source.is_file():
        restore_file(source, home / ".config/gtk-3.0/bookmarks")


# ------------------------
# Desktop folder
# ------------------------

desktop_dir = Path(GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DESKTOP))

def save_desktop_folder():
    subprocess.run([
        "tar", "-czf", "General/desktop-folder.tgz",
        "-C", str(desktop_dir.parent),
        desktop_dir.name
    ], check=True)

    safe_copytree(f"{home}/.local/share/gvfs-metadata", "General/gvfs-metadata")
    print("[OK] Saving Desktop folder")

def import_desktop_folder():
    root = Path("General") if Path("General").is_dir() else Path(".")
    archive = root / "desktop-folder.tgz"
    if archive.is_file():
        restore_tar(archive, desktop_dir, single_folder=True)
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
        if environment and environment["de_name"] == "Cinnamon" and settings["save-backgrounds"]:
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

def restore_optional(source, destination):
    if Path(source).is_dir():
        restore_tree(source, destination)


class Import:

    def __init__(self, options, wallpaper=""):
        folders = read_folder_metadata(Path.cwd())
        plan = options.get("folders", {})
        validate_folder_plan(folders, plan)
        # General dirs (reverse copy)
        for item in GENERAL_ITEMS.values():
            if settings[item["key"]]:
                for src, dst in item["dirs"]:
                    if os.path.exists("General"):
                        restore_optional(os.path.join("General", dst), src)
                    else:
                        restore_optional(dst, src)

        # Special handlers
        for item in SPECIAL_ITEMS.values():
            if settings[item["key"]]:
                item["import"]()

        for source, destination in plan.items():
            restore_tree(Path(source), Path(destination))
            print(f"[OK] Restored custom folder: {destination}")

        # DE configuration
        if environment:
            if environment["de_name"] == "KDE Plasma":
                print("Importing KDE Plasma configuration...")
                for src, dst in KDE_DIRS_IMPORT:
                    if os.path.exists("DE"):
                        restore_optional(os.path.join("DE", src), dst)
                    else:
                        restore_optional(src, dst)
            else:
                print(f"Importing environment-specific config for: {environment['de_name']}")
                for src, dst in environment["dirs"]:
                    if os.path.exists("DE"):
                        restore_optional(os.path.join("DE", dst), src)
                    else:
                        restore_optional(dst, src)
        else:
            print(f"[WARN] Unknown DE: {environment_key}")

        if wallpaper and options.get("wallpaper", settings["save-backgrounds"]):
            restored = restore_wallpaper(wallpaper, folders, plan, home, flatpak, DATA)
            if restored:
                dump = Path("General/dconf-settings.ini") if Path("General").is_dir() else Path("dconf-settings.ini")
                with dump.open("a", encoding="utf-8") as file:
                    file.write("\n" + restored)
        self.import_dconf()

    def import_dconf(self):
        import subprocess

        path = (
            "./General/dconf-settings.ini"
            if os.path.exists("General")
            else "./dconf-settings.ini"
        )

        env = os.environ.copy()

        if not flatpak:
            with open("temporary-profile", "w") as profile:
                profile.write("user-db:user\n")
            env["DCONF_PROFILE"] = os.path.abspath("temporary-profile")

        with open(path, "rb") as source:
            subprocess.run(
                ["dconf", "load", "/"],
                stdin=source,
                env=env,
                check=True
            )

        print("[OK] Imported dconf")
