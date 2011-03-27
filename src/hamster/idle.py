# - coding: utf-8 -

# Copyright (C) 2008 Patryk Zawadzki <patrys at pld-linux.org>

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
#
import logging
import datetime as dt
import gobject
import ctypes, ctypes.wintypes
try:
    import _winreg as winreg
except ImportError:
    try:
        import winreg
    except ImportError:
        winreg = None

class IdleListener(gobject.GObject):
    """
    Listen for idleness

    Monitors the system for idleness.  There are two types, implicit (due to
    inactivity) and explicit (locked screen), that need to be handled differently.
    An implicit idle state should subtract the time-to-become-idle (as specified
    in the configuration) from the last activity but an explicit idle state should
    not.
    """

    __gsignals__ = {
        "idle-changed": (gobject.SIGNAL_RUN_LAST, gobject.TYPE_NONE, (gobject.TYPE_PYOBJECT,))
    }
    def __init__(self):
        gobject.GObject.__init__(self)

        self.screen_locked = False
        self.idle_from = None
        self.timeout = self.get_screensaver_timeout()
        gobject.timeout_add_seconds(5, self.check_idle)

    def check_idle(self):
        # Check idleness or screen lock
        idletime = self.get_idle_time()
        self.screen_locked = self.is_screenlocked()
        if idletime >= self.timeout or self.screen_locked:
            if self.idle_from is None:
                logging.debug("idle/screenlock detected")
                self.idle_from = dt.datetime.now()
                self.emit('idle-changed', 1)
        elif self.idle_from is not None: # user is back
            self.idle_from = None
            self.emit('idle-changed', 0)

        return True
        

    def get_idle_time(self):
        """ Returns the idle time in seconds """

        class LASTINPUTINFO(ctypes.Structure):
            """ LastInputInfo struct (http://msdn.microsoft.com/en-us/library/ms646272%28v=VS.85%29.aspx) """
            _fields_ = [
                ("cbSize", ctypes.wintypes.UINT),
                ("dwTime",  ctypes.wintypes.DWORD),
            ]

        lastinput = LASTINPUTINFO()
        lastinput.cbSize = ctypes.sizeof(lastinput) # this member must be set to sizeof(LASTINPUTINFO)
    
        ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lastinput))
        ticks = ctypes.windll.kernel32.GetTickCount() - lastinput.dwTime
    
        return ticks / 1000.0  # convert milliseconds to seconds

    def get_idle_from(self):
        """ Return time when the user went idle """
        if not self.idle_from:
            return dt.datetime.now()

        if self.screen_locked:
            return self.idle_from
        else:
            return self.idle_from - dt.timedelta(seconds = self.timeout)

    def get_screensaver_timeout(self):
        """ Returns the Windows screensaver timeout (in seconds) """

        if winreg == None: return 600  # default to 10 minutes

        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop")
            value = winreg.QueryValueEx(key, "ScreenSaveTimeOut")[0]
            key.Close()
            return int(value)
        except:
            logging.warn("WARNING - Failed to get screensaver timeout")
            return 600
        
        
    def is_screenlocked(self):
        # if screen is locked, GetForegroundWindow() returns 0
        if ctypes.windll.user32.GetForegroundWindow() == 0:
            return True
        else:
            return False


