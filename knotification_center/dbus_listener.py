'''D-Bus background listener and controller for KDE and Freedesktop notifications'''

import threading
from typing import Any, Optional

import dbus
import dbus.lowlevel
from dbus.mainloop.glib import DBusGMainLoop, threads_init
from PyQt6.QtCore import QObject, pyqtSignal

from knotification_center import constants
from knotification_center.database import NotificationDB
from knotification_center import util

try:
    threads_init()
    DBusGMainLoop(set_as_default=True)
except Exception:
    pass

LOGGER = util.LOGGER


class DBusNotificationWorker(QObject):
    '''Worker thread managing D-Bus eavesdropping, signals, and method calls'''

    notification_added  = pyqtSignal(dict)
    notification_closed = pyqtSignal(int, int)
    dbus_id_updated     = pyqtSignal(int, int)
    dnd_changed         = pyqtSignal(bool)

    def __init__(self, db: NotificationDB, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.db                = db
        self.bus               : Optional[dbus.SessionBus]  = None
        self.monitor_bus       : Optional[dbus.SessionBus]  = None
        self.notify_iface      : Optional[dbus.Interface]   = None
        self.kde_notif_mgr     : Optional[dbus.Interface]   = None
        self._pending_notifies : dict[tuple[Any, Any], int] = dict()

    def start_listening(self) -> None:
        '''Initialize D-Bus connections for commands and message monitoring'''

        LOGGER.info("Starting D-Bus eavesdrop listener")
        self.bus = dbus.SessionBus()
        self.monitor_bus = dbus.SessionBus(private=True)

        try:
            proxy = self.bus.get_object(
                constants.DBUS_NOTIF_SERVICE,
                constants.DBUS_NOTIF_PATH,
            )
            self.notify_iface = dbus.Interface(
                proxy,
                constants.DBUS_NOTIF_IFACE,
            )
            try:
                self.kde_notif_mgr = dbus.Interface(
                    proxy,
                    constants.DBUS_KDE_NOTIF_MGR,
                )
            except Exception:
                self.kde_notif_mgr = None
        except Exception as e:
            LOGGER.warning("Could not connect to notification proxy: %s", e)

        self.monitor_bus.add_message_filter(self._filter_message)

        rules = [
            (
                f"type='method_call',interface='{constants.DBUS_NOTIF_IFACE}',"
                f"member='Notify'"
            ),
            "type='method_return'",
            (
                f"type='signal',interface='{constants.DBUS_NOTIF_IFACE}',"
                f"member='NotificationClosed'"
            ),
            (
                f"type='signal',interface='{constants.DBUS_PROPERTIES_IFACE}',"
                f"member='PropertiesChanged',path='{constants.DBUS_NOTIF_PATH}'"
            ),
        ]

        try:
            dbus_obj = self.monitor_bus.get_object(
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
            )
            mon_iface = dbus.Interface(dbus_obj, "org.freedesktop.DBus.Monitoring")
            mon_iface.BecomeMonitor(rules, dbus.UInt32(0))
            LOGGER.info("Registered D-Bus monitor via BecomeMonitor")
        except Exception as e:
            LOGGER.warning(
                "BecomeMonitor unavailable (%s); falling back to legacy rules",
                e,
            )
            self.monitor_bus.add_match_string_non_blocking(
                f"eavesdrop=true,interface='{constants.DBUS_NOTIF_IFACE}'"
            )
            self.monitor_bus.add_match_string_non_blocking(
                "type='method_return',eavesdrop=true"
            )
            self.monitor_bus.add_match_string_non_blocking(
                f"type='signal',interface='{constants.DBUS_NOTIF_IFACE}',"
                f"eavesdrop=true"
            )
            self.monitor_bus.add_match_string_non_blocking(
                f"type='signal',interface='{constants.DBUS_PROPERTIES_IFACE}',"
                f"member='PropertiesChanged',path='{constants.DBUS_NOTIF_PATH}'"
            )

    def cleanup(self) -> None:
        '''Clean up D-Bus monitor connection'''

        if self.monitor_bus:
            try:
                self.monitor_bus.close()
            except Exception:
                pass
            self.monitor_bus = None

    def _filter_message(self, bus: dbus.SessionBus, message: dbus.lowlevel.Message) -> None:
        '''Filter incoming D-Bus messages from private connection'''

        try:
            mtype = message.get_type()
            # 1: Method call
            if mtype == 1 and message.get_interface() == constants.DBUS_NOTIF_IFACE:
                if message.get_member() == "Notify":
                    self._handle_notify_call(message)
            # 2: Method return
            elif mtype == 2:
                self._handle_method_return(message)
            # 4: Signal
            elif mtype == 4:
                member = message.get_member()
                if member == "NotificationClosed":
                    self._handle_notification_closed(message)
                elif member == "PropertiesChanged":
                    args = message.get_args_list()
                    if len(args) >= 2 and args[0] == constants.DBUS_NOTIF_IFACE:
                        changed = args[1]
                        if "Inhibited" in changed:
                            inhibited = bool(changed["Inhibited"])
                            LOGGER.debug("D-Bus DND Inhibited state changed: %s", inhibited)
                            self.dnd_changed.emit(inhibited)
        except Exception as e:
            LOGGER.error("Error processing D-Bus message: %s", e)

    def _parse_urgency(self, val: Any) -> int:
        '''Extract numeric urgency from D-Bus byte, integer, or string payload'''

        try:
            return int(val)
        except (ValueError, TypeError):
            if isinstance(val, (bytes, bytearray)) and len(val) == 1:
                return val[0]
            if isinstance(val, str) and len(val) == 1:
                return ord(val)
            return int(constants.Urgency.NORMAL)

    def _handle_notify_call(self, message: dbus.lowlevel.Message) -> None:
        '''Process incoming Notify method call intercepted over D-Bus'''

        args = message.get_args_list()
        if len(args) < 7:
            return

        app_name    = str(args[0]) or "Unknown"
        replaces_id = int(args[1]) if len(args) > 1 else 0
        app_icon    = str(args[2]) if len(args) > 2 else ""
        summary     = str(args[3]) if len(args) > 3 else ""
        body        = str(args[4]) if len(args) > 4 else ""

        raw_actions = args[5] if len(args) > 5 else list()
        actions     = [str(a) for a in raw_actions]

        raw_hints = args[6] if len(args) > 6 else dict()
        hints     : dict[str, Any] = dict()
        urgency   = int(constants.Urgency.NORMAL)
        category  = ""

        if isinstance(raw_hints, dict):
            for k, v in raw_hints.items():
                k_str = str(k)
                if k_str in (
                    "image-data",
                    "image_data",
                    "icon_data",
                    "x-kde-raw-icon-data",
                    "image-data-raw",
                ):
                    continue
                if k_str == "urgency":
                    urgency = self._parse_urgency(v)
                elif k_str == "category":
                    category = str(v)
                hints[k_str] = str(v)

        LOGGER.debug(
            "Captured Notify call: app=%s, replaces_id=%d, summary=%s",
            app_name,
            replaces_id,
            summary,
        )

        internal_id = self.db.insert_notification(
            app_name=app_name,
            summary=summary,
            body=body,
            app_icon=app_icon,
            urgency=urgency,
            category=category,
            actions=actions,
            hints=hints,
            dbus_id=replaces_id,
        )

        sender = message.get_sender()
        serial = message.get_serial()
        if len(self._pending_notifies) > 200:
            oldest_key = next(iter(self._pending_notifies))
            self._pending_notifies.pop(oldest_key, None)
        self._pending_notifies[(sender, serial)] = internal_id

        data = {
            "id"      : internal_id,
            "dbus_id" : replaces_id,
            "app_name": app_name,
            "app_icon": app_icon,
            "summary" : summary,
            "body"    : body,
            "urgency" : urgency,
            "category": category,
            "actions" : actions,
            "hints"   : hints,
            "status"  : constants.Status.ACTIVE.value,
        }
        self.notification_added.emit(data)

    def _handle_method_return(self, message: dbus.lowlevel.Message) -> None:
        '''Map Notify method return serial back to internal notification ID'''

        destination = message.get_destination()
        reply_serial = message.get_reply_serial()
        key = (destination, reply_serial)

        if key not in self._pending_notifies:
            return

        args = message.get_args_list()
        if not args:
            return

        internal_id = self._pending_notifies.pop(key)
        try:
            val = args[0]
            if isinstance(val, (int, dbus.UInt32, dbus.Int32, dbus.Byte)) or (
                isinstance(val, str) and val.isdigit()
            ):
                dbus_id = int(val)
                self.db.update_dbus_id(internal_id, dbus_id)
                self.dbus_id_updated.emit(internal_id, dbus_id)
            else:
                LOGGER.debug(
                    "Method return for id %d did not return an integer: %r",
                    internal_id,
                    type(val),
                )
        except Exception as e:
            LOGGER.error("Failed mapping method return to dbus_id: %s", e)

    def _handle_notification_closed(self, message: dbus.lowlevel.Message) -> None:
        '''Handle NotificationClosed signal from desktop notification server'''

        args = message.get_args_list()
        if len(args) >= 2:
            dbus_id = int(args[0])
            reason = int(args[1])
            LOGGER.debug("Captured NotificationClosed: dbus_id=%d, reason=%d", dbus_id, reason)
            if reason in (2, 3):
                self.db.mark_dismissed_by_dbus_id(dbus_id)
            self.notification_closed.emit(dbus_id, reason)

    def close_notification_on_bus(self, dbus_id: int) -> None:
        '''Invoke CloseNotification on notification daemon'''

        if not dbus_id or not self.bus:
            return

        def _do_close() -> None:
            try:
                LOGGER.debug("Calling CloseNotification on bus: dbus_id=%d", dbus_id)
                if self.notify_iface:
                    self.notify_iface.CloseNotification(dbus.UInt32(dbus_id))
            except Exception as e:
                LOGGER.debug("CloseNotification(%d) exception: %s", dbus_id, e)

        threading.Thread(target=_do_close, daemon=True).start()

    def close_multiple_on_bus(self, dbus_ids: list[int]) -> None:
        '''Invoke CloseNotification for multiple notification IDs'''

        if not dbus_ids or not self.bus:
            return

        def _do_close() -> None:
            for did in dbus_ids:
                if not did:
                    continue
                try:
                    LOGGER.debug("Batch CloseNotification on bus: dbus_id=%d", did)
                    if self.notify_iface:
                        self.notify_iface.CloseNotification(dbus.UInt32(did))
                except Exception as e:
                    LOGGER.debug("Batch CloseNotification(%d) exception: %s", did, e)

        threading.Thread(target=_do_close, daemon=True).start()

    def invoke_action_on_bus(self, dbus_id: int, action_key: str) -> None:
        '''Invoke action on notification via KDE NotificationManager or Freedesktop proxy'''

        if not dbus_id or not self.bus:
            return

        def _do_invoke() -> None:
            try:
                LOGGER.debug(
                    "Invoking action on bus: dbus_id=%d, action_key=%s",
                    dbus_id,
                    action_key,
                )
                if self.kde_notif_mgr:
                    self.kde_notif_mgr.InvokeAction(dbus.UInt32(dbus_id), action_key)
                elif self.bus:
                    proxy = self.bus.get_object(
                        constants.DBUS_NOTIF_SERVICE,
                        constants.DBUS_NOTIF_PATH,
                    )
                    iface = dbus.Interface(proxy, constants.DBUS_KDE_NOTIF_MGR)
                    iface.InvokeAction(dbus.UInt32(dbus_id), action_key)
            except Exception as e:
                LOGGER.warning(
                    "InvokeAction failed for dbus_id=%d, action_key=%s: %s",
                    dbus_id,
                    action_key,
                    e,
                )

        threading.Thread(target=_do_invoke, daemon=True).start()

    def send_reply_on_bus(
        self,
        dbus_id    : int,
        reply_text : str,
        action_key : Optional[str] = None,
    ) -> None:
        '''Send inline reply by emitting NotificationReplied and invoking reply action'''

        if not dbus_id or not self.bus:
            return

        def _do_reply() -> None:
            if not self.bus:
                return
            try:
                LOGGER.info("Sending inline reply on bus: dbus_id=%d, text=%r", dbus_id, reply_text)
                msg = dbus.lowlevel.SignalMessage(
                    constants.DBUS_NOTIF_PATH,
                    constants.DBUS_NOTIF_IFACE,
                    "NotificationReplied",
                )
                msg.append(dbus.UInt32(dbus_id), signature="u")
                msg.append(dbus.String(reply_text), signature="s")
                self.bus.send_message(msg)

                if action_key:
                    LOGGER.debug("Invoking associated action key for reply: %s", action_key)
                    if self.kde_notif_mgr:
                        self.kde_notif_mgr.InvokeAction(dbus.UInt32(dbus_id), action_key)
            except Exception as e:
                LOGGER.error("send_reply_on_bus failed for dbus_id=%d: %s", dbus_id, e)

        threading.Thread(target=_do_reply, daemon=True).start()
