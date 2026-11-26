"""Read SaveDesktop backups into a private staging directory."""
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import tarfile
import tempfile
import zipfile


def member_path(name):
    path = PurePosixPath(name)
    if not path.parts or path.is_absolute() or ".." in path.parts or "\\" in name or ":" in path.parts[0]:
        raise ValueError(f"Unsafe backup path: {name}")
    return path


def extract_zip(source, staging, password):
    with zipfile.ZipFile(source) as archive:
        seen = set()
        for entry in archive.infolist():
            if entry.is_dir() and entry.filename == "./":
                continue
            name = member_path(entry.filename)
            if name in seen:
                raise ValueError(f"Duplicate backup entry: {name}")
            seen.add(name)
            target = staging.joinpath(*name.parts)
            mode = stat.S_IFMT(entry.external_attr >> 16)
            if mode not in (0, stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK):
                raise ValueError(f"Unsupported backup entry: {name}")
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            # Extract links separately below, after all regular paths have been created.
            if mode == stat.S_IFLNK:
                target.parent.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as output:
                if entry.flag_bits & 1:
                    if not password:
                        raise ValueError("This ZIP backup requires a password.")
                    process = subprocess.run(
                        ["7z", "x", "-so", "-spd", f"-p{password}", "--", str(source), entry.filename],
                        stdout=output, stderr=subprocess.PIPE, check=False,
                    )
                    if process.returncode or output.tell() != entry.file_size:
                        raise ValueError("Cannot decrypt ZIP entry. Check the backup password and file integrity.")
                else:
                    with archive.open(entry) as payload:
                        shutil.copyfileobj(payload, output)
        for entry in archive.infolist():
            if stat.S_IFMT(entry.external_attr >> 16) == stat.S_IFLNK:
                if entry.flag_bits & 1:
                    raise ValueError("Encrypted symbolic links are unsupported.")
                if entry.file_size > 4096:
                    raise ValueError(f"Invalid symbolic link: {entry.filename}")
                link = archive.read(entry).decode("utf-8")
                member_path(link)
                target = staging.joinpath(*member_path(entry.filename).parts)
                if any(parent.is_symlink() for parent in target.parents if parent != staging):
                    raise ValueError(f"Symbolic-link parent in backup: {entry.filename}")
                target.symlink_to(link)


def extract_tar(source, staging):
    with tarfile.open(source, "r:*") as archive:
        for item in archive:
            if item.name in (".", "./") and item.isdir():
                continue
            member_path(item.name)
            if item.issym() or item.islnk():
                member_path(item.linkname)
            if not (item.isfile() or item.isdir() or item.issym()):
                raise ValueError(f"Unsupported TAR entry: {item.name}")
            archive.extract(item, staging, filter="data")


def copy_backup(source, staging):
    for entry in source.rglob("*"):
        if entry.is_symlink():
            member_path(str(entry.readlink()))
        elif not (entry.is_file() or entry.is_dir()):
            raise ValueError(f"Unsupported backup entry: {entry.name}")
    shutil.copytree(source, staging, dirs_exist_ok=True, symlinks=True)


def backup_root(staging):
    # A manually created ZIP often contains one outer directory.
    root = staging
    candidates = [entry for entry in staging.iterdir() if entry.name not in ("__MACOSX", ".DS_Store")]
    if len(candidates) == 1 and candidates[0].is_dir() and not candidates[0].is_symlink() and candidates[0].name != "General":
        root = candidates[0]
    general = root / "General"
    dump = (general if general.is_dir() else root) / "dconf-settings.ini"
    if any((root / name).is_symlink() for name in ("General", "DE", "Custom_Dirs", "Flatpak_Apps")):
        raise ValueError("Backup sections cannot be symbolic links.")
    if not dump.is_file() or dump.is_symlink():
        raise ValueError("The selected backup is missing dconf-settings.ini.")
    return root


@contextmanager
def prepare_backup(source, password=None, *, parent=None):
    source = Path(source).absolute()
    with tempfile.TemporaryDirectory(prefix="restore-", dir=parent) as temporary:
        staging = Path(temporary)
        try:
            if source.is_dir():
                copy_backup(source, staging)
            elif source.name.lower().endswith(".zip"):
                extract_zip(source, staging, password)
            elif source.name.lower().endswith((".tar.gz", ".tgz")):
                extract_tar(source, staging)
            else:
                raise ValueError("Select a ZIP, TAR.GZ or SaveDesktop backup folder.")
            root = backup_root(staging)
        except (OSError, zipfile.BadZipFile, tarfile.TarError, UnicodeError, NotImplementedError, RuntimeError) as error:
            raise ValueError(f"Cannot read backup: {error}") from error
        yield root


def restore_tree(source, destination):
    """Merge files without following existing links in destination directories."""
    source = Path(source)
    if source.is_symlink():
        raise ValueError(f"Restore source is a symbolic link: {source}")
    destination = Path(destination)
    if any(path.is_symlink() for path in (destination, *destination.parents)):
        raise ValueError(f"Restore destination is a symbolic link: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    for entry in Path(source).iterdir():
        target = destination / entry.name
        if entry.is_symlink() and target.is_symlink() and entry.readlink() == target.readlink():
            continue
        if target.is_symlink():
            raise ValueError(f"Restore destination is a symbolic link: {target}")
        if entry.is_symlink():
            link = entry.readlink()
            member_path(str(link))
            target.symlink_to(link)
        elif entry.is_dir():
            restore_tree(entry, target)
        else:
            restore_file(entry, target)


def restore_file(source, destination):
    destination = Path(destination)
    if Path(source).is_symlink() or any(path.is_symlink() for path in (destination, *destination.parents)):
        raise ValueError(f"Symbolic link in file restore: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as output:
        temporary = Path(output.name)
    try:
        shutil.copy2(source, temporary)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def restore_tar(source, destination, *, single_folder=False):
    with tempfile.TemporaryDirectory(prefix="restore-tar-") as temporary:
        staging = Path(temporary)
        extract_tar(source, staging)
        if single_folder:
            sections = list(staging.iterdir())
            if len(sections) != 1 or not sections[0].is_dir() or sections[0].is_symlink():
                raise ValueError("Expected a single directory in the nested archive.")
            staging = sections[0]
        restore_tree(staging, destination)
