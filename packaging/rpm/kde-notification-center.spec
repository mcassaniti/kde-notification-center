%{!?python3_sitelib: %global python3_sitelib %(python3 -c "import sysconfig; print(sysconfig.get_path('purelib', vars={'base': '/usr'}))")}

Name:           kde-notification-center
Version:        %{pkg_version}
Release:        %{pkg_release}%{?dist}
Summary:        KDE Notification Center

License:        GPL-3.0-or-later
URL:            https://github.com/mcassaniti/kde-notification-center
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

Requires:       python3
Requires:       (python3-qt6 or python3-pyqt6)
Requires:       python3-dbus
Requires:       python3-gobject
Recommends:     libnotify

BuildRequires:  python3-devel
BuildRequires:  python3-setuptools

%description
Manage, filter, search, and clean up KDE desktop notifications with native
StatusNotifierItem tray badge count, D-Bus eavesdropping, and auto-purge history.

%prep
%autosetup -p1 -n %{name}-%{version}

%build
python3 setup.py build

%install
rm -rf %{buildroot}
mkdir -p %{buildroot}
python3 setup.py install -O1 --skip-build --root %{buildroot} --prefix=/usr

# If Debian/Ubuntu patched python redirected files into /usr/local, move to /usr
if [ -d "%{buildroot}/usr/local" ]; then
    cp -a %{buildroot}/usr/local/* %{buildroot}/usr/
    rm -rf %{buildroot}/usr/local
fi

# Ensure standard system python3 shebang
if [ -d "%{buildroot}%{_bindir}" ]; then
    sed -i '1s|^#!.*python.*|#!/usr/bin/python3|' %{buildroot}%{_bindir}/*
fi

install -D -m 0644 packaging/knotification-center.desktop %{buildroot}%{_datadir}/applications/knotification-center.desktop
install -D -m 0644 packaging/knotification-center-autostart.desktop %{buildroot}%{_sysconfdir}/xdg/autostart/knotification-center.desktop

%files
%{_bindir}/knotification-center
%{python3_sitelib}/knotification_center
%{python3_sitelib}/kde_notification_center*.egg-info
%{_datadir}/applications/knotification-center.desktop
%{_sysconfdir}/xdg/autostart/knotification-center.desktop
%doc README.md
%license LICENSE

%changelog
