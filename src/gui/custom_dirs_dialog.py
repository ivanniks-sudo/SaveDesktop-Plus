import os
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gio, GLib

from savedesktop.core.custom_dirs import access_path, original_path
from savedesktop.globals import APP_ID, flatpak, settings


class CustomDirsDialog(Adw.AlertDialog):
    def __init__(self, parent):
        super().__init__()
        self.parent = parent
        self.set_heading(_("Custom folders"))
        self.set_body(_("Select custom folders to include in the configuration archive."))
        self.add_response("cancel", _("Cancel"))
        self.add_response("ok", _("Apply"))
        self.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        self.connect("response", self.apply_settings)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.set_extra_child(box)
        self.flow_box = Gtk.ListBox.new()
        self.flow_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.flow_box.add_css_class("boxed-list")
        scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            max_content_height=280,
            propagate_natural_height=True,
            child=self.flow_box,
        )
        box.append(scroller)
        disabled = {access_path(path) for path in settings.get_strv("disabled-custom-dirs")}
        known_paths = settings.get_value("custom-dir-original-paths").unpack()
        for folder in settings.get_strv("custom-dirs"):
            path = access_path(folder)
            self._add_folder(path, enabled=path not in disabled, known_path=known_paths.get(path), loading=True)

        button = Gtk.Button.new_with_label(_("Add folder"))
        button.add_css_class("suggested-action")
        button.add_css_class("pill")
        button.set_halign(Gtk.Align.CENTER)
        button.connect("clicked", lambda _button: self._show_file_dialog())
        box.append(button)

        entry_box = Gtk.Box(spacing=6)
        self.path_entry = Gtk.Entry(hexpand=True, placeholder_text=_("Folder path"))
        self.path_entry.connect("activate", self._add_manual_path)
        entry_box.append(self.path_entry)
        add_path = Gtk.Button(label=_("Add path"))
        add_path.connect("clicked", self._add_manual_path)
        entry_box.append(add_path)
        box.append(entry_box)
        box.append(Gtk.Label(
            label=_("Use Ctrl+H in the folder chooser to show hidden folders."),
            wrap=True, xalign=0, css_classes=["dim-label"],
        ))

    def _show_file_dialog(self, initial_path=None):
        chooser = Gtk.FileDialog.new()
        chooser.set_modal(True)
        chooser.set_title(_("Choose another folder"))
        if initial_path:
            chooser.set_initial_folder(Gio.File.new_for_path(initial_path))
        chooser.select_folder(self.parent, None, self._folder_selected, None)

    def _folder_selected(self, chooser, result, data):
        try:
            folder = chooser.select_folder_finish(result)
        except GLib.Error as error:
            if not (error.matches(Gtk.DialogError.quark(), Gtk.DialogError.DISMISSED)
                    or error.matches(Gtk.DialogError.quark(), Gtk.DialogError.CANCELLED)):
                self._show_selection_error(error.message)
            return

        path = folder.get_path()
        if path is None:
            self._show_selection_error(_("The selected folder has no local filesystem path."))
        elif self._add_folder(path):
            self.path_entry.set_text("")

    def _add_manual_path(self, widget):
        path = os.path.expanduser(self.path_entry.get_text())
        if not os.path.isabs(path):
            self._show_selection_error(_("Enter an absolute folder path or a path starting with ~/."))
        elif self._folder_is_readable(path):
            if self._add_folder(path):
                self.path_entry.set_text("")
        elif flatpak:
            dialog = Adw.AlertDialog.new(
                _("Folder unavailable"),
                _("Choose the folder in the file dialog to grant access. The path you entered is kept."),
            )
            dialog.add_response("cancel", _("Cancel"))
            dialog.add_response("choose", _("Choose folder"))
            dialog.set_response_appearance("choose", Adw.ResponseAppearance.SUGGESTED)
            dialog.connect("response", lambda _dialog, response: self._show_file_dialog(path) if response == "choose" else None)
            dialog.present(self.parent)
        else:
            self._show_selection_error(_("The folder is missing or cannot be read."))

    def _show_selection_error(self, message):
        dialog = Adw.AlertDialog.new(_("Could not select folder"), message)
        dialog.add_response("close", _("Close"))
        dialog.present(self.parent)

    @staticmethod
    def _folder_is_readable(path):
        try:
            with os.scandir(path) as entries:
                next(entries, None)
        except OSError:
            return False
        return True

    def _add_folder(self, path, *, enabled=True, known_path=None, loading=False):
        host_path = original_path(path, flatpak=flatpak) or known_path
        readable = self._folder_is_readable(path)
        if host_path == path and readable:
            host_path = os.path.realpath(path)
            if ".." in Path(path).parts:
                path = host_path
        for existing in self.flow_box:
            if path != existing.access_path and (host_path is None or host_path != existing.host_path):
                continue
            if not existing.readable and readable:
                existing.access_path = path
                self._describe_row(existing, host_path, readable)
                return True
            if not loading:
                self._show_selection_error(_("This folder is already in the list."))
            return False

        row = Adw.ActionRow.new()
        row.access_path = path
        row.set_use_markup(False)
        row.set_title_lines(3)
        self._describe_row(row, host_path, readable)

        row.include = Gtk.CheckButton(active=enabled, valign=Gtk.Align.CENTER)
        label = _("Include in backup")
        row.include.set_tooltip_text(label)
        row.include.update_property([Gtk.AccessibleProperty.LABEL], [label])
        row.add_prefix(row.include)
        row.set_activatable_widget(row.include)

        remove = Gtk.Button.new_from_icon_name("user-trash-symbolic")
        remove.add_css_class("destructive-action")
        remove.set_tooltip_text(_("Remove"))
        remove.set_valign(Gtk.Align.CENTER)
        remove.connect("clicked", lambda _button: self.flow_box.remove(row))
        row.add_suffix(remove)
        self.flow_box.append(row)
        return True

    def _describe_row(self, row, host_path, readable):
        row.host_path = host_path
        row.readable = readable
        row.set_title(host_path or Path(row.access_path).name or row.access_path)
        row.set_tooltip_text(host_path or _("The original location is unavailable."))
        if not readable:
            row.set_subtitle(_("Folder unavailable. Select it again to restore access, or exclude it from backup."))
        elif host_path is None:
            row.set_subtitle(_("The original location is unavailable. Backup uses the folder granted by Document Portal."))
        else:
            row.set_subtitle("")

    def apply_settings(self, dialog, response):
        if response == "ok":
            update = Gio.Settings.new(APP_ID)
            update.delay()
            update.set_strv("custom-dirs", [row.access_path for row in self.flow_box])
            update.set_strv("disabled-custom-dirs", [row.access_path for row in self.flow_box if not row.include.get_active()])
            update.set_value("custom-dir-original-paths", GLib.Variant(
                "a{ss}", {row.access_path: row.host_path for row in self.flow_box if row.host_path is not None},
            ))
            update.apply()
