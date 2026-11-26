import os, sys, shutil, subprocess, argparse, re, json, signal, tempfile
from savedesktop.globals import *
from savedesktop.core.password_store import PasswordStore
from savedesktop.core.de_config import Save, Import
from savedesktop.core.backup_input import prepare_backup
from savedesktop.core.wallpaper import split_wallpaper

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
            if output.exists() or output.is_symlink():
                output.rename(previous)
            try:
                pending.rename(output)
            except OSError:
                if previous.exists() or previous.is_symlink():
                    previous.rename(output)
                raise

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
        previous = Path.cwd()
        try:
            with prepare_backup(dir_path, password, parent=CACHE) as root:
                os.chdir(root)
                dump = Path("General/dconf-settings.ini") if Path("General").is_dir() else Path("dconf-settings.ini")
                self._sanitize_dconf_dump(dump)
                wallpaper = ""
                if environment and environment["de_name"] == "Cinnamon":
                    other, wallpaper = split_wallpaper(dump.read_text(encoding="utf-8"))
                    dump.write_text(other, encoding="utf-8")
                self._replace_home_in_files(".", home)
                Import(options or {}, wallpaper)
        finally:
            os.chdir(previous)
            remove_temp_file()
        print("Configuration imported successfully.")

    # Remove the org/gnome/portal/filechooser string from the dconf-settings.ini file
    def _sanitize_dconf_dump(self, path):
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        sanitized = []
        skip_block = False

        for line in lines:
            # Start of problematic schema
            if any(line.startswith(prefix) for prefix in (
                "[org/gnome/portal/filechooser", "[io/github/vikdevelop/SaveDesktop",
                "[io/github/ivanniks_sudo/SaveDesktopPlus",
            )):
                skip_block = True
                continue

            # End of schema block
            if skip_block and line.startswith("["):
                skip_block = False

            if not skip_block:
                sanitized.append(line)

        if sanitized != lines:
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(sanitized)

            print("Excluded application and portal settings from the dconf import")
            
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

    if args.create:
        dir_path = args.create
        Create(dir_path)
    elif args.unpack:
        dir_path = args.unpack
        Unpack(dir_path, json.load(sys.stdin) if args.restore_options_stdin else None)
    else:
        pass
