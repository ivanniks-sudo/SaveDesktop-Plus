import os, sys, shutil, subprocess, argparse, re, json, signal, tempfile
from gettext import translation
from pathlib import Path
from savedesktop.globals import *
from savedesktop.core.password_store import PasswordStore
from savedesktop.core.de_config import Save, Import
from savedesktop.core.backup_input import HOME_PATH, ZipPath, prepare_backup, publish_directory
from savedesktop.core.wallpaper import split_wallpaper
from savedesktop.core.operation_notices import warn
from savedesktop.core.restore_completion import report_completion
from savedesktop.core.desktop_configuration import filter_desktop_settings

_ = translation("savedesktop", localedir=os.getenv("SAVEDESKTOP_LOCALE"), fallback=True).gettext

TEMP_CACHE = f"{CACHE}/workspace"
temp_file = f"{CACHE}/temp_file"

# Get password entered in the "Create a new password" dialog from the temporary file
def get_password():
    if os.path.exists(temp_file):
        with open(temp_file) as tmp:
            return tmp.read().strip()
    elif os.path.exists(f"{DATA}/password"):
        p = PasswordStore()
        return p.password

# Remove above temporary file
def remove_temp_file():
    if os.path.exists(temp_file):
        os.remove(temp_file)

password = get_password()

class Create:
    def __init__(self, dir_path):
        output = Path(dir_path).absolute()
        previous = Path.cwd()
        with tempfile.TemporaryDirectory(prefix="backup-", dir=CACHE) as temporary:
            try:
                os.chdir(temporary)
                Save()
                if output.name.lower().endswith(".zip"):
                    self._create_archive(output)
                else:
                    self._publish_folder(output, Path(temporary))
            finally:
                os.chdir(previous)
                remove_temp_file()
        print("Configuration saved successfully.")

    def _publish_folder(self, output, source):
        with tempfile.TemporaryDirectory(prefix=".save-desktop-", dir=output.parent) as temporary:
            pending = Path(temporary) / "new"
            previous = Path(temporary) / "previous"
            shutil.copytree(source, pending, symlinks=True)
            publish_directory(pending, output, previous)

    def _create_archive(self, output):
        with tempfile.TemporaryDirectory(prefix="zip-", dir=CACHE) as temporary:
            archive = Path(temporary) / "backup.zip"
            cmd = ["7z", "a", "-snl", "-mx=3"]
            if settings["enable-encryption"] or os.path.exists(f"{CACHE}/pb"):
                if password:
                    cmd += ["-mem=AES256", f"-p{password}"]
            result = subprocess.run([*cmd, str(archive), "."], capture_output=True, text=True)
            if result.returncode:
                raise OSError(f"Cannot create ZIP: {result.stderr or result.stdout}")
            # Publish from the same filesystem as the final archive.
            with tempfile.NamedTemporaryFile(prefix=".save-desktop-", dir=output.parent, delete=False) as file:
                pending = Path(file.name)
            try:
                shutil.copyfile(archive, pending)
                pending.replace(output)
            finally:
                pending.unlink(missing_ok=True)


class Unpack:
    def __init__(self, dir_path, options=None):
        options = options or {}
        try:
            with prepare_backup(dir_path, password, parent=CACHE) as root:
                general = root / "General" if (root / "General").is_dir() else root
                dump = self._sanitize_dconf_dump((general / "dconf-settings.ini").read_text(encoding="utf-8"))
                wallpaper = ""
                if environment and environment["de_name"] == "Cinnamon":
                    dump, wallpaper = split_wallpaper(dump)
                    dump = filter_desktop_settings(dump, options.get("desktop_configuration", True))
                unsafe_portal_value = re.compile(r"/run/(?:flatpak/doc|user/[0-9]+/doc)(?=/|['\"])")
                retained = [line for line in dump.splitlines(keepends=True)
                            if not ("=" in line and unsafe_portal_value.search(line))]
                if len("".join(retained)) != len(dump):
                    warn(_("Some settings were skipped because they referenced temporary Document Portal paths."))
                dump = "".join(retained)
                dump = HOME_PATH.sub(lambda _: str(home).encode("utf-8"), dump.encode("utf-8")).decode("utf-8")
                if isinstance(root, ZipPath):
                    root.backup.home = home
                else:
                    self._replace_home_in_files(root, home)
                restore = Import(root, options, dump, wallpaper)
        finally:
            remove_temp_file()
        report_completion({
            "cinnamon_shell": restore.cinnamon_shell,
            "nemo_pending": restore.nemo_pending,
            "flatpaks_pending": restore.flatpaks_pending,
        })
        print("Configuration imported successfully.")

    def _sanitize_dconf_dump(self, dump):
        lines = []
        skip_block = False
        for line in dump.splitlines(keepends=True):
            if line.startswith("["):
                skip_block = line.startswith((
                    "[org/gnome/portal/filechooser", "[io/github/vikdevelop/SaveDesktop",
                    "[io/github/ivanniks_sudo/SaveDesktopPlus",
                ))
            if not skip_block:
                lines.append(line)
        return "".join(lines)

    # Replace original /home/$USER path with actual path in the dconf-settings.ini file and other XML files
    def _replace_home_in_files(self, root, home, patterns=(".xml", ".ini")):
        # Define a regular expression as binary (prefix "b")
        regex = re.compile(br"""(?:/var)?/home/[^/\s'"\\<>]+(?=/|['"<>\s]|$)""")
        # Convert the target path to bytes in UTF-8 encoding (which is the most common encoding for paths)
        home_bytes = str(home).encode("utf-8")

        for dirpath, _, filenames in os.walk(root):
            for filename in filenames:
                if filename.endswith(patterns):
                    path = os.path.join(dirpath, filename)
                    if os.path.islink(path):
                        continue

                    # Read in binary ("rb")—encoding is ignored, and no error occurs
                    with open(path, "rb") as f:
                        text_bytes = f.read()

                    new_bytes = regex.sub(lambda _: home_bytes, text_bytes)

                    if new_bytes != text_bytes:
                        # Write in binary ("wb")— preserve the original structure
                        with open(path, "wb") as f:
                            f.write(new_bytes)
                        print(f"Updated /home/$USER path in: {path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-c", "--create", help="Create archive", type=str)
    parser.add_argument("-u", "--unpack", help="Unpack archive", type=str)
    parser.add_argument("--restore-options-stdin", action="store_true")
    args = parser.parse_args()

    def cancel(signum, frame):
        raise SystemExit(130)

    signal.signal(signal.SIGTERM, cancel)

    try:
        if args.create:
            Create(args.create)
        elif args.unpack:
            Unpack(args.unpack, json.load(sys.stdin) if args.restore_options_stdin else None)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
