'''Mock D-Bus services for automated testing of KDE Notification Center'''

from typing import Any
import dbus
import dbus.service


class MockNotificationServer(dbus.service.Object):
    '''Mock org.freedesktop.Notifications and org.kde.NotificationManager service'''

    def __init__(self, bus: dbus.SessionBus) -> None:
        self.bus = bus
        self.bus_name = dbus.service.BusName("org.freedesktop.Notifications", bus=self.bus)
        super().__init__(bus, "/org/freedesktop/Notifications")

        self.next_id = 1
        self.received_notifies: list[dict[str, Any]] = list()
        self.closed_ids: list[int] = list()
        self.invoked_actions: list[tuple[int, str]] = list()
        self.replies_received: list[tuple[int, str]] = list()

        self.bus.add_signal_receiver(
            self._on_notification_replied,
            signal_name="NotificationReplied",
            dbus_interface="org.freedesktop.Notifications",
            path="/org/freedesktop/Notifications",
        )

    def _on_notification_replied(self, nid: int, text: str) -> None:
        '''Signal handler for NotificationReplied'''

        self.replies_received.append((int(nid), str(text)))

    @dbus.service.method(
        "org.freedesktop.Notifications",
        in_signature="susssasa{sv}i",
        out_signature="u",
    )
    def Notify(
        self,
        app_name       : str,
        replaces_id    : int,
        app_icon       : str,
        summary        : str,
        body           : str,
        actions        : list,
        hints          : dict,
        expire_timeout : int,
    ) -> int:
        '''Mock Notify implementation'''

        nid = replaces_id if replaces_id > 0 else self.next_id
        if replaces_id <= 0:
            self.next_id += 1

        self.received_notifies.append({
            "id"            : nid,
            "app_name"      : str(app_name),
            "replaces_id"   : int(replaces_id),
            "app_icon"      : str(app_icon),
            "summary"       : str(summary),
            "body"          : str(body),
            "actions"       : [str(a) for a in actions],
            "hints"         : dict(hints),
            "expire_timeout": int(expire_timeout),
        })
        return nid

    @dbus.service.method(
        "org.freedesktop.Notifications",
        in_signature="u",
        out_signature="",
    )
    def CloseNotification(self, nid: int) -> None:
        '''Mock CloseNotification implementation'''

        self.closed_ids.append(int(nid))
        self.NotificationClosed(dbus.UInt32(nid), dbus.UInt32(3))

    @dbus.service.method(
        "org.freedesktop.Notifications",
        in_signature="",
        out_signature="as",
    )
    def GetCapabilities(self) -> list[str]:
        '''Mock server capabilities'''

        return ["actions", "body", "inline-reply"]

    @dbus.service.method(
        "org.freedesktop.Notifications",
        in_signature="",
        out_signature="ssss",
    )
    def GetServerInformation(self) -> tuple[str, str, str, str]:
        '''Mock server information'''

        return ("MockNotificationServer", "Mock", "1.0", "1.2")

    @dbus.service.method(
        "org.kde.NotificationManager",
        in_signature="us",
        out_signature="",
    )
    def InvokeAction(self, nid: int, action_key: str) -> None:
        '''Mock InvokeAction implementation'''

        self.invoked_actions.append((int(nid), str(action_key)))
        self.ActionInvoked(dbus.UInt32(nid), dbus.String(action_key))

    @dbus.service.method(
        "org.freedesktop.Notifications",
        in_signature="",
        out_signature="a{sv}",
    )
    def GetTestStats(self) -> dict[str, Any]:
        '''Return test execution stats for assertions'''

        return {
            "closed_ids": dbus.Array(
                [dbus.UInt32(x) for x in self.closed_ids],
                signature="u",
            ),
            "invoked_actions": dbus.Array(
                [dbus.String(f"{nid}:{act}") for nid, act in self.invoked_actions],
                signature="s",
            ),
            "replies_received": dbus.Array(
                [dbus.String(f"{nid}:{txt}") for nid, txt in self.replies_received],
                signature="s",
            ),
        }

    @dbus.service.signal("org.freedesktop.Notifications", signature="uu")
    def NotificationClosed(self, nid: int, reason: int) -> None:
        pass

    @dbus.service.signal("org.freedesktop.Notifications", signature="us")
    def ActionInvoked(self, nid: int, action_key: str) -> None:
        pass


class MockStatusNotifierWatcher(dbus.service.Object):
    '''Mock org.kde.StatusNotifierWatcher service'''

    def __init__(self, bus: dbus.SessionBus) -> None:
        self.bus = bus
        self.bus_name = dbus.service.BusName("org.kde.StatusNotifierWatcher", bus=self.bus)
        super().__init__(bus, "/StatusNotifierWatcher")
        self.registered_items: list[str] = list()

    @dbus.service.method(
        "org.kde.StatusNotifierWatcher",
        in_signature="s",
        out_signature="",
    )
    def RegisterStatusNotifierItem(self, service: str) -> None:
        '''Mock RegisterStatusNotifierItem'''

        self.registered_items.append(str(service))

    @dbus.service.method(
        "org.kde.StatusNotifierWatcher",
        in_signature="",
        out_signature="as",
    )
    def GetRegisteredItems(self) -> list[str]:
        '''Return registered StatusNotifierItem list for test verification'''

        return self.registered_items

    @dbus.service.method(
        "org.freedesktop.DBus.Properties",
        in_signature="ss",
        out_signature="v",
    )
    def Get(self, iface: str, prop: str) -> Any:
        '''Mock property getter'''

        if prop == "IsStatusNotifierHostRegistered":
            return dbus.Boolean(True)
        return False


def main() -> None:
    '''Run standalone mock D-Bus loop process'''

    from dbus.mainloop.glib import DBusGMainLoop, threads_init
    from gi.repository import GLib

    threads_init()
    DBusGMainLoop(set_as_default=True)

    bus = dbus.SessionBus()
    _server = MockNotificationServer(bus)
    _watcher = MockStatusNotifierWatcher(bus)

    loop = GLib.MainLoop()
    print("MOCK_SERVER_READY", flush=True)
    try:
        loop.run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
