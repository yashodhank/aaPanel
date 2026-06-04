#!/bin/bash
# Tomcat 11 local installer — downloads from Apache mirrors and sets up
# the same directory structure expected by bt_tomcat() and javaModel.
#
# Called as: bash tomcat11_install.sh install <version> <jdk-path>
#             bash tomcat11_install.sh uninstall <version>

set -e

ACTION="${1:-install}"
VERSION="${2:-11}"
JDK_PATH="${3:-}"

TOMCAT_MAJOR=11
TOMCAT_MINOR="11.0.5"  # pin a known-good release; update when needed
TOMCAT_DIR="apache-tomcat-${TOMCAT_MINOR}"
TOMCAT_TGZ="${TOMCAT_DIR}.tar.gz"
BTT_PATH="/usr/local/bttomcat/tomcat${VERSION}"
BAK_PATH="/usr/local/bttomcat/tomcat_bak${VERSION}"
INIT_SCRIPT="/etc/init.d/bttomcat${VERSION}"

MIRROR_BASE="https://dlcdn.apache.org/tomcat/tomcat-${TOMCAT_MAJOR}/v${TOMCAT_MINOR}/bin"

if [ "$ACTION" = "uninstall" ]; then
    echo "Stopping Tomcat ${VERSION}..."
    [ -x "$INIT_SCRIPT" ] && "$INIT_SCRIPT" stop 2>/dev/null || true
    echo "Removing ${BTT_PATH}..."
    rm -rf "$BTT_PATH" "$BAK_PATH"
    rm -f "$INIT_SCRIPT"
    rm -f "/usr/local/bttomcat/tomcat${VERSION}"  # symlink cleanup
    echo "Tomcat ${VERSION} uninstalled."
    exit 0
fi

if [ "$ACTION" != "install" ]; then
    echo "Usage: $0 {install|uninstall} <version> [jdk-path]"
    exit 1
fi

echo "Installing Tomcat ${VERSION} (Apache Tomcat ${TOMCAT_MINOR})..."

# Clean up any partial install
rm -rf "$BTT_PATH" "$BAK_PATH"

# Download
TMP_DIR="/tmp/tomcat${VERSION}_install_$$"
mkdir -p "$TMP_DIR"
cd "$TMP_DIR"

echo "Downloading ${MIRROR_BASE}/${TOMCAT_TGZ}..."
if command -v /usr/local/curl/bin/curl &>/dev/null; then
    CURL=/usr/local/curl/bin/curl
else
    CURL=curl
fi

$CURL -fsSL -o "$TOMCAT_TGZ" "${MIRROR_BASE}/${TOMCAT_TGZ}" || {
    echo "ERROR: Download failed from ${MIRROR_BASE}/${TOMCAT_TGZ}"
    rm -rf "$TMP_DIR"
    exit 1
}

# Extract
echo "Extracting..."
mkdir -p "$BTT_PATH"
tar -xzf "$TOMCAT_TGZ" -C "$BTT_PATH" --strip-components=1

# Configure JAVA_HOME in daemon.sh
DAEMON_SH="${BTT_PATH}/bin/daemon.sh"
if [ -f "$DAEMON_SH" ]; then
    if [ -n "$JDK_PATH" ]; then
        sed -i "1iJAVA_HOME=${JDK_PATH}" "$DAEMON_SH"
    else
        # Try to find a suitable JDK
        DEFAULT_JDK="/usr/local/btjdk/jdk17/bin/java"
        if [ -x "$DEFAULT_JDK" ]; then
            DEFAULT_JDK_HOME="/usr/local/btjdk/jdk17"
            sed -i "1iJAVA_HOME=${DEFAULT_JDK_HOME}" "$DAEMON_SH"
        else
            echo "WARNING: No JDK path specified and no default JDK found."
            echo "         Set JAVA_HOME in ${DAEMON_SH} before starting."
        fi
    fi
fi

# Create version.pl
echo "${TOMCAT_MINOR}" > "${BTT_PATH}/version.pl"

# Create backup copy for per-site project creation
echo "Creating backup template..."
cp -r "$BTT_PATH" "$BAK_PATH"

# Clean up webapps in backup to keep template minimal
rm -rf "${BAK_PATH}/webapps/"*

# Create init script
echo "Creating init script..."
cat > "$INIT_SCRIPT" << 'INITEOF'
#!/bin/bash
# chkconfig: 2345 80 20
# description: bttomcat{VER} startup script

TOM_VERSION="{VER}"
export JAVA_HOME=$(head -1 /usr/local/bttomcat/tomcat${TOM_VERSION}/bin/daemon.sh | sed 's/JAVA_HOME=//')
export CATALINA_HOME=/usr/local/bttomcat/tomcat${TOM_VERSION}
export CATALINA_BASE=/usr/local/bttomcat/tomcat${TOM_VERSION}

case "$1" in
    start)
        ${CATALINA_HOME}/bin/daemon.sh start
        ;;
    stop)
        ${CATALINA_HOME}/bin/daemon.sh stop
        ;;
    restart)
        ${CATALINA_HOME}/bin/daemon.sh stop
        sleep 2
        ${CATALINA_HOME}/bin/daemon.sh start
        ;;
    status)
        ${CATALINA_HOME}/bin/daemon.sh status
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status}"
        exit 1
esac
INITEOF

sed -i "s/{VER}/${VERSION}/g" "$INIT_SCRIPT"
chmod +x "$INIT_SCRIPT"

# Clean up
rm -rf "$TMP_DIR"

echo "Tomcat ${VERSION} installation complete."
echo "Path: ${BTT_PATH}"
echo "Init: ${INIT_SCRIPT}"
