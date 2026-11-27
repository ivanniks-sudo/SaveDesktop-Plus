#!/usr/bin/env python3
"""Apply a prepared Flatpak restore on the host, at login or on explicit request."""
import contextlib
import ctypes
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from tempfile import NamedTemporaryFile, TemporaryDirectory

if __package__:
    from .backup_input import extract_tar, restore_tree
else:
    from backup_input import extract_tar, restore_tree

APP_ID = "io.github.ivanniks_sudo.SaveDesktopPlus"
PROTECTED_APPS = {APP_ID, "io.github.vikdevelop.SaveDesktop"}
SCOPES = ("--system", "--user")
LISTS = ("installed_flatpaks.sh", "installed_user_flatpaks.sh")
FLATHUB = "https://dl.flathub.org/repo/flathub.flatpakrepo"
APP = re.compile(r"[a-zA-Z_][\w-]*(?:\.[a-zA-Z_][\w-]*){2,}", re.ASCII)


def run(*args, capture=False):
    result = subprocess.run(("flatpak", *args), text=True, check=True,
                            stdout=subprocess.PIPE if capture else None)
    return result.stdout if capture else None


def desired_apps(source, excluded):
    desired = set()
    for filename, scope in zip(LISTS, SCOPES):
        path = source / filename
        if path.is_symlink():
            raise ValueError(f"Flatpak list cannot be a symbolic link: {filename}")
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if (len(parts) == 5 and parts[:3] == ["flatpak", "install", scope]
                    and parts[-1] == "-y" and APP.fullmatch(parts[3])):
                if parts[3] not in excluded:
                    desired.add((scope, parts[3]))
            elif line.strip() and not line.lstrip().startswith("#"):
                raise ValueError(f"Unsupported Flatpak list entry in {filename}")
    return desired


def running_apps():
    return set(run("ps", "--columns=application", capture=True).split())


@contextlib.contextmanager
def restore_lock(directory):
    descriptor = os.open(directory / "flatpak-restore.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


def save_completed(workspace, completed):
    with NamedTemporaryFile(dir=workspace, delete=False, mode="w", encoding="utf-8") as stream:
        temporary = Path(stream.name)
        try:
            json.dump(sorted(completed), stream)
            stream.flush()
            temporary.replace(workspace / "flatpak-completed.json")
        finally:
            temporary.unlink(missing_ok=True)


def merge_app_data(sources, destination):
    if any(item.is_symlink() for item in (destination, *destination.parents)):
        raise ValueError(f"Symbolic link in Flatpak data destination: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".sdplus-data-", dir=destination.parent) as staging:
        pending = Path(staging) / "app"
        if destination.exists():
            # Retain independent user state and publish the merged tree in one rename.
            shutil.copytree(destination, pending, symlinks=True)
        for source in sources:
            restore_tree(source, pending)
        if destination.name in running_apps():
            return False
        if destination.exists():
            # Linux RENAME_EXCHANGE prevents an interval with no live data directory.
            rename = ctypes.CDLL(None, use_errno=True).renameat2
            if rename(-100, os.fsencode(pending), -100, os.fsencode(destination), 2) != 0:
                error = ctypes.get_errno()
                raise OSError(error, os.strerror(error), str(destination))
        else:
            pending.rename(destination)
    return True


def restore(workspace, home):
    preferences = workspace / "flatpak-prefs.json"
    if preferences.is_symlink():
        raise ValueError("Flatpak restore preferences cannot be a symbolic link")
    prefs = json.loads(preferences.read_text(encoding="utf-8"))
    journal = workspace / "flatpak-completed.json"
    section = workspace / "Flatpak_Apps"
    if journal.is_symlink() or section.is_symlink():
        raise ValueError("Flatpak restore state cannot contain symbolic links")
    done = set(json.loads(journal.read_text(encoding="utf-8"))) if journal.is_file() else set()
    excluded = set(prefs["disabled-flatpaks"]) | PROTECTED_APPS
    source = section if section.is_dir() else workspace
    install = prefs["install-flatpaks"]
    desired = desired_apps(source, excluded) if install else set()
    listed_scopes = {scope for filename, scope in zip(LISTS, SCOPES) if (source / filename).is_file()}
    failures = []
    deferred = []

    installed = {(scope, app) for scope in SCOPES
                 for app in run("list", scope, "--app", "--columns=application", capture=True).split()
                 if APP.fullmatch(app)}
    missing = desired - installed
    for scope, app in sorted(desired & installed):
        print(f"[INFO] {app} is already available in the system.", flush=True)
    for index, (scope, app) in enumerate(sorted(missing), 1):
        print(f"↓ Installing {app} ({index}/{len(missing)})", flush=True)
        try:
            run("remote-add", scope, "--if-not-exists", "flathub", FLATHUB)
            run("install", scope, "--noninteractive", "-y", "flathub", app)
        except (OSError, subprocess.CalledProcessError) as error:
            failures.append(f"{app} ({scope}): {error}")
        else:
            installed.add((scope, app))
            print(f"✔ Finished installing {app}", flush=True)
        if not prefs["copy-data"] and prefs["keep-flatpaks"]:
            print(f"[PROGRESS] {index}/{len(missing)}", flush=True)

    unavailable = {app for _, app in desired - installed}
    if prefs["copy-data"]:
        print("[COPY] Copying the Flatpak's user data to ~/.var/app", flush=True)
        with TemporaryDirectory(prefix="restore-apps-") as temporary:
            staging = Path(temporary)
            archive = source / "flatpak-apps-data.tgz"
            if archive.is_symlink():
                raise ValueError("Flatpak data archive cannot be a symbolic link")
            if archive.is_file():
                extract_tar(archive, staging)
            entries = {}
            for folder in (workspace / "app", staging / "app"):
                if folder.is_dir() and not folder.is_symlink():
                    for entry in folder.iterdir():
                        if APP.fullmatch(entry.name) and entry.name not in excluded:
                            entries.setdefault(entry.name, []).append(entry)
            for app, folders in sorted(entries.items()):
                if app in done:
                    continue
                if app in unavailable:
                    deferred.append(app)
                    continue
                try:
                    if app in running_apps() or not merge_app_data(folders, home / ".var/app" / app):
                        deferred.append(app)
                        continue
                    done.add(app)
                    save_completed(workspace, done)
                    print(f"[DATA] {app}", flush=True)
                except (OSError, ValueError, subprocess.CalledProcessError) as error:
                    failures.append(f"{app}: {error}")

    if install and not prefs["keep-flatpaks"] and desired and not failures and not deferred:
        for scope, app in sorted(installed - desired):
            if scope not in listed_scopes or app in excluded or any(name == app for _, name in desired):
                continue
            try:
                if app in running_apps():
                    deferred.append(app)
                    continue
                print(f"[REMOVE] {scope} {app}", flush=True)
                # Flatpak data are shared between installations. Keep the files for manual cleanup.
                run("uninstall", scope, "--noninteractive", "-y", app)
            except (OSError, subprocess.CalledProcessError) as error:
                failures.append(f"{app} ({scope}): {error}")

    for error in failures:
        print(f"[ERROR] {error}", flush=True)
    for app in deferred:
        print(f"[PENDING] {app}", flush=True)
    return "partial" if failures or deferred else "success"


def main():
    home = Path.home()
    if "--current-session" in sys.argv[1:]:
        workspace = home / ".var/app" / APP_ID / "cache/tmp/workspace"
    else:
        workspace = Path(__file__).resolve().parent
    autostart = home / ".config/autostart" / f"{APP_ID}.Flatpak.desktop"
    try:
        if any(item.is_symlink() for item in (workspace, *workspace.parents)):
            raise ValueError("Flatpak workspace cannot traverse a symbolic link")
        with restore_lock(workspace.parent):
            return restore_and_log(workspace, home, autostart)
    except BlockingIOError:
        print("A Flatpak restore is already running. Use the progress window to follow it.", file=sys.stderr)
        return 1
    except (OSError, ValueError) as error:
        print(f"Flatpak restore could not start: {error}", file=sys.stderr)
        return 1


def restore_and_log(workspace, home, autostart):
    finished = workspace / "flatpak-succeeded"
    if finished.is_symlink():
        raise ValueError("Flatpak result cannot be a symbolic link")
    if finished.exists():
        print("This Flatpak restore has already completed.")
        return 0
    log_path = workspace / "log.pipe"
    print(f"Flatpak restore log: {log_path}", flush=True)
    descriptor = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", buffering=1) as log:
        # Also send the Flatpak subprocess output to the status window.
        sys.stdout.flush()
        os.dup2(log.fileno(), 1)
        os.dup2(log.fileno(), 2)
        try:
            if "--show-progress" in sys.argv[1:]:
                subprocess.Popen(("flatpak", "run", APP_ID, "--show-flatpaks-installer"),
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            result = restore(workspace, home)
            if result == "success":
                with finished.open("x"):
                    pass
                autostart.unlink(missing_ok=True)
        except Exception as error:
            print(f"[ERROR] {error}", flush=True)
            result = "error"
        print(f"[RESULT] {result}", flush=True)
        return 0 if result == "success" else 1

if __name__ == "__main__":
    sys.exit(main())
