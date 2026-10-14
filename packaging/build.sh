#!/usr/bin/env bash
set -euo pipefail

# Determine version (strip leading 'v' if present to follow systemd-style versioning)
RAW_VERSION="${1:-${VERSION:-001}}"
VERSION="${RAW_VERSION#v}"
RELEASE="1"
APP_NAME="kde-notification-center"
BIN_NAME="knotification-center"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="${REPO_ROOT}/dist"
BUILD_DIR="${REPO_ROOT}/build"

echo "=== Building ${APP_NAME} packages for version ${VERSION} ==="

rm -rf "${BUILD_DIR}"
mkdir -p "${DIST_DIR}" "${BUILD_DIR}"

# 1. Build DEB Package (if dpkg-deb is available)
if command -v dpkg-deb >/dev/null 2>&1; then
    echo "--- Building DEB package ---"
    DEB_STAGING="${BUILD_DIR}/deb"
    mkdir -p "${DEB_STAGING}/DEBIAN"
    mkdir -p "${DEB_STAGING}/usr/share/applications"
    mkdir -p "${DEB_STAGING}/etc/xdg/autostart"
    mkdir -p "${DEB_STAGING}/usr/share/doc/${APP_NAME}"

    # Check if Debian-specific --install-layout option is supported
    LAYOUT_ARG=""
    if python3 setup.py install --help 2>&1 | grep -q -- '--install-layout'; then
        LAYOUT_ARG="--install-layout=deb"
    fi

    VERSION="${VERSION}" python3 setup.py install \
        --root="${DEB_STAGING}" \
        ${LAYOUT_ARG} \
        --prefix=/usr

    # Normalize to Debian standard /usr/lib/python3/dist-packages
    if compgen -G "${DEB_STAGING}/usr/lib/python3*/site-packages" > /dev/null; then
        mkdir -p "${DEB_STAGING}/usr/lib/python3/dist-packages"
        for site_dir in "${DEB_STAGING}/usr/lib/python3"*"/site-packages"; do
            if [ -d "${site_dir}" ]; then
                cp -a "${site_dir}"/* "${DEB_STAGING}/usr/lib/python3/dist-packages/"
                rm -rf "${site_dir}"
            fi
        done
        for py_dir in "${DEB_STAGING}/usr/lib/python3."*; do
            [ -d "${py_dir}" ] && rmdir "${py_dir}" 2>/dev/null || true
        done
    fi

    # Write package VERSION file
    echo "${VERSION}" > "${DEB_STAGING}/usr/lib/python3/dist-packages/knotification_center/VERSION"

    # Install Desktop launcher & Autostart entries
    cp "${REPO_ROOT}/packaging/knotification-center.desktop" \
       "${DEB_STAGING}/usr/share/applications/${BIN_NAME}.desktop"
    chmod 0644 "${DEB_STAGING}/usr/share/applications/${BIN_NAME}.desktop"

    cp "${REPO_ROOT}/packaging/knotification-center-autostart.desktop" \
       "${DEB_STAGING}/etc/xdg/autostart/${BIN_NAME}.desktop"
    chmod 0644 "${DEB_STAGING}/etc/xdg/autostart/${BIN_NAME}.desktop"

    # Install documentation & copyright
    cp "${REPO_ROOT}/README.md" "${DEB_STAGING}/usr/share/doc/${APP_NAME}/"
    chmod 0644 "${DEB_STAGING}/usr/share/doc/${APP_NAME}/README.md"

    cat << EOF > "${DEB_STAGING}/usr/share/doc/${APP_NAME}/copyright"
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: ${APP_NAME}
Upstream-Contact: Michael Cassaniti <michael@cassaniti.id.au>
Source: https://github.com/mcassaniti/kde-notification-center

Files: *
Copyright: 2026 Michael Cassaniti <michael@cassaniti.id.au>
License: GPL-3.0-or-later
 On Debian systems, the full text of the GNU General Public License version 3
 can be found in the file '/usr/share/common-licenses/GPL-3'.
EOF
    chmod 0644 "${DEB_STAGING}/usr/share/doc/${APP_NAME}/copyright"

    # Dynamically generate Debian control file
    cat << EOF > "${DEB_STAGING}/DEBIAN/control"
Package: ${APP_NAME}
Version: ${VERSION}
Section: utils
Priority: optional
Architecture: all
Maintainer: Michael Cassaniti <michael@cassaniti.id.au>
Depends: python3, python3-pyqt6, python3-dbus, python3-gi
Recommends: libnotify-bin
Homepage: https://github.com/mcassaniti/kde-notification-center
Description: KDE Notification Center
 Manage, filter, search, and clean up KDE desktop notifications with native
 StatusNotifierItem tray badge count, D-Bus eavesdropping, and auto-purge history.
EOF

    DEB_FILE="${DIST_DIR}/${APP_NAME}_${VERSION}_all.deb"
    dpkg-deb --build --root-owner-group "${DEB_STAGING}" "${DEB_FILE}"
    echo "Created DEB: ${DEB_FILE}"
else
    echo "--- Notice: dpkg-deb not installed on host; skipping DEB build ---"
fi

# 2. Build RPM Package (if rpmbuild is available)
if command -v rpmbuild >/dev/null 2>&1; then
    echo "--- Building RPM package ---"
    RPM_TOPDIR="${BUILD_DIR}/rpmbuild"
    mkdir -p "${RPM_TOPDIR}"/{BUILD,RPMS,SOURCES,SPECS,SRPMS}

    # Package source tarball
    tar --exclude='.git' --exclude='dist' --exclude='build' \
        -czf "${RPM_TOPDIR}/SOURCES/${APP_NAME}-${VERSION}.tar.gz" \
        --transform "s,^,${APP_NAME}-${VERSION}/," \
        -C "${REPO_ROOT}" .

    rpmbuild -bb --nodeps \
        --define "_topdir ${RPM_TOPDIR}" \
        --define "pkg_version ${VERSION}" \
        --define "pkg_release ${RELEASE}" \
        "${REPO_ROOT}/packaging/rpm/${APP_NAME}.spec"

    find "${RPM_TOPDIR}/RPMS" -name "*.rpm" -exec cp {} "${DIST_DIR}/" \;
    echo "Created RPM in: ${DIST_DIR}"
else
    echo "--- Notice: rpmbuild not installed on host; skipping RPM build ---"
fi

# 3. Generate Checksums in dist/
(
    cd "${DIST_DIR}"
    if compgen -G "*.deb" > /dev/null || compgen -G "*.rpm" > /dev/null; then
        sha256sum *.{deb,rpm} 2>/dev/null > SHA256SUMS || true
        echo "Generated SHA256SUMS:"
        cat SHA256SUMS
    fi
)

echo "=== Build completed successfully for version ${VERSION} ==="
