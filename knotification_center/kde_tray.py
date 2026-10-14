'''KDE StatusNotifierItem and DBusMenu integration for native Plasma system tray'''

from os import getpid
from typing import Any, Callable, Optional

import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop, threads_init
from PyQt6.QtCore import QObject, QPoint, pyqtSignal
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QMenu

threads_init()
DBusGMainLoop(set_as_default=True)

from knotification_center import constants
from knotification_center import util

LOGGER = util.LOGGER


class StatusNotifierItemDBusService(dbus.service.Object):
    '''Exports org.kde.StatusNotifierItem D-Bus interface for Plasma panel/drawer integration'''

    def __init__(
        self,
        bus         : dbus.SessionBus,
        path        : str,
        parent_item : "KDEStatusNotifierItem",
    ) -> None:
        super().__init__(bus, path)
        self.item = parent_item

    @dbus.service.method(
        "org.freedesktop.DBus.Properties",
        in_signature="ss",
        out_signature="v",
    )
    def Get(self, interface_name: str, property_name: str) -> Any:
        '''Retrieve a property on this D-Bus object'''

        props = self.GetAll(interface_name)
        if property_name in props:
            return props[property_name]
        raise dbus.exceptions.DBusException(f"Unknown property: {property_name}")

    @dbus.service.method(
        "org.freedesktop.DBus.Properties",
        in_signature="s",
        out_signature="a{sv}",
    )
    def GetAll(self, interface_name: str) -> dict[str, Any]:
        '''Retrieve all properties on the org.kde.StatusNotifierItem interface'''

        if interface_name == "org.kde.StatusNotifierItem":
            tt = dbus.Struct(
                (
                    dbus.String(self.item.icon_name),
                    dbus.Array(list(), signature="(iiay)"),
                    dbus.String(self.item.tooltip_title),
                    dbus.String(self.item.tooltip_sub),
                ),
                signature="(sa(iiay)ss)",
            )
            return {
                "Category"          : dbus.String("ApplicationStatus"),
                "Id"                : dbus.String(constants.APP_ID),
                "Title"             : dbus.String(self.item.title),
                "Status"            : dbus.String(self.item.status),
                "WindowId"          : dbus.Int32(0),
                "IconThemePath"     : dbus.String(str(constants.ICONS_DIR)),
                "IconName"          : dbus.String(self.item.icon_name),
                "OverlayIconName"   : dbus.String(""),
                "AttentionIconName" : dbus.String(self.item.icon_name),
                "AttentionMovieName": dbus.String(""),
                "Menu"              : dbus.ObjectPath("/MenuBar"),
                "ItemIsMenu"        : dbus.Boolean(False),
                "ToolTip"           : tt,
            }
        return dict()

    @dbus.service.method("org.kde.StatusNotifierItem", in_signature="ii", out_signature="")
    def Activate(self, x: int, y: int) -> None:
        '''Primary activation triggered on tray click'''

        LOGGER.debug("StatusNotifierItem Activate at (%d, %d)", x, y)
        self.item.sig_activate.emit()

    @dbus.service.method("org.kde.StatusNotifierItem", in_signature="ii", out_signature="")
    def SecondaryActivate(self, x: int, y: int) -> None:
        '''Secondary activation triggered on middle click'''

        LOGGER.debug("StatusNotifierItem SecondaryActivate at (%d, %d)", x, y)
        self.item.sig_secondary_activate.emit()

    @dbus.service.method("org.kde.StatusNotifierItem", in_signature="ii", out_signature="")
    def ContextMenu(self, x: int, y: int) -> None:
        '''Context menu request triggered on right click'''

        LOGGER.debug("StatusNotifierItem ContextMenu at (%d, %d)", x, y)
        self.item.sig_context_menu.emit(x, y)

    @dbus.service.method("org.kde.StatusNotifierItem", in_signature="is", out_signature="")
    def Scroll(self, delta: int, orientation: str) -> None:
        pass

    @dbus.service.method("org.kde.StatusNotifierItem", in_signature="s", out_signature="")
    def ProvideXdgActivationToken(self, token: str) -> None:
        pass

    @dbus.service.signal("org.kde.StatusNotifierItem", signature="s")
    def NewStatus(self, status: str) -> None:
        pass

    @dbus.service.signal("org.kde.StatusNotifierItem", signature="")
    def NewIcon(self) -> None:
        pass

    @dbus.service.signal("org.kde.StatusNotifierItem", signature="")
    def NewTitle(self) -> None:
        pass

    @dbus.service.signal("org.kde.StatusNotifierItem", signature="")
    def NewToolTip(self) -> None:
        pass

    @dbus.service.signal("org.kde.StatusNotifierItem", signature="")
    def NewAttentionIcon(self) -> None:
        pass

    @dbus.service.signal("org.kde.StatusNotifierItem", signature="")
    def NewOverlayIcon(self) -> None:
        pass

    @dbus.service.signal("org.kde.StatusNotifierItem", signature="")
    def NewMenu(self) -> None:
        pass


class DBusMenuService(dbus.service.Object):
    '''Exports context menu via com.canonical.dbusmenu for native Wayland and Plasma tray integration'''

    def __init__(
        self,
        bus         : dbus.SessionBus,
        path        : str,
        parent_item : "KDEStatusNotifierItem",
    ) -> None:
        super().__init__(bus, path)
        self.item     = parent_item
        self.revision = 1

    def _get_items(self) -> list[dict[str, Any]]:
        '''Convert QMenu actions into DBusMenu item dictionary structures'''

        menu = self.item.get_menu_callback()
        if not menu:
            return list()
        items: list[dict[str, Any]] = list()
        for idx, act in enumerate(menu.actions(), start=1):
            if act.isSeparator():
                items.append({"id": idx, "type": "separator"})
            else:
                items.append({
                    "id"     : idx,
                    "type"   : "standard",
                    "label"  : act.text().replace("&", ""),
                    "enabled": act.isEnabled(),
                    "visible": act.isVisible(),
                    "action" : act,
                })
        return items

    @dbus.service.signal("com.canonical.dbusmenu", signature="ui")
    def LayoutUpdated(self, revision: int, parent: int) -> None:
        pass

    @dbus.service.signal("com.canonical.dbusmenu", signature="iu")
    def ItemActivationRequested(self, id: int, timestamp: int) -> None:
        pass

    @dbus.service.method(
        "org.freedesktop.DBus.Properties",
        in_signature="ss",
        out_signature="v",
    )
    def Get(self, iface: str, prop: str) -> Any:
        '''Retrieve DBusMenu property'''

        if prop == "Version":
            return dbus.UInt32(3)
        if prop == "Status":
            return dbus.String("normal")
        raise dbus.exceptions.DBusException(f"Unknown property: {prop}")

    @dbus.service.method(
        "org.freedesktop.DBus.Properties",
        in_signature="s",
        out_signature="a{sv}",
    )
    def GetAll(self, iface: str) -> dict[str, Any]:
        '''Retrieve all DBusMenu properties'''

        return {
            "Version": dbus.UInt32(3),
            "Status" : dbus.String("normal"),
        }

    @dbus.service.method(
        "com.canonical.dbusmenu",
        in_signature="iias",
        out_signature="u(ia{sv}av)",
    )
    def GetLayout(
        self,
        parentId       : int,
        recursionDepth : int,
        propertyNames  : list[str],
    ) -> tuple:
        '''Build and return hierarchical DBusMenu layout structure'''

        items = self._get_items()
        children = list()
        for it in items:
            iid = it.get("id", 0)
            itype = it.get("type", "standard")
            raw_props = dict()
            if itype == "separator":
                raw_props["type"] = dbus.String("separator")
            else:
                raw_props["label"]   = dbus.String( it.get("label"  , ""))
                raw_props["enabled"] = dbus.Boolean(it.get("enabled", True))
                raw_props["visible"] = dbus.Boolean(it.get("visible", True))
            child_props = dbus.Dictionary(raw_props, signature="sv")
            child_struct = dbus.Struct(
                (dbus.Int32(iid), child_props, dbus.Array(list(), signature="v")),
                signature="(ia{sv}av)",
                variant_level=1,
            )
            children.append(child_struct)

        root_props = dbus.Dictionary(
            {"children-display": dbus.String("submenu")},
            signature="sv",
        )
        root_struct = dbus.Struct(
            (dbus.Int32(0), root_props, dbus.Array(children, signature="v")),
            signature="(ia{sv}av)",
        )
        return (dbus.UInt32(self.revision), root_struct)

    @dbus.service.method(
        "com.canonical.dbusmenu",
        in_signature="aias",
        out_signature="a(ia{sv})",
    )
    def GetGroupProperties(
        self,
        ids: list[int],
        propertyNames: list[str],
    ) -> dbus.Array:
        '''Retrieve properties for a subset of DBusMenu items'''

        items = self._get_items()
        res = list()
        for it in items:
            iid = it.get("id", 0)
            if not ids or iid in ids:
                itype = it.get("type", "standard")
                raw_props = dict()
                if itype == "separator":
                    raw_props["type"] = dbus.String("separator")
                else:
                    raw_props["label"]   = dbus.String(it.get("label"   , ""))
                    raw_props["enabled"] = dbus.Boolean(it.get("enabled", True))
                    raw_props["visible"] = dbus.Boolean(it.get("visible", True))
                child_props = dbus.Dictionary(raw_props, signature="sv")
                res.append(dbus.Struct((dbus.Int32(iid), child_props), signature="(ia{sv})"))
        return dbus.Array(res, signature="(ia{sv})")

    @dbus.service.method("com.canonical.dbusmenu", in_signature="is", out_signature="v")
    def GetProperty(self, item_id: int, name: str) -> Any:
        '''Retrieve a single property for a specific menu item'''

        for it in self._get_items():
            if it.get("id") == item_id:
                if name == "label":
                    return dbus.String(it.get("label", ""))
                if name == "type":
                    return dbus.String(it.get("type", "standard"))
        raise dbus.exceptions.DBusException(f"Unknown property: {name}")

    @dbus.service.method("com.canonical.dbusmenu", in_signature="isvu", out_signature="")
    def Event(self, item_id: int, event_id: str, data: Any, timestamp: int) -> None:
        '''Handle menu interaction events forwarded by the desktop environment'''

        LOGGER.debug("DBusMenu event: item_id=%d, event_id=%s", item_id, event_id)
        if event_id == "clicked":
            for it in self._get_items():
                if it.get("id") == item_id:
                    action = it.get("action")
                    if action:
                        action.trigger()
                    break

    @dbus.service.method("com.canonical.dbusmenu", in_signature="i", out_signature="b")
    def AboutToShow(self, item_id: int) -> bool:
        return False


class KDEStatusNotifierItem(QObject):
    '''StatusNotifierItem controller registering with StatusNotifierWatcher'''

    sig_activate = pyqtSignal()
    sig_secondary_activate = pyqtSignal()
    sig_context_menu = pyqtSignal(int, int)

    def __init__(
        self,
        on_activate           : Callable[[], None],
        get_menu_callback     : Callable[[], Optional[QMenu]],
        on_secondary_activate : Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__()
        self.on_activate           = on_activate
        self.get_menu_callback     = get_menu_callback
        self.on_secondary_activate = on_secondary_activate or on_activate

        self.sig_activate.connect(self.on_activate)
        self.sig_secondary_activate.connect(self.on_secondary_activate)
        self.sig_context_menu.connect(self._show_context_menu)

        self.title         = constants.APP_NAME
        self.tooltip_title = f"{constants.APP_NAME} (No active notifications)"
        self.tooltip_sub   = "Middle-click: Enter Do Not Disturb"
        self.status        = "Passive"
        badge_path         = util.generate_badge_png(0, dnd=False)
        self.icon_name     = badge_path.stem

        self.bus          = dbus.SessionBus()
        self.service_name = f"org.kde.StatusNotifierItem-{getpid()}-1"
        self.bus_name     = dbus.service.BusName(self.service_name, bus=self.bus)
        self.service_obj  = StatusNotifierItemDBusService(self.bus, "/StatusNotifierItem", self)
        self.menu_service = DBusMenuService(self.bus, "/MenuBar", self)

        try:
            watcher = self.bus.get_object(
                constants.DBUS_SNI_WATCHER,
                constants.DBUS_SNI_WATCHER_PATH,
            )
            watcher_iface = dbus.Interface(watcher, constants.DBUS_SNI_WATCHER)
            watcher_iface.RegisterStatusNotifierItem(self.service_name)
            LOGGER.debug("Registered with StatusNotifierWatcher as %s", self.service_name)
        except Exception as e:
            LOGGER.warning("Could not register with StatusNotifierWatcher: %s", e)

    def update_badge(
        self,
        active_count  : int,
        summary_items : Optional[list[dict[str, Any]]] = None,
        dnd           : bool                           = False,
    ) -> None:
        '''Update badge icon, tooltip summary, and active/passive visibility status'''

        new_status = "Active" if (active_count > 0 or dnd) else "Passive"
        badge_path = util.generate_badge_png(active_count, dnd=dnd)
        self.icon_name = badge_path.stem

        if active_count > 0:
            plural = "notification" if active_count == 1 else "notifications"
            self.tooltip_title = f"{constants.APP_NAME} ({active_count} active {plural})"
        else:
            self.tooltip_title = f"{constants.APP_NAME} (No active notifications)"

        sub_lines: list[str] = list()
        if summary_items and active_count > 0:
            for item in summary_items[:4]:
                app = item.get("app_name") or "Unknown"
                summary = item.get("summary") or ""
                if len(summary) > 42:
                    summary = summary[:39] + "..."
                sub_lines.append(f"• {app}: {summary}")
            if len(summary_items) > 4:
                sub_lines.append(f"... and {len(summary_items) - 4} more")
        elif active_count == 0:
            sub_lines.append("No active notifications")

        if dnd:
            sub_lines.append("🌙 Do Not Disturb is ON (Middle-click to exit)")
        else:
            sub_lines.append("Middle-click: Enter Do Not Disturb")

        self.tooltip_sub = "\n".join(sub_lines)
        status_changed = (self.status != new_status)
        self.status = new_status

        self.service_obj.NewIcon()
        self.service_obj.NewTitle()
        self.service_obj.NewToolTip()
        if status_changed:
            self.service_obj.NewStatus(new_status)

        if self.menu_service:
            self.menu_service.revision += 1
            self.menu_service.LayoutUpdated(
                dbus.UInt32(self.menu_service.revision),
                dbus.Int32(0),
            )

    def _show_context_menu(self, x: int, y: int) -> None:
        '''Display context menu at coordinates or cursor position'''

        menu = self.get_menu_callback()
        if menu:
            pos = QPoint(x, y) if (x > 0 and y > 0) else QCursor.pos()
            menu.popup(pos)

    def cleanup(self) -> None:
        '''Unregister D-Bus objects on shutdown'''

        try:
            self.service_obj.remove_from_connection()
            if self.menu_service:
                self.menu_service.remove_from_connection()
        except Exception:
            pass
