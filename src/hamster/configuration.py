# -*- coding: utf-8 -*-

# Copyright (C) 2008 Toms Bauģis <toms.baugis at gmail.com>

# This file is part of Project Hamster.

# Project Hamster is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# Project Hamster is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with Project Hamster.  If not, see <http://www.gnu.org/licenses/>.

try:
    import ConfigParser as configparser
except ImportError:
    import configparser

import os
from client import Storage
import logging
import datetime as dt
import gobject, gtk

import logging
log = logging.getLogger("configuration")

class Singleton(object):
    def __new__(cls, *args, **kwargs):
        if '__instance' not in vars(cls):
            cls.__instance = object.__new__(cls, *args, **kwargs)
        return cls.__instance

class RuntimeStore(Singleton):
    """
    Handles one-shot configuration that is not stored between sessions
    """
    database_path = ""
    database_file = None
    last_etag = None
    data_dir = ""
    home_data_dir = ""
    storage = None
    conf = None


    def __init__(self):
        try:
            import defs
            self.data_dir = os.path.join(defs.DATA_DIR, "hamster-applet")
            self.version = defs.VERSION
        except ImportError:
            # if defs is not there, we are running from sources
            module_dir = os.path.dirname(os.path.realpath(__file__))
            self.data_dir = os.path.join(module_dir, '..', '..', 'data')
            self.version = "uninstalled"

        self.data_dir = os.path.realpath(self.data_dir)


        self.storage = Storage()

        if os.environ.has_key('APPDATA'):
            self.home_data_dir = os.path.realpath(os.path.join(os.environ['APPDATA'], "hamster-applet"))
        else:
            logging.error("APPDATA variable is not set")
            raise Exception("APPDATA environment variable is not defined")

            

    @property
    def art_dir(self):
        return os.path.join(self.data_dir, "art")


runtime = RuntimeStore()


class OneWindow(object):
    def __init__(self, get_dialog_class):
        self.dialogs = {}
        self.get_dialog_class = get_dialog_class

    def on_dialog_destroy(self, params):
        del self.dialogs[params]
        #self.dialogs[params] = None

    def show(self, parent = None, **kwargs):
        params = str(sorted(kwargs.items())) #this is not too safe but will work for most cases

        if params in self.dialogs:
            self.dialogs[params].window.present()
        else:
            if parent:
                dialog = self.get_dialog_class()(parent, **kwargs)

                if isinstance(parent, gtk.Widget):
                    dialog.window.set_transient_for(parent.get_toplevel())

                # to make things simple, we hope that the target has defined self.window
                dialog.window.connect("destroy",
                                      lambda window, params: self.on_dialog_destroy(params),
                                      params)

            else:
                dialog = self.get_dialog_class()(**kwargs)

                # no parent means we close on window close
                dialog.window.connect("destroy",
                                      lambda window, params: gtk.main_quit(),
                                      params)


            self.dialogs[params] = dialog

class Dialogs(Singleton):
    """makes sure that we have single instance open for windows where it makes
       sense"""
    def __init__(self):
        def get_edit_class():
            from edit_activity import CustomFactController
            return CustomFactController
        self.edit = OneWindow(get_edit_class)

        def get_overview_class():
            from overview import Overview
            return Overview
        self.overview = OneWindow(get_overview_class)

        def get_stats_class():
            from stats import Stats
            return Stats
        self.stats = OneWindow(get_stats_class)

        def get_about_class():
            from about import About
            return About
        self.about = OneWindow(get_about_class)

        def get_prefs_class():
            from preferences import PreferencesEditor
            return PreferencesEditor
        self.prefs = OneWindow(get_prefs_class)

dialogs = Dialogs()


def load_ui_file(name):
    ui = gtk.Builder()
    ui.add_from_file(os.path.join(runtime.data_dir, name))
    return ui

class INIStore(gobject.GObject, Singleton):
    """
    Settings implementation which stores settings in an INI file.
    """
    SECTION = 'Settings'    # Section to read/store settings in INI file
    VALID_KEY_TYPES = (bool, str, int, list, tuple)
    #TODO: Remove non-Windows related settings
    DEFAULTS = {
        'enable_timeout'              :   False,       # Should hamster stop tracking on idle
        'stop_on_shutdown'            :   False,       # Should hamster stop tracking on shutdown
        'notify_on_idle'              :   False,       # Remind also if no activity is set
        'notify_interval'             :   27,          # Remind of current activity every X minutes
        'day_start_minutes'           :   5 * 60 + 30, # At what time does the day start (5:30AM)
        'overview_window_box'         :   [],          # X, Y, W, H
        'overview_window_maximized'   :   False,       # Is overview window maximized
        'workspace_tracking'          :   [],          # Should hamster switch activities on workspace change 0,1,2
        'workspace_mapping'           :   [],          # Mapping between workspace numbers and activities
        'standalone_window_box'       :   [],          # X, Y, W, H
        'standalone_window_maximized' :   False,       # Is overview window maximized
        'activities_source'           :   "",          # Source of TODO items ("", "evo", "gtg")
        'last_report_folder'          :   "~",         # Path to directory where the last report was saved
    }

    __gsignals__ = {
        "conf-changed": (gobject.SIGNAL_RUN_LAST, gobject.TYPE_NONE, (gobject.TYPE_PYOBJECT, gobject.TYPE_PYOBJECT))
    }
    def __init__(self):
        self._client = configparser.RawConfigParser()

        #TODO: Store file in home_data_dir
        self.config = "hamster.ini"
        if not os.path.isfile(self.config):
            self._client.add_section(self.SECTION)
            self._flush()
        try:
            self._client.read(self.config)
        except IOError,e:
            log.error("Error reading configurationfile: %s" % e)
            raise
        
        gobject.GObject.__init__(self)
        self._notifications = []

    def _flush(self):
        """Write and re-read configuration values in INI file"""
        try:
            fcfg = open(self.config,'w')
            self._client.write(fcfg)
            fcfg.close()
            self._client.read(self.config)
        except IOError,e:
            log.error("Error writing to configuration file: %s" % e)
            raise

    def _fix_key(self, key):
        """
        Appends the GCONF_PREFIX to the key if needed

        @param key: The key to check
        @type key: C{string}
        @returns: The fixed key
        @rtype: C{string}
        """
        #TODO: Remove calls to this function
        return key

    def _get_value(self, key, default):
        """calls appropriate configparser function by the default value"""
        vtype = type(default)
        try:
            if vtype is bool:
                return self._client.getboolean(self.SECTION, key)
            elif vtype is str:
                return self._client.get(self.SECTION, key)
            elif vtype is int:
                return self._client.getint(self.SECTION, key)
            elif vtype in (list, tuple):
                l = []
                temp = self._client.get(self.SECTION, key)
                for i in temp.split(','):
                    l.append(i.strip())
                return l
        except configparser.NoOptionError:
            return None
        except TypeError:
            return None
        except AttributeError:
            return None

        return None

    def get(self, key, default=None):
        """
        Returns the value of the key or the default value if the key is
        not yet in config
        """

        #function arguments override defaults
        if default is None:
            default = self.DEFAULTS.get(key, None)
        vtype = type(default)

        #we now have a valid key and type
        if default is None:
            log.warn("Unknown key: %s, must specify default value" % key)
            return None

        if vtype not in self.VALID_KEY_TYPES:
            log.warn("Invalid key type: %s" % vtype)
            return None

        #for gconf refer to the full key path
        #key = self._fix_key(key)

        #if key not in self._notifications:
        #    self._notifications.append(key)

        value = self._get_value(key, default)
        if value is None:
            self.set(key, default)
            return default
        elif value is not None:
            return value

        log.warn("Unknown gconf key: %s" % key)
        return None

    def set(self, key, value):
        """
        Sets the key value in gconf and connects adds a signal
        which is fired if the key changes
        """
        log.debug("Settings %s -> %s" % (key, value))
        if key in self.DEFAULTS:
            vtype = type(self.DEFAULTS[key])
        else:
            vtype = type(value)

        if vtype not in self.VALID_KEY_TYPES:
            log.warn("Invalid key type: %s" % vtype)
            return False

        #for gconf refer to the full key path
        #key = self._fix_key(key)

        if vtype is bool:
            self._client.set(self.SECTION, key, value)
        elif vtype is str:
            self._client.set(self.SECTION, key, value)
        elif vtype is int:
            self._client.set(self.SECTION, key, value)
        elif vtype in (list, tuple):
            # flatten list/tuple
            self._client.set(self.SECTION, key, ",".join([str(i) for i in value]))

        self._flush()

        self.emit('conf-changed', key, value)
        return True


conf = INIStore()
