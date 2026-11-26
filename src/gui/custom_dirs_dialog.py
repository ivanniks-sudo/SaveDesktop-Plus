import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib

from savedesktop.core.custom_dirs import access_path, original_path
from savedesktop.globals import flatpak, settings


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

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        self.set_extra_child(box)
        self.flow_box = Gtk.ListBox.new()
        self.flow_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.flow_box.add_css_class("boxed-list")
        box.append(self.flow_box)
        for folder in settings.get_strv("custom-dirs"):
            self._add_folder(access_path(folder))

        button = Gtk.Button.new_with_label(_("Add folder"))
        button.add_css_class("suggested-action")
        button.add_css_class("pill")
        button.set_valign(Gtk.Align.CENTER)
        button.set_halign(Gtk.Align.CENTER)
        button.connect("clicked", self._show_file_dialog)
        box.append(button)

    def _show_file_dialog(self, button):
        chooser = Gtk.FileDialog.new()
        chooser.set_modal(True)
        chooser.set_title(_("Choose another folder"))
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
            return
        self._add_folder(path)

    def _show_selection_error(self, message):
        dialog = Adw.AlertDialog.new(_("Could not select folder"), message)
        dialog.add_response("close", _("Close"))
        dialog.present(self.parent)

    def _add_folder(self, path):
        host_path = original_path(path, flatpak=flatpak)
        row = Adw.ActionRow.new()
        row.access_path = path
        row.set_use_markup(False)
        row.set_title(host_path or path)
        row.set_title_lines(4)
        if host_path is None:
            row.set_subtitle(_("The original location is unavailable. Backup uses the folder granted by Document Portal."))

        remove = Gtk.Button.new_from_icon_name("user-trash-symbolic")
        remove.add_css_class("destructive-action")
        remove.set_tooltip_text(_("Remove"))
        remove.set_valign(Gtk.Align.CENTER)
        remove.connect("clicked", lambda _button: self.flow_box.remove(row))
        row.add_suffix(remove)
        self.flow_box.append(row)

    def apply_settings(self, dialog, response):
        if response == "ok":
            settings.set_strv("custom-dirs", [row.access_path for row in self.flow_box])
