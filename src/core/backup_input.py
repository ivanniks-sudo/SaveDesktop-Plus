"""Read SaveDesktop backups and safely copy selected entries."""
from collections import defaultdict, deque
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
import errno
import filecmp
import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import zipfile


def member_path(name):
    path = PurePosixPath(name)
    if not path.parts or path.is_absolute() or ".." in path.parts or "\\" in name or ":" in path.parts[0] or "\0" in name:
        raise ValueError(f"Unsafe backup path: {name}")
    return path


def check_link(parent, target, lookup):
    """Resolve a relative target against the whole link graph inside one tree."""
    def components(value):
        text = str(value)
        path = PurePosixPath(text)
        if not text or path.is_absolute() or "\\" in text or "\0" in text or (
            path.parts and ":" in path.parts[0]
        ):
            raise ValueError(f"Unsafe symbolic link target: {text}")
        return path.parts

    resolved = list(parent.parts)
    pending = deque(components(target))
    followed = 0
    while pending:
        part = pending.popleft()
        if part == "..":
            if not resolved:
                raise ValueError(f"Symbolic link leaves the restore tree: {parent} -> {target}")
            resolved.pop()
            continue
        resolved.append(part)
        link = lookup(PurePosixPath(*resolved))
        if link is not None:
            followed += 1
            if followed > 40:
                raise ValueError(f"Symbolic link loop: {parent} -> {target}")
            resolved.pop()
            pending.extendleft(reversed(components(link)))
    return PurePosixPath(*resolved)


HOME_PATH = re.compile(br"""(?:/var)?/home/[^/\s'"\\<>]+(?=/|['"<>\s]|$)""")


class ZipBackup:
    def __init__(self, archive, source, password):
        self.archive = archive
        self.source = source
        self.password = password
        self.home = None
        self.entries = {}
        self.links = {}
        self.children = defaultdict(set)
        directories = set()
        for entry in archive.infolist():
            if entry.is_dir() and entry.filename == "./":
                continue
            name = member_path(entry.orig_filename)
            if name in self.entries:
                raise ValueError(f"Duplicate backup entry: {name}")
            mode = stat.S_IFMT(entry.external_attr >> 16)
            if mode not in (0, stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK) or (
                entry.is_dir() and mode not in (0, stat.S_IFDIR)
            ) or (mode == stat.S_IFDIR and not entry.is_dir()):
                raise ValueError(f"Unsupported backup entry: {name}")
            self.entries[name] = entry
            directories.update(name.parents)
            if entry.is_dir():
                directories.add(name)
            elif mode == stat.S_IFLNK:
                if entry.flag_bits & 1:
                    raise ValueError("Encrypted symbolic links are unsupported.")
                link = self.read_small(entry, limit=4096).decode("utf-8")
                self.links[name] = link
            for part in (name, *name.parents):
                if part.parts:
                    self.children[part.parent].add(part)
        for name in directories:
            if name in self.entries and not self.entries[name].is_dir():
                raise ValueError(f"Non-directory parent in backup: {name}")
        for name, target in self.links.items():
            check_link(name.parent, target, self.links.get)
        self.directories = directories

    def read_small(self, entry, *, limit=64 * 1024 * 1024):
        if entry.file_size > limit:
            raise ValueError(f"Backup configuration is too large: {entry.filename}")
        if entry.flag_bits & 1:
            with tempfile.TemporaryFile() as output:
                self.copy_encrypted(entry, output)
                output.seek(0)
                return output.read()
        try:
            return self.archive.read(entry)
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError) as error:
            raise ValueError(f"Cannot read ZIP entry {entry.filename}: {error}") from error

    def copy_encrypted(self, entry, output):
        if not self.password:
            raise ValueError("This ZIP backup requires a password.")
        result = subprocess.run(
            ["7z", "x", "-so", "-spd", f"-p{self.password}", "--", str(self.source), entry.filename],
            stdout=output, stderr=subprocess.PIPE, check=False,
        )
        if result.returncode or output.tell() != entry.file_size:
            raise ValueError("Cannot decrypt ZIP entry. Check the backup password and file integrity.")


class ZipPath:
    def __init__(self, backup, path=PurePosixPath()):
        self.backup = backup
        self.path = path

    def __truediv__(self, child):
        return ZipPath(self.backup, self.path / member_path(str(child)))

    def __str__(self):
        return str(self.path)

    @property
    def name(self):
        return self.path.name

    def iterdir(self):
        for path in sorted(self.backup.children.get(self.path, ())):
            yield ZipPath(self.backup, path)

    def rglob(self, pattern):
        for child in self.iterdir():
            yield child
            if child.is_dir():
                yield from child.rglob(pattern)

    def relative_to(self, root):
        return self.path.relative_to(root.path)

    def exists(self):
        return self.is_dir() or self.path in self.backup.entries

    def is_dir(self):
        return self.path in self.backup.directories

    def is_file(self):
        return self.path in self.backup.entries and not (self.is_dir() or self.is_symlink())

    def is_symlink(self):
        return self.path in self.backup.links

    def readlink(self):
        return Path(self.backup.links[self.path])

    def read_text(self, encoding="utf-8"):
        if not self.is_file():
            raise ValueError(f"Expected a regular ZIP entry: {self.path}")
        return self.backup.read_small(self.backup.entries[self.path]).decode(encoding)

    def copy_to(self, destination):
        entry = self.backup.entries[self.path]
        try:
            with destination.open("wb") as output:
                if entry.flag_bits & 1:
                    self.backup.copy_encrypted(entry, output)
                else:
                    with self.backup.archive.open(entry) as payload:
                        if self.backup.home is not None and self.path.suffix in (".ini", ".xml"):
                            home = str(self.backup.home).encode("utf-8")
                            for line in payload:
                                output.write(HOME_PATH.sub(lambda _: home, line))
                        else:
                            shutil.copyfileobj(payload, output)
            if entry.flag_bits & 1 and self.backup.home is not None and self.path.suffix in (".ini", ".xml"):
                replace_home(destination, self.backup.home)
            mode = (entry.external_attr >> 16) & 0o777
            destination.chmod(mode or 0o644)
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError) as error:
            raise ValueError(f"Cannot read ZIP entry {entry.filename}: {error}") from error


def replace_home(path, home):
    original = path.read_bytes()
    updated = HOME_PATH.sub(lambda _: str(home).encode("utf-8"), original)
    if updated != original:
        path.write_bytes(updated)


def extract_tar(source, staging, *, fileobj=None):
    links = {}
    entries = set()
    with tarfile.open(source if fileobj is None else None, "r:*" if fileobj is None else "r|*", fileobj=fileobj) as archive:
        for item in archive:
            if item.name in (".", "./") and item.isdir():
                continue
            name = member_path(item.name)
            if name in entries:
                raise ValueError(f"Duplicate TAR entry: {name}")
            entries.add(name)
            if item.issym():
                links[name] = item.linkname
            elif item.isfile() or item.isdir():
                archive.extract(item, staging, filter="data")
            else:
                raise ValueError(f"Unsupported TAR entry: {item.name}")
    for name in entries:
        if any(parent in links for parent in name.parents):
            raise ValueError(f"Symbolic-link parent in backup: {name}")
    for name, target in links.items():
        check_link(name.parent, target, links.get)
    for name, target in links.items():
        path = staging / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(target)


def copy_backup(source, staging):
    links = {}
    for entry in source.rglob("*"):
        if entry.is_symlink():
            links[PurePosixPath(entry.relative_to(source))] = entry.readlink()
        elif not (entry.is_file() or entry.is_dir()):
            raise ValueError(f"Unsupported backup entry: {entry.name}")
    for name, target in links.items():
        check_link(name.parent, target, links.get)
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
    if source.is_file() and source.name.lower().endswith(".zip"):
        try:
            with zipfile.ZipFile(source) as archive:
                index = ZipBackup(archive, source, password)
                root = backup_root(ZipPath(index))
                for name, target in index.links.items():
                    if not name.is_relative_to(root.path):
                        continue
                    relative = name.relative_to(root.path)
                    parts = relative.parts
                    depth = 2 if len(parts) > 2 and parts[0] in ("General", "DE", "Custom_Dirs", "Flatpak_Apps") else 1
                    component = root.path.joinpath(*parts[:depth])
                    if name != component:
                        check_link(name.relative_to(component).parent, target,
                                   lambda path: index.links.get(component / path))
                yield root
        except (OSError, zipfile.BadZipFile, tarfile.TarError, UnicodeError, NotImplementedError, RuntimeError, EOFError) as error:
            raise ValueError(f"Cannot process ZIP backup: {error}") from error
        return
    with tempfile.TemporaryDirectory(prefix="restore-", dir=parent) as temporary:
        staging = Path(temporary)
        try:
            if source.is_dir():
                copy_backup(source, staging)
            elif source.name.lower().endswith((".tar.gz", ".tgz")):
                extract_tar(source, staging)
            else:
                raise ValueError("Select a ZIP, TAR.GZ or SaveDesktop backup folder.")
            root = backup_root(staging)
        except (OSError, zipfile.BadZipFile, tarfile.TarError, UnicodeError, NotImplementedError, RuntimeError) as error:
            raise ValueError(f"Cannot read backup: {error}") from error
        yield root


def restore_tree(source, destination, *, exclude=(), conflicts=None):
    """Merge files without following existing links in destination directories."""
    if not isinstance(source, ZipPath):
        source = Path(source)
    if source.is_symlink():
        raise ValueError(f"Restore source is a symbolic link: {source}")
    destination = Path(destination)
    links = {}

    def collect(directory, relative, omitted=()):
        target_dir = destination / relative
        if any(path.is_symlink() for path in (target_dir, *target_dir.parents)):
            raise ValueError(f"Restore destination is a symbolic link: {target_dir}")
        target_dir.mkdir(parents=True, exist_ok=True)
        for entry in directory.iterdir():
            if entry.name in omitted:
                continue
            name = relative / entry.name
            target = destination / name
            if conflicts is not None and (target.is_symlink() or target.exists()):
                if entry.is_symlink() and target.is_symlink() and entry.readlink() == target.readlink():
                    continue
                if target.is_symlink() or entry.is_dir() != target.is_dir() or entry.is_symlink():
                    conflicts.append(target)
                    continue
            if entry.is_symlink():
                links[name] = entry.readlink()
            elif target.is_symlink():
                raise ValueError(f"Restore destination is a symbolic link: {target}")
            elif entry.is_dir():
                collect(entry, name)
            else:
                restore_file(entry, target, conflicts=conflicts)

    def lookup(name):
        if name in links:
            return links[name]
        existing = destination / name
        return existing.readlink() if existing.is_symlink() else None

    collect(source, PurePosixPath(), exclude)
    for name, target in links.items():
        check_link(name.parent, target, lookup)
        existing = destination / name
        if existing.is_symlink() and existing.readlink() == target:
            continue
        try:
            existing.symlink_to(target)
        except FileExistsError:
            if conflicts is None:
                raise
            conflicts.append(existing)


def publish_new_file(temporary, destination):
    try:
        os.link(temporary, destination)
    except OSError as error:
        if error.errno not in (errno.EPERM, errno.EOPNOTSUPP, errno.EXDEV, errno.ENOSYS):
            raise
        # Document Portal and some filesystems do not support hard links.
        # Exclusive creation still prevents replacing a concurrent write.
        with temporary.open("rb") as source, destination.open("xb") as output:
            created = os.fstat(output.fileno())
            try:
                shutil.copyfileobj(source, output)
                output.flush()
            except BaseException:
                try:
                    if os.path.samestat(created, destination.lstat()):
                        destination.unlink()
                except FileNotFoundError:
                    pass
                raise


def restore_file(source, destination, *, conflicts=None):
    destination = Path(destination)
    if not isinstance(source, ZipPath):
        source = Path(source)
    if source.is_symlink() or any(path.is_symlink() for path in destination.parents):
        raise ValueError(f"Symbolic link in file restore: {destination}")
    if destination.is_symlink():
        if conflicts is None:
            raise ValueError(f"Symbolic link in file restore: {destination}")
        conflicts.append(destination)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as output:
        temporary = Path(output.name)
    try:
        if isinstance(source, ZipPath):
            source.copy_to(temporary)
        else:
            shutil.copy2(source, temporary)
        if conflicts is None:
            temporary.replace(destination)
        else:
            try:
                publish_new_file(temporary, destination)
            except FileExistsError:
                try:
                    same = (not destination.is_symlink() and destination.is_file() and
                            filecmp.cmp(temporary, destination, shallow=False))
                except OSError:
                    same = False
                if not same:
                    conflicts.append(destination)
    finally:
        temporary.unlink(missing_ok=True)


def restore_tar(source, destination, *, single_folder=False, conflicts=None):
    with tempfile.TemporaryDirectory(prefix="restore-tar-") as temporary:
        staging = Path(temporary)
        if isinstance(source, ZipPath):
            entry = source.backup.entries[source.path]
            if entry.flag_bits & 1:
                nested = staging / "nested.tar"
                source.copy_to(nested)
                extract_tar(nested, staging / "files")
                staging /= "files"
            else:
                with source.backup.archive.open(entry) as payload:
                    extract_tar(None, staging, fileobj=payload)
                    # tarfile can stop at its own EOF before the enclosing ZIP does.
                    while payload.read(1024 * 1024):
                        pass
        else:
            extract_tar(source, staging)
        if single_folder:
            sections = list(staging.iterdir())
            if len(sections) != 1 or not sections[0].is_dir() or sections[0].is_symlink():
                raise ValueError("Expected a single directory in the nested archive.")
            staging = sections[0]
        restore_tree(staging, destination, conflicts=conflicts)



def publish_directory(pending, destination, previous):
    """Publish a complete sibling directory, rolling back a failed rename."""
    try:
        if destination.exists() or destination.is_symlink():
            destination.rename(previous)
        pending.rename(destination)
    except BaseException:
        if not (destination.exists() or destination.is_symlink()) and (previous.exists() or previous.is_symlink()):
            previous.rename(destination)
        raise
