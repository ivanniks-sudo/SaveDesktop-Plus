#!/usr/bin/env python3
import os, sys, re, zipfile, random, string, gi, subprocess, locale, shutil, json
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, GLib, Gdk
from datetime import date
from pathlib import Path
from uuid import uuid4
from threading import Thread
from savedesktop.globals import *
from savedesktop.gui.more_options_dialog import MoreOptionsDialog
from savedesktop.gui.items_dialog import itemsDialog
from savedesktop.gui.restore_options import RestoreOptions
from savedesktop.core.backup_input import prepare_backup
from savedesktop.core.custom_dirs import read_folder_metadata
from savedesktop.core.desktop_configuration import filter_desktop_settings
from savedesktop.core.operation_notices import operation_notices
from savedesktop.core.restore_completion import HOST_BUILD, HOST_PROTOCOL, STEP, read_completion, restart_cinnamon
from savedesktop.core.de_config import prepare_session_launcher
from savedesktop.core.flatpaks_installer import PROTECTED_APPS, desired_apps
from savedesktop.core.wallpaper import split_wallpaper
from savedesktop.gui.synchronization_dialogs import InitSetupDialog, SetDialog, CloudDialog

# Application window
class MainWindow(Adw.ApplicationWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.set_title(APP_NAME)
        self.import_session = None
        self.import_cancelled = False
        self.app_wiki = "https://vikdevelop.github.io/SaveDesktop/wiki"

        # header bar and toolbarview
        self.headerbar = Adw.HeaderBar.new()
        self.toolbarview = Adw.ToolbarView.new()
        self.toolbarview.add_top_bar(self.headerbar)

        # set the window size and maximization from the GSettings database
        (width, height) = settings["window-size"]
        self.set_default_size(width, height)
        self.set_size_request(360, 500)

        # if the value is TRUE, it enables window maximalization
        if settings["maximized"]:
            self.maximize()

        # primary menu
        self.main_menu = Gio.Menu()

        # primary menu section
        self.general_menu = Gio.Menu()
        self.general_menu.append(_("Keyboard shortcuts"), 'app.shortcuts')
        self.general_menu.append(_("Select configuration items"), 'app.items-dialog')
        self.general_menu.append(_("About app"), 'app.about')
        self.main_menu.append_section(None, self.general_menu)

        # menu button
        self.menu_button = Gtk.MenuButton.new()
        self.menu_button.set_icon_name(icon_name='open-menu-symbolic')
        self.menu_button.set_menu_model(menu_model=self.main_menu)
        self.menu_button.set_primary(True)
        self.headerbar.pack_end(child=self.menu_button)

        # primary layout
        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scrolled.set_vexpand(True)
        self.scrolled.set_hexpand(True)
        self.toolbarview.set_content(self.scrolled)

        self.headapp = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.headapp.set_valign(Gtk.Align.FILL)
        self.headapp.set_halign(Gtk.Align.FILL)
        self.headapp.set_vexpand(True)
        self.headapp.set_hexpand(True)
        self.scrolled.set_child(self.headapp)

        # A view container for the menu switcher
        self.stack = Adw.ViewStack(vexpand=True)
        self.stack.set_hhomogeneous(False)
        self.headapp.append(self.stack)

        # Layout for saving and importing configuration
        self.saveBox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        self.importBox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.syncingBox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)

        # Add pages to the menu switcher
        self.stack.add_titled_with_icon(self.saveBox,"savepage",_("Save"),"document-save-symbolic")
        self.stack.add_titled_with_icon(self.importBox,"importpage",_("Import"),"document-open-symbolic")
        self.stack.add_titled_with_icon(self.syncingBox,"syncpage",_("Sync"),"view-refresh-symbolic")

        # menu switcher
        self.switcher_title = Adw.ViewSwitcherTitle.new()
        self.switcher_title.set_stack(self.stack)
        self.switcher_title.set_title(APP_NAME)
        self.headerbar.set_title_widget(self.switcher_title)

        # menu bar
        self.switcher_bar = Adw.ViewSwitcherBar.new()
        self.switcher_bar.set_stack(self.stack)
        self.toolbarview.add_bottom_bar(self.switcher_bar)

        self._setup_switcher_responsive()

        # Toast Overlay for showing the popup window
        self.toast_overlay = Adw.ToastOverlay.new()
        self.toast_overlay.set_child(self.toolbarview)
        self.set_content(self.toast_overlay)

        # Popup window for showing messages about necessity to log out of the system after selected the periodic synchronization interval
        self.toast = Adw.Toast.new(title='')
        self.toast.set_timeout(0)

        self._env_detection()

    # Switch between ViewSwitcherTitle and ViewSwitcherBar based on the Adw.Breakpoint status
    def _setup_switcher_responsive(self):
        self.break_point = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse("max-width: 400sp")
        )

        # When activating a narrow breakpoint, display the switcher_bar
        self.apply_handler = self.break_point.connect("apply", self.__on_break_point_apply)

        # Hide switcher_bar when narrow breakpoint is deactivated
        self.unapply_handler = self.break_point.connect("unapply", self.__on_break_point_unapply)

        # Add a breakpoint to the window
        self.add_breakpoint(self.break_point)

        # Default state - hidden (only displayed when the breakpoint is met)
        self.switcher_bar.set_reveal(False)

    def __on_break_point_apply(self, break_point):
        self.switcher_bar.set_reveal(True)

    def __on_break_point_unapply(self, break_point):
        self.switcher_bar.set_reveal(False)

    # If the user has a supported environment, it shows the app window, otherwise, it shows the window with information about an unsupported environment
    def _env_detection(self):
        def setup_environment(env_name):
            self.environment = env_name
            self.save_desktop()
            self.import_desktop()
            self.sync_desktop()
            self.connect("close-request", self.on_close)

        if environment:
            setup_environment(environment["de_name"])
        else:
            # Handle unsupported desktop environments
            self.headerbar.set_title_widget(None)
            self.toolbarview.remove(self.switcher_bar)
            self.pBox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            self.pBox.set_halign(Gtk.Align.CENTER)
            self.pBox.set_valign(Gtk.Align.CENTER)
            self.pBox.set_margin_start(50)
            self.pBox.set_margin_end(50)
            self.toolbarview.set_content(self.pBox)

            self.env_list = [env["de_name"] for env in ENVIRONMENTS.values()]
            self.unsupp_label = Gtk.Label.new()
            self.unsupp_label.set_label(str=f'<big>{_("<big><b>You have an unsupported environment installed.</b></big>\nPlease use one of these environments: {}.")}</big>'.format(self.env_list))
            self.unsupp_label.set_use_markup(True)
            self.unsupp_label.set_justify(Gtk.Justification.CENTER)
            self.unsupp_label.set_wrap(True)
            self.pBox.append(self.unsupp_label)

    # Show main page
    def save_desktop(self):
        # Set valign for the save desktop layout
        self.saveBox.set_valign(Gtk.Align.CENTER)

        # Title image for the save page
        self.titleImage = Gtk.Image.new_from_icon_name("computer")
        self.titleImage.set_pixel_size(128)
        self.saveBox.append(self.titleImage)

        # Title "Save Current configuration" for save page and subtitle "{user_desktop}"
        self.label_title = Gtk.Label.new()
        self.label_title.set_markup('<big><b>{}</b></big>\n{}'.format(_("Save the current configuration"), self.environment))
        self.label_title.set_justify(Gtk.Justification.CENTER)
        self.saveBox.append(self.label_title)

        # Box for show these options: set the filename, set items that will be included to the config archive and periodic saving
        self.lbox_e = Gtk.ListBox.new()
        self.lbox_e.set_selection_mode(mode=Gtk.SelectionMode.NONE)
        self.lbox_e.add_css_class(css_class='boxed-list-separate')
        self.lbox_e.set_margin_start(20)
        self.lbox_e.set_margin_end(20)
        self.lbox_e.set_halign(Gtk.Align.CENTER)
        self.lbox_e.set_valign(Gtk.Align.CENTER)
        self.saveBox.append(self.lbox_e)

        # set the filename section
        self.saveEntry = Adw.EntryRow.new()
        self.saveEntry.set_title(_("Set the file name"))
        self.saveEntry.set_text(settings["filename"])
        self.lbox_e.append(self.saveEntry)

        self.lbox_e.set_show_separators(True)

        # section for showing dialog with more options
        # button
        self.msButton = Gtk.Button.new_from_icon_name("go-next-symbolic")
        self.msButton.add_css_class('flat')
        self.msButton.set_valign(Gtk.Align.CENTER)
        self.msButton.connect("clicked", self._open_more_options_dialog)

        # action row
        self.moreSettings = Adw.ActionRow.new()
        self.moreSettings.set_title(_("More options"))
        self.moreSettings.set_subtitle(f'{_("Periodic saving")}, {_("Manual saving")}')
        self.moreSettings.set_subtitle_lines(3)
        self.moreSettings.add_suffix(self.msButton)
        self.moreSettings.set_activatable_widget(self.msButton)
        self.lbox_e.append(self.moreSettings)

        # Save configuration button
        self.saveButton = Gtk.Button.new_with_label(_("Save"))
        self.saveButton.add_css_class("suggested-action")
        self.saveButton.add_css_class("pill")
        self.saveButton.connect("clicked", self.select_folder)
        self.saveButton.set_valign(Gtk.Align.CENTER)
        self.saveButton.set_halign(Gtk.Align.CENTER)
        self.saveBox.append(self.saveButton)

    def _open_more_options_dialog(self, w):
        self.more_options_dialog = MoreOptionsDialog(self)
        self.more_options_dialog.choose(self, None, None, None)
        self.more_options_dialog.present(self)

    # Import configuration page
    def import_desktop(self):
        self.importBox.set_valign(Gtk.Align.CENTER)
        self.importBox.set_halign(Gtk.Align.CENTER)

        # Box for the below buttons
        self.btnBox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)

        # Import configuration button
        self.fileButton = Gtk.Button.new_with_label(_("Import from file"))
        self.fileButton.add_css_class("pill")
        self.fileButton.add_css_class("suggested-action")
        self.fileButton.set_halign(Gtk.Align.CENTER)
        self.fileButton.set_valign(Gtk.Align.CENTER)
        self.fileButton.connect("clicked", self.select_file_to_import)
        self.btnBox.append(self.fileButton)

        # Import configuration from folder button
        self.folderButton = Gtk.Button.new_with_label(_("Import from folder"))
        self.folderButton.add_css_class("pill")
        self.folderButton.set_halign(Gtk.Align.CENTER)
        self.folderButton.set_valign(Gtk.Align.CENTER)
        self.folderButton.connect("clicked", self.select_folder_to_import)
        self.btnBox.append(self.folderButton)

        # Image and title for the Import page
        self.importPage = Adw.StatusPage.new()
        self.importPage.set_icon_name("package-x-generic")
        self.importPage.set_title(_("Import"))
        self.importPage.set_description(_("Import saved configuration"))
        self.importPage.set_child(self.btnBox)
        self.importBox.append(self.importPage)

    # Syncing desktop page
    def sync_desktop(self):
        self._basic_setup()

        self.syncingBox.set_valign(Gtk.Align.CENTER)
        self.syncingBox.set_valign(Gtk.Align.CENTER)

        # Box, image and title for this page
        self.sync_btn_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.syncPage = Adw.StatusPage.new()
        self.syncPage.set_icon_name("folder-remote")
        self.syncPage.set_title(_("Sync"))
        self.syncPage.set_description(f'{_("Sync your desktop environment configuration with other computers in the network.")} <a href="{self.app_wiki}/synchronization/{self.language}">{_("Learn more")}</a>')
        self.syncPage.set_child(self.sync_btn_box)
        self.syncingBox.append(self.syncPage)

        # "Set up the sync file" button
        self.setButton = Gtk.Button.new_with_label(_("Set up the sync file"))
        self.setButton.set_name("set-button")
        self.setButton.add_css_class("pill")
        self.setButton.add_css_class("suggested-action")
        self.setButton.connect("clicked", self._open_InitSetupDialog)
        self.setButton.set_valign(Gtk.Align.CENTER)
        self.setButton.set_halign(Gtk.Align.CENTER)
        self.sync_btn_box.append(self.setButton)

        # "Connect with other computer" button
        self.getButton = Gtk.Button.new_with_label(_("Connect to the cloud storage"))
        self.getButton.set_name("get-button")
        self.getButton.add_css_class("pill")
        self.getButton.connect("clicked", self._open_InitSetupDialog)
        self.getButton.set_valign(Gtk.Align.CENTER)
        self.getButton.set_halign(Gtk.Align.CENTER)
        self.sync_btn_box.append(self.getButton)

    def _basic_setup(self):
        # Set showing the Initial synchronization setup dialog only if the periodic saving folder or cloud drive folder does not use GVFS or Rclone filesystem
        settings["first-synchronization-setup"] = True if not os.path.exists(f"{DATA}/savedesktop-synchronization.sh") else False
        # Get language to show the wiki page in the correct ones
        self.language = language

    def _open_InitSetupDialog(self, w):
        self._basic_setup()
        self.__get_button_type(w)
        if settings["first-synchronization-setup"] == True:
            self.init_setup_dialog = InitSetupDialog(self)
            self.init_setup_dialog.choose(self, None, None, None)
            self.init_setup_dialog.present(self)
        else:
            if self.get_btn_type == "set-button":
                self._open_SetDialog()
            else:
                self._open_CloudDialog()

    def __get_button_type(self, w):
        try:
            self.get_btn_type = w.get_name()
        except:
            self.get_btn_type = w

    def _open_SetDialog(self):
        self.set_dialog = SetDialog(self)
        self.set_dialog.choose(self, None, None, None)
        self.set_dialog.present(self)

    def _open_CloudDialog(self):
        self.set_dialog = CloudDialog(self)
        self.set_dialog.choose(self, None, None, None)
        self.set_dialog.present(self)

    # Select folder for saving configuration
    def select_folder(self, w):
        if self.import_session is not None:
            return
        def save_selected(source, res, data):
            try:
                folder = source.select_folder_finish(res)
            except:
                return
            self.folder = folder.get_path()
            if settings["enable-encryption"] == True:
                self.create_password_dialog()
            else:
                self.save_config()

        if self.saveEntry.get_text() == "":
            self.filename_text = "config"
        else:
            if " " in self.saveEntry.get_text():
                self.with_spaces_text = self.saveEntry.get_text()
                self.filename_text = self.with_spaces_text.replace(" ", "_")
            else:
                self.filename_text = f'{self.saveEntry.get_text()}'

        self.folderchooser = Gtk.FileDialog.new()
        self.folderchooser.set_modal(True)
        self.folderchooser.set_title(_("Save the current configuration"))
        self.folderchooser.select_folder(self, None, save_selected, None)

    def _begin_import_session(self):
        if self.import_session is not None:
            return None
        self.import_session = object()
        self.import_cancelled = False
        self.import_restart_requested = False
        self.authorized_restore = None
        self.stack.set_visible_child_name("importpage")
        return self.import_session

    def _finish_import_session(self):
        approved = getattr(self, "authorized_restore", None)
        if approved:
            bus, job = approved
            service = APP_ID + ".RestoreHost"
            bus.call(service, "/" + service.replace(".", "/"), service, "CancelRestore",
                     GLib.Variant("(s)", (job,)), None, Gio.DBusCallFlags.NO_AUTO_START, 1000, None, None)
            self.authorized_restore = None
        self._remove_restore_staging(getattr(self, "restore_options", {}).get("job"))
        self.import_session = None
        self.toast_overlay.dismiss_all()
        self.stack.set_visible_child_name("importpage")
        (Path(CACHE) / "temp_file").unlink(missing_ok=True)

    def _select_import(self, folder=False):
        session = self._begin_import_session()
        if session is None:
            return

        def selected(chooser, result, data):
            if self.import_session is not session:
                return
            try:
                entry = chooser.select_folder_finish(result) if folder else chooser.open_finish(result)
            except GLib.Error:
                self._finish_import_session()
                return
            self.path_to_import = entry.get_path()
            if not self.path_to_import:
                self._finish_import_session()
                self.show_err_msg(_("The selected folder has no local filesystem path."))
                return
            if not folder and self.path_to_import.lower().endswith(".zip"):
                try:
                    with zipfile.ZipFile(self.path_to_import) as archive:
                        encrypted = any(entry.flag_bits & 1 for entry in archive.infolist())
                except (OSError, zipfile.BadZipFile):
                    encrypted = False  # Archive inspection reports the structural error.
                if encrypted:
                    self.check_password_dialog()
                    return
            self.import_config()

        self.file_chooser = Gtk.FileDialog(modal=True, title=_("Import from folder") if folder else _("Import from file"))
        if folder:
            self.file_chooser.select_folder(self, None, selected, None)
        else:
            file_filter = Gtk.FileFilter(name=_("Save Desktop files"))
            file_filter.add_pattern("*.sd.tar.gz")
            file_filter.add_suffix("zip")
            filters = Gio.ListStore.new(Gtk.FileFilter)
            filters.append(file_filter)
            self.file_chooser.set_filters(filters)
            self.file_chooser.open(self, None, selected, None)

    def select_file_to_import(self, w):
        self._select_import()

    def select_folder_to_import(self, w):
        self._select_import(folder=True)

    # Dialog for creating password for the config archive
    def create_password_dialog(self):
        # Dialog itself
        self.pswdDialog = Adw.AlertDialog.new()
        self.pswdDialog.set_heading(_("Create new password"))
        self.pswdDialog.set_body(_("Please create new password for your archive. Criteria include a length of at least 12 characters, one uppercase letter, one lowercase letter, and one special character."))
        self.pswdDialog.choose(self, None, None, None)
        self.pswdDialog.add_response("cancel", _("Cancel"))
        self.pswdDialog.add_response("ok", _("Apply"))
        self.pswdDialog.set_response_enabled("ok", False)
        self.pswdDialog.set_response_appearance('ok', Adw.ResponseAppearance.SUGGESTED)
        self.pswdDialog.connect('response', self._pswdDialog_closed)
        self.pswdDialog.present()

        # Button for generating strong password
        self.pswdgenButton = Gtk.Button.new_from_icon_name("dialog-password-symbolic")
        self.pswdgenButton.set_tooltip_text(_("Generate Password"))
        self.pswdgenButton.add_css_class("flat")
        self.pswdgenButton.set_valign(Gtk.Align.CENTER)
        self.pswdgenButton.connect("clicked", self._get_generated_password)

        # entry for entering password
        self.pswdEntry = Adw.PasswordEntryRow.new()
        self.pswdEntry.set_title(_("Password"))
        self.pswdEntry.connect('changed', self._check_password)
        self.pswdEntry.add_suffix(self.pswdgenButton)
        self.pswdDialog.set_extra_child(self.pswdEntry)

    # Action after closing pswdDialog
    def _pswdDialog_closed(self, w, response):
        if response == 'ok':
            with open(f"{CACHE}/temp_file", "w") as tmp:
                tmp.write(self.pswdEntry.get_text())
            self.save_config()

    # Check the password to see if it meets the criteria
    def _check_password(self, pswdEntry):
        password = self.pswdEntry.get_text()
        criteria = [
            (len(password) < 12, "The password is too short. It should has at least 12 characters"),
            (not re.search(r'[A-Z]', password), "The password should has at least one capital letter"),
            (not re.search(r'[a-z]', password), "The password should has at least one lowercase letter"),
            (not re.search(r'[-_@.:,+=]', password), "The password should has at least one special character"),
            (" " in password, "The password must not contain spaces")
        ]

        for condition, message in criteria:
            if condition:
                self.pswdDialog.set_response_enabled("ok", False)
                print(message)
                return

        self.pswdDialog.set_response_enabled("ok", True)

    # Generate Password
    def _get_generated_password(self, w):
        self.password = self._password_generator()
        self.pswdEntry.set_text(self.password)

    def _password_generator(self):
        safe = "-_@.:,+="
        allc = safe + string.ascii_letters + string.digits
        password = [random.choice(safe), random.choice(string.ascii_letters), random.choice(string.digits)] + \
                   [random.choice(allc) for _ in range(21)]
        random.shuffle(password)
        return ''.join(password)

    # dialog for entering password of the archive
    def check_password_dialog(self):
        # Dialog itself
        self.checkDialog = Adw.AlertDialog.new()
        self.checkDialog.set_heading(_("Unlock the archive with a password"))
        self.checkDialog.set_body(_("Enter the password below to unlock the archive with your configuration. If you have forgotten it, you will not be able to unzip the archive and start importing your configuration."))
        self.checkDialog.add_response("cancel", _("Cancel"))
        self.checkDialog.add_response("ok", _("Apply"))
        self.checkDialog.set_response_appearance('ok', Adw.ResponseAppearance.SUGGESTED)
        self.checkDialog.connect('response', self._checkDialog_closed)

        self.checkEntry = Adw.PasswordEntryRow.new()
        self.checkEntry.set_title(_("Password"))
        self.checkDialog.set_extra_child(self.checkEntry)
        self.checkDialog.present(self)

    # action after closing dialog for checking password
    def _checkDialog_closed(self, w, response):
        if response == 'ok':
            self.checkDialog.set_response_enabled("ok", False)
            with open(f"{CACHE}/temp_file", "w") as tmp:
                tmp.write(self.checkEntry.get_text())

            self.import_config()
        else:
            self._finish_import_session()

    # Save configuration
    def save_config(self):
        print("Saving the configuration is in progress…")
        self.archive_mode = "--create"
        self.archive_name = f"{self.folder}/{self.filename_text}"

        if not settings["save-without-archive"]:
            self.archive_name += ".sd.zip"

        self.status_title = _("<big><b>Saving configuration …</b></big>\nThe configuration of your desktop environment will be saved in:\n <i>{}/{}.sd.tar.gz</i>\n").split('</b>')[0].split('<b>')[-1]
        self.status_desc = self._set_status_desc_save()
        self.done_title = _("Configuration has been saved!")
        self.done_desc = _("<big><b>{}</b></big>\nYou can now view the archive with the configuration of your desktop environment, or return to the previous page.\n").format(_("Configuration has been saved!"))

        self.please_wait()
        save_thread = Thread(target=self._call_archive_command)
        save_thread.start()

    def _set_status_desc_save(self):
        status_old = _("<big><b>Saving configuration …</b></big>\nThe configuration of your desktop environment will be saved in:\n <i>{}/{}.sd.tar.gz</i>\n")
        status = status_old.replace("sd.tar.gz", "sd.zip") if not settings["save-without-archive"] else status_old.replace("sd.tar.gz", "")
        return status.format(self.folder, self.filename_text)

    # Import configuration
    def import_config(self):
        self.toast_overlay.dismiss_all()

        self.archive_name = self.path_to_import
        self.archive_mode = "--unpack"
        self.status_title = _("Restoring backup")
        self.status_desc = _("Preparing the selected components…")
        self.restore_options = {"job": str(uuid4())}
        session = self.import_session
        notice = Adw.Toast(title=_("Analyzing backup…"), timeout=0, button_label=_("Cancel"))
        notice.connect("button-clicked", lambda _toast: self._finish_import_session() if self.import_session is session else None)
        self.toast_overlay.add_toast(notice)
        Thread(target=self._inspect_backup, args=(session, self.archive_name), daemon=True).start()

    def _inspect_backup(self, session, archive_name):
        try:
            credentials = Path(CACHE) / "temp_file"
            password = credentials.read_text().strip() if credentials.is_file() else None
            with prepare_backup(archive_name, password, parent=CACHE) as root:
                paths = [str(path.relative_to(root)) for path in root.rglob("*")]
                folders = read_folder_metadata(root)
                general = root / "General" if (root / "General").is_dir() else root
                dump, wallpaper = split_wallpaper((general / "dconf-settings.ini").read_text(encoding="utf-8"))
                nemo_metadata = any(path in paths for path in ("General/NemoDesktop.ini", "DE/nemo/desktop-metadata", "nemo/desktop-metadata"))
                section = root / "Flatpak_Apps"
                try:
                    application_ids = sorted({app for _, app in desired_apps(
                        section if section.is_dir() else root, PROTECTED_APPS)})
                except ValueError:
                    application_ids = []
                available = {
                    "application_ids": application_ids,
                    "nemo_metadata": nemo_metadata,
                    "wallpaper": bool(wallpaper) or any(path in paths for path in ("General/backgrounds", "General/wallpapers", "backgrounds", "wallpapers")),
                    "configuration": filter_desktop_settings(dump, True) != filter_desktop_settings(dump, False)
                    or nemo_metadata,
                    "flatpak_apps": flatpak and any(Path(path).name in ("installed_flatpaks.sh", "installed_user_flatpaks.sh") for path in paths),
                    "flatpak_data": flatpak and any(Path(path).name == "flatpak-apps-data.tgz" or path.startswith("app/") for path in paths),
                    "contents": any(path in paths for path in ("General/desktop-folder.tgz", "desktop-folder.tgz")),
                }
        except (ValueError, OSError) as error:
            GLib.idle_add(self._inspect_failed, session, str(error))
        else:
            GLib.idle_add(self._show_restore_items, session, paths, folders, available)

    def _inspect_failed(self, session, error):
        if self.import_session is session:
            self._finish_import_session()
            self.show_err_msg(error)

    def _show_restore_items(self, session, paths, folders, available):
        if self.import_session is not session:
            return
        self.toast_overlay.dismiss_all()
        self.restore_has_nemo_metadata = available.get("nemo_metadata", False)
        cinnamon = bool(environment and environment["de_name"] == "Cinnamon")
        self.items_dialog = RestoreOptions(
            self, Path(self.archive_name).name, paths, folders, available, cinnamon,
            self._accept_restore_options, self._needs_restore_host,
        )
        self.items_dialog.present(self)

    def _needs_restore_host(self, options):
        return ((self.restore_has_nemo_metadata and options.get("desktop_configuration")) or
                options.get("flatpak_apps") or options.get("flatpak_data"))

    def _accept_restore_options(self, options):
        if options is None:
            self._finish_import_session()
            return
        self.restore_options.update(options)
        self._start_gui()

    def _start_gui(self):
        self.restore_states = {}
        self.archive_proc = None
        self.host_skip = False
        self.please_wait()
        self._add_restore_log()
        if self._needs_restore_host(self.restore_options):
            self._check_restore_host()
        else:
            self._run_archive_restore()

    def _run_archive_restore(self):
        self.cancel_button.set_sensitive(True)
        self.archive_proc = None
        self.status_page.set_child(None)
        self.status_page.set_description(_("Preparing the selected components…"))
        Thread(target=self._call_archive_command, args=(self.import_session,), daemon=True).start()

    def _check_restore_host(self, preparing=False):
        session = self.import_session
        service = APP_ID + ".RestoreHost"

        def unavailable():
            if preparing or self.import_session is not session:
                return
            try:
                prepare_session_launcher()
            except (OSError, ValueError, GLib.Error) as error:
                self._log_restore(str(error))
                self._restore_summary({}, "", error=True)
                return
            self.status_page.set_description(
                _("Open “SaveDesktop Plus — Set up restoration” from the system menu. "
                  "Restoration will continue automatically after this one-time setup or update.")
            )
            skip = Gtk.Button(label=_("Apply and finish at next sign-in"), halign=Gtk.Align.CENTER)
            self.status_page.set_child(skip)

            def skip_setup(_button):
                self.host_skip = True
                self._run_archive_restore()

            def check_again():
                if self.import_session is not session or self.host_skip or self.status_page.get_child() is not skip:
                    return False
                self._check_restore_host(preparing=True)
                return True

            skip.connect("clicked", skip_setup)
            GLib.timeout_add_seconds(2, check_again)

        def finished(connection, result):
            if self.import_session is not session or self.host_skip or self.import_cancelled:
                return
            try:
                protocol, version = connection.call_finish(result).unpack()
            except GLib.Error as error:
                if not preparing:
                    self._log_restore(str(error))
                unavailable()
                return
            if (protocol, version) != (HOST_PROTOCOL, HOST_BUILD):
                unavailable()
                return
            if not self.restore_host_ready:
                self.restore_host_ready = True
                self.status_page.set_child(None)
                self._stage_restore_for_confirmation(session, connection)

        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error as error:
            self._log_restore(str(error))
            unavailable()
            return
        bus.call(service, "/" + service.replace(".", "/"), service, "GetProtocol",
                 None, GLib.VariantType.new("(us)"), Gio.DBusCallFlags.NONE, 3000, None, finished)

    def _stage_restore_for_confirmation(self, session, bus):
        self.status_page.set_description(_("Preparing the selected components…"))
        job = self.restore_options["job"]
        command = [sys.executable, "-m", "savedesktop.core.archive", "--prepare-restore", self.archive_name]
        options = json.dumps(self.restore_options)

        def ready(completion, error):
            if self.import_session is not session or self.import_cancelled:
                self._remove_restore_staging(job)
                return False
            if error:
                self._log_restore(error)
                self._restore_summary({}, "", error=True)
                return False
            flags = (bool(completion.get("nemo_pending")), bool(completion.get("flatpaks_pending")))
            if not any(flags):
                self.status_page.set_description(_("Nothing requires the restore helper."))
                apply = Gtk.Button(label=_("Apply"), halign=Gtk.Align.CENTER)
                apply.connect("clicked", lambda _: self._run_archive_restore())
                self.status_page.set_child(apply)
                return False
            self.status_page.set_description(_("Confirm the selected changes in the system window…"))
            self.cancel_button.set_sensitive(False)
            service = APP_ID + ".RestoreHost"

            def approved(connection, result):
                if self.import_session is not session:
                    return
                try:
                    accepted, = connection.call_finish(result).unpack()
                except GLib.Error as problem:
                    self._log_restore(str(problem))
                    self._restore_summary({}, "", error=True)
                    return
                if accepted:
                    self.authorized_restore = (connection, job)
                    self._run_archive_restore()
                else:
                    self.cancel_button.set_sensitive(True)
                    self._cancel(None)

            bus.call(service, "/" + service.replace(".", "/"), service, "AuthorizeRestore",
                     GLib.Variant("(sbb)", (job, *flags)), GLib.VariantType.new("(b)"),
                     Gio.DBusCallFlags.NONE, 2 ** 31 - 1, None, approved)
            return False

        def prepare():
            try:
                proc = subprocess.Popen(command, text=True, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        env={**os.environ, "PYTHONPATH": app_prefix})
                if self.import_session is session:
                    self.archive_proc = proc
                if self.import_session is not session or self.import_cancelled:
                    proc.terminate()
                output, _unused = proc.communicate(options)
                completion = read_completion(output)
                error = output or _("The backup could not be prepared.") if proc.returncode or not completion else ""
            except (OSError, ValueError) as problem:
                completion, error = {}, str(problem)
            GLib.idle_add(ready, completion, error)

        Thread(target=prepare, daemon=True).start()

    @staticmethod
    def _remove_restore_staging(job):
        if job:
            shutil.rmtree(Path(CACHE) / "restore-jobs" / job, ignore_errors=True)

    def _add_restore_log(self):
        self.restore_host_ready = False
        listing = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        listing.set_margin_start(16)
        listing.set_margin_end(16)
        row = Adw.ExpanderRow(title=_("Diagnostic log"))
        listing.append(row)
        view = Gtk.TextView(editable=False, monospace=True, wrap_mode=Gtk.WrapMode.CHAR)
        self.restore_log = view.get_buffer()
        scroller = Gtk.ScrolledWindow(min_content_height=120, max_content_height=220)
        scroller.set_child(view)
        row.add_row(scroller)
        self.status_box.append(listing)

    def _log_restore(self, line):
        self.restore_log.insert(self.restore_log.get_end_iter(), line.rstrip() + "\n")
        return False

    def _restore_labels(self):
        return {
            "settings": _("Desktop environment settings"),
            "folders": _("My files and folders"),
            "wallpaper": _("Wallpaper"),
            "desktop_files": _("Desktop files and folders"),
            "desktop_layout": _("Desktop settings"),
            "flatpak_apps": _("Restore apps"),
            "flatpak_data": _("Restore app settings and data"),
            "prepare_apps": _("Preparing the selected Flatpak components…"),
        }

    def _restore_step(self, stage, state="running", detail=""):
        label = self._restore_labels().get(stage)
        if label:
            if state == "running":
                self.status_page.set_description(f"{label} · {detail}" if detail else label)
            if stage != "prepare_apps":
                self.restore_states[stage] = state
        return False

    def _call_archive_command(self, session=None):
        def deliver(callback, *args):
            if session is None or self.import_session is session:
                callback(*args)
            return False

        command = [sys.executable, "-m", "savedesktop.core.archive", self.archive_mode, self.archive_name]
        options = None
        if self.archive_mode == "--unpack":
            command.append("--restore-options-stdin")
            options = json.dumps(self.restore_options)
        proc = subprocess.Popen(
            command, stdout=None if options is None else subprocess.PIPE,
            stderr=subprocess.PIPE if options is None else subprocess.STDOUT,
            stdin=subprocess.PIPE if options is not None else None,
            text=True, env={**os.environ, "PYTHONPATH": f"{app_prefix}"},
        )
        if session is None or self.import_session is session:
            self.archive_proc = proc
        if session is not None and (self.import_session is not session or self.import_cancelled):
            proc.terminate()
        if options is None:
            _, err = proc.communicate()
        else:
            lines = []
            try:
                with proc.stdin as stream:
                    stream.write(options)
            except BrokenPipeError:
                pass
            for line in proc.stdout:
                if line.startswith(STEP):
                    try:
                        stage, state = json.loads(line[len(STEP):])
                    except (TypeError, ValueError):
                        lines.append(line)
                    else:
                        GLib.idle_add(deliver, self._restore_step, stage, state)
                else:
                    lines.append(line)
            proc.stdout.close()
            proc.wait()
            err = "".join(lines)
            if err:
                GLib.idle_add(deliver, self._log_restore, err)
        if err:
            print(err, end="", file=sys.stderr)
        status = proc.returncode
        message = err.strip() if status else operation_notices(err)
        completion = read_completion(err) if not status and options is not None else {}
        GLib.idle_add(deliver, self._archive_finished, status, message, options is not None, completion)

    def _archive_finished(self, status, message, restoring, completion):
        if restoring and (self.import_session is None or self.import_restart_requested):
            return
        if (restoring and self.import_cancelled) or status < 0 or status == 130:
            if restoring:
                self._finish_import_session()
            return
        if status == 0 and restoring:
            self.cancel_button.set_sensitive(False)
            if completion.get("cinnamon_shell"):
                session_type = os.environ.get("XDG_SESSION_TYPE", "").lower()
                if session_type == "x11":
                    self.import_restart_requested = True
                    self.status_page.set_description(_("Requesting a Cinnamon restart…"))
                    self.cancel_button.set_sensitive(False)
                    restart_cinnamon(session_type, lambda accepted: self._restore_done(message, completion, accepted))
                    return
            self._restore_done(message, completion, False)
            return
        if restoring:
            self._restore_summary(completion, message, error=True)
            return
        if status == 0:
            if message and not restoring:
                self.done_title = _("Backup created with warnings")
                self.done_desc = _("The backup was created. Some data could not be saved.")
            self.done()
            if message:
                self._show_restore_notice(message, restoring)
        else:
            self._set_default_widgets_state()
            self.show_err_msg(message)

    def _restore_done(self, message, completion, restart_requested):
        completion["shell_deferred"] = completion.get("cinnamon_shell") and not restart_requested
        if completion.get("nemo_pending"):
            self.restore_states["desktop_layout"] = "pending"
        if completion.get("flatpaks_pending"):
            for key in ("flatpak_apps", "flatpak_data"):
                if self.restore_options.get(key):
                    self.restore_states[key] = "pending"
        if not (completion.get("nemo_pending") or completion.get("flatpaks_pending")):
            self._restore_summary(completion, message)
            return
        if self.host_skip:
            completion["helper_unavailable"] = True
            self._restore_summary(completion, message)
            return

        self.status_page.set_description(_("Applying the remaining changes…"))
        self.cancel_button.set_sensitive(False)
        service = APP_ID + ".RestoreHost"
        path = "/" + service.replace(".", "/")
        job = self.restore_options["job"]

        def progress(_bus, _sender, _path, _iface, _signal, params, *_data):
            active_job, stage, detail = params.unpack()
            if active_job != job:
                return
            if stage == "log":
                self._log_restore(detail)
            elif stage.endswith("_result"):
                self._restore_step(stage.removesuffix("_result"), detail)
            else:
                self._restore_step(stage, detail=detail)

        def finished(bus, result):
            bus.signal_unsubscribe(subscription)
            try:
                nemo, apps = bus.call_finish(result).unpack()
            except GLib.Error as error:
                self._log_restore(str(error))
                completion["helper_unavailable"] = True
            else:
                if nemo != "none":
                    self.restore_states["desktop_layout"] = nemo
                if apps != "none":
                    for key in ("flatpak_apps", "flatpak_data"):
                        if self.restore_states.get(key) in ("pending", "running"):
                            self.restore_states[key] = apps
            self._restore_summary(completion, message)

        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error as error:
            self._log_restore(str(error))
            completion["helper_unavailable"] = True
            self._restore_summary(completion, message)
            return
        subscription = bus.signal_subscribe(service, service, "Progress", path, job,
                                            Gio.DBusSignalFlags.NONE, progress)
        self.authorized_restore = None
        bus.call(service, path, service, "StartRestore",
                 GLib.Variant("(sbb)", (job, bool(completion.get("nemo_pending")),
                                       bool(completion.get("flatpaks_pending")))),
                 GLib.VariantType.new("(ss)"), Gio.DBusCallFlags.NONE, 2 ** 31 - 1, None, finished)

    def _restore_summary(self, completion, message, *, error=False):
        self._finish_import_session()
        status_text = {
            "done": _("restored"),
            "pending": _("waiting for the next sign-in"),
            "deferred": _("waiting for the next sign-in"),
            "failed": _("could not be restored"),
            "partial": _("partially restored"),
            "running": _("could not be completed"),
        }
        if error:
            for key, selected in self.restore_options.items():
                if key in ("system_settings", "wallpaper", "desktop_contents", "desktop_configuration", "folders",
                           "flatpak_apps", "flatpak_data") and selected:
                    name = {"system_settings": "settings", "desktop_contents": "desktop_files", "desktop_configuration": "desktop_layout"}.get(key, key)
                    self.restore_states.setdefault(name, "failed")
        labels = self._restore_labels()
        lines = [f"{labels[key]} — {status_text[state]}" for key, state in self.restore_states.items()]
        if completion.get("helper_unavailable"):
            lines.append(_("The restore helper was unavailable. Pending components can be applied at the next sign-in."))
        if completion.get("shell_deferred"):
            lines.append(_("Sign out and sign in again to reload Cinnamon."))
        if self.restore_states.get("desktop_layout") == "deferred":
            lines.append(_("The desktop layout remains queued for the next sign-in."))
        if any(self.restore_states.get(key) == "partial" for key in ("flatpak_apps", "flatpak_data")):
            lines.append(_("Review the log. Close affected apps before retrying, and check availability of the installation sources."))
        if any(self.restore_states.get(key) == "partial" for key in ("folders", "desktop_files", "wallpaper")):
            lines.append(_("Existing files were kept. Files skipped from the backup are listed in the diagnostic log."))
        if message and not error:
            lines.append(_("Some settings or files were skipped. See the diagnostic log for details."))
        if error:
            lines.append(_("The operation stopped. See the diagnostic log for details."))
        elif not completion.get("shell_deferred") and all(state == "done" for state in self.restore_states.values()):
            lines.append(_("A computer restart is not required."))
        self.spinner.stop()
        self.status_box.remove(self.spinner)
        self.status_box.remove(self.cancel_button)
        title = _("Restore stopped") if error else _("Restore completed")
        if not error and (message or completion.get("shell_deferred") or
                          any(state != "done" for state in self.restore_states.values())):
            title = _("Restore completed with pending changes or warnings")
        self.set_title(title)
        self.status_page.set_title(title)
        self.status_page.set_description("\n".join(lines))
        self.status_page.set_icon_name("dialog-warning-symbolic" if error or title != _("Restore completed") else "done")
        close = Gtk.Button(label=_("Close"), halign=Gtk.Align.CENTER, css_classes=["pill", "suggested-action"])
        close.connect("clicked", lambda _: self._set_default_widgets_state())
        self.status_page.set_child(close)

    def _show_restore_notice(self, message, restoring):
        heading = _("Backup notice") if restoring else _("Backup created with warnings")
        dialog = Adw.AlertDialog.new(heading, message)
        dialog.add_response("close", _("Close"))
        dialog.present(self)

    # "Please wait" information page
    def please_wait(self):
        self.headerbar.set_title_widget(None)
        self.break_point.disconnect(self.apply_handler)
        self.switcher_bar.set_reveal(False)

        # Create box widget for this page
        self.status_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.status_box.set_vexpand(True)
        self.status_box.set_hexpand(True)
        self.status_box.set_size_request(-1, 100)
        self.status_box.set_valign(Gtk.Align.CENTER)
        self.scrolled.set_child(self.status_box)

        # Set bold title
        self.set_title(self.status_title)

        # Create spinner for this page
        self.spinner = Gtk.Spinner.new()
        self.spinner.set_size_request(100, 100)
        self.spinner.start()
        self.status_box.append(self.spinner)

        # Adw.StatusPage()
        self.status_page = Adw.StatusPage.new()
        self.status_page.set_description(self.status_desc)
        self.status_box.append(self.status_page)

        # Create button for cancel saving configuration
        self.cancel_button = Gtk.Button.new_with_label(_("Cancel"))
        self.cancel_button.add_css_class("pill")
        self.cancel_button.add_css_class("destructive-action")
        self.cancel_button.connect("clicked", self._cancel)
        self.cancel_button.set_valign(Gtk.Align.CENTER)
        self.cancel_button.set_halign(Gtk.Align.CENTER)
        self.status_box.append(self.cancel_button)

    # Stop Saving/Importing Configuration
    def _cancel(self, w):
        if self.import_session is not None:
            self.import_cancelled = True
            if getattr(self, "archive_proc", None) is None:
                self._finish_import_session()
        self._kill_process()
        self._set_default_widgets_state()
        for widget in [self.spinner, self.cancel_button, self.status_page]:
            self.status_box.remove(widget)

    # Kill a process for saving/importing configuration
    def _kill_process(self):
        if getattr(self, "archive_proc", None) is not None and self.archive_proc.poll() is None:
            self.archive_proc.terminate()
            print("The process was stopped.")

    # config has been saved action
    def done(self):
        self._send_notification()

        # stop spinner animation
        self.spinner.stop()
        self.status_box.remove(self.cancel_button)
        self.status_box.remove(self.spinner)

        # set title to "Configuration has been saved!"
        self.set_title(self.done_title)

        # Adw.StatusPage()
        self.status_page.set_icon_name("done")
        self.status_page.set_description(self.done_desc)

        # Box layout for the buttons below
        self.buttons_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.status_page.set_child(self.buttons_box)

        self._add_specific_button()

        # create button for backing to the previous page
        self.back_button = Gtk.Button.new_with_label(_("Back to previous page"))
        self.back_button.connect("clicked", self._back_to_main)
        self.back_button.add_css_class("pill")
        self.back_button.set_valign(Gtk.Align.CENTER)
        self.back_button.set_halign(Gtk.Align.CENTER)
        self.buttons_box.append(self.back_button)

    # send notification about saved configuration if application window is inactive only
    def _send_notification(self):
        self.notification_save = Gio.Notification.new(APP_NAME)
        self.notification_save.set_body(self.done_title)
        app = self.get_application()
        active_window = app.get_active_window()
        if active_window is None or not active_window.is_active():
            app.send_notification(None, self.notification_save)

    def _add_specific_button(self):
        if self.archive_mode == "--create":
            self.open_folder_button = Gtk.Button.new_with_label(_("Open the folder"))
            self.open_folder_button.add_css_class('pill')
            self.open_folder_button.add_css_class('suggested-action')
            self.open_folder_button.set_action_name("app.open_dir")
            self.open_folder_button.set_valign(Gtk.Align.CENTER)
            self.open_folder_button.set_halign(Gtk.Align.CENTER)
            self.buttons_box.append(self.open_folder_button)
    def _back_to_main(self, w):
        self._set_default_widgets_state()
        if self.archive_mode == "--create":
            widgets = [self.status_page, self.open_folder_button, self.back_button]
        else:
            widgets = [self.status_page, self.back_button]
        for widget in widgets:
            self.status_box.remove(widget)

    def _set_default_widgets_state(self):
        self.scrolled.set_child(self.headapp)
        self.headerbar.set_title_widget(self.switcher_title)
        self.set_title(APP_NAME)
        self.apply_handler = self.break_point.connect("apply", self.__on_break_point_apply)
        if self.get_default_size()[0] < 400:
            self.switcher_bar.set_reveal(True)

    # show message dialog in the error case
    def show_err_msg(self, error):
        self.error_str = str(error)
        self.errDialog = Adw.AlertDialog.new()
        self.errDialog.set_heading(heading=_("An error occurred"))
        self.errDialog.set_body(body=f"{self.error_str}")
        self.errDialog.add_response('cancel', _("Cancel"))
        self.errDialog.add_response('copy', _("Copy"))
        self.errDialog.set_response_appearance('copy', Adw.ResponseAppearance.SUGGESTED)
        self.errDialog.connect('response', self._errDialog_closed)
        self.errDialog.present(self)

    def _errDialog_closed(self, w, response):
        if response == "copy":
            self.clipboard = Gdk.Display.get_default().get_clipboard()
            self.clipboard_copy = Gdk.Clipboard.set(self.clipboard, self.error_str)

    # a warning indicating that the user must log out
    def show_warn_toast(self):
        self.toast.set_title(title=_("Changes will only take effect after the next login"))
        self.toast.set_button_label(_("Log Out"))
        self.toast.set_action_name("app.logout")
        self.toast_overlay.add_toast(self.toast)

    # action after closing the main window
    def on_close(self, w):
        if self.import_session is not None:
            self._finish_import_session()
        self.close()
        self._kill_process()

        # Save window size, state, and filename
        settings["window-size"] = self.get_default_size()
        settings["maximized"] = self.is_maximized()
        settings["filename"] = self.saveEntry.get_text()

        # Remove this file to disable expanding the "Periodic saving" row in the
        # More Options Dialog
        if os.path.exists(f"{CACHE}/expand_pb_row"):
            os.remove(f"{CACHE}/expand_pb_row")

        # Call remove_cache function using Thread
        rem_cache = Thread(target=self.remove_cache)
        rem_cache.start()

    # Remove this folder to cleanup unnecessary content created during saving,
    # importing, or syncing the configuration only if these files are not present
    def remove_cache(self):
        if os.path.exists(f"{CACHE}/workspace"):
            files_to_check = [f"{CACHE}/pb", f"{CACHE}/sync", f"{CACHE}/workspace/flatpaks_installer.py"]
            if not any(os.path.exists(path) for path in files_to_check):
                shutil.rmtree(f"{CACHE}/workspace")

