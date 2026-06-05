#!/bin/bash
# Generic Tomcat installer — supports Tomcat 10 and 11, downloads from Apache
# mirrors, installs to /www/server/tomcat{VERSION} (tomcat2 plugin path) with
# a symlink at /usr/local/bttomcat/tomcat{VERSION} for javaModel compatibility.
#
# Called as: bash tomcat_install.sh install <version> [jdk-path]
#             bash tomcat_install.sh uninstall <version>

set -e

ACTION="${1:-install}"
VERSION="${2:-11}"
JDK_PATH="${3:-}"

# ---- version registry ----
case "$VERSION" in
    10)
        TOMCAT_MAJOR=10
        TOMCAT_MINOR="10.1.55"
        MIN_JDK=11
        ;;
    11)
        TOMCAT_MAJOR=11
        TOMCAT_MINOR="11.0.22"
        MIN_JDK=17
        ;;
    *)
        echo "ERROR: Unsupported tomcat version: $VERSION (supported: 10, 11)"
        exit 1
        ;;
esac

TOMCAT_DIR="apache-tomcat-${TOMCAT_MINOR}"
TOMCAT_TGZ="${TOMCAT_DIR}.tar.gz"
TC_PATH="/www/server/tomcat${VERSION}"
BAK_PATH="/www/server/tomcat_bak${VERSION}"
INIT_SCRIPT="/etc/init.d/tomcat${VERSION}"
BTT_INIT_SCRIPT="/etc/init.d/bttomcat${VERSION}"
BTT_LINK="/usr/local/bttomcat/tomcat${VERSION}"
BTT_BAK_LINK="/usr/local/bttomcat/tomcat_bak${VERSION}"
MIRROR_BASE="https://dlcdn.apache.org/tomcat/tomcat-${TOMCAT_MAJOR}/v${TOMCAT_MINOR}/bin"

# ---- uninstall ----
if [ "$ACTION" = "uninstall" ]; then
    echo "Stopping Tomcat ${VERSION}..."
    [ -x "$INIT_SCRIPT" ] && "$INIT_SCRIPT" stop 2>/dev/null || true
    rm -rf "$TC_PATH" "$BAK_PATH" "$BTT_LINK" "$BTT_BAK_LINK" || true
    rm -f "$INIT_SCRIPT" "$BTT_INIT_SCRIPT" || true
    echo "Tomcat ${VERSION} uninstalled."
    echo "NOTE: JDK at /usr/local/btjdk/ was not removed (may be shared)."
    exit 0
fi

if [ "$ACTION" != "install" ]; then
    echo "Usage: $0 {install|uninstall} <version> [jdk-path]"
    exit 1
fi

# ---- find or resolve JDK ----
resolve_jdk() {
    if [ -n "$JDK_PATH" ] && [ -x "$JDK_PATH/bin/java" ]; then
        echo "$JDK_PATH"
        return 0
    fi

    # Check common locations (newest first)
    for candidate in \
        /usr/local/btjdk/jdk21 \
        /usr/local/btjdk/jdk17 \
        /usr/local/btjdk/jdk11 \
        /usr/local/btjdk/jdk8 \
        /www/server/java/jdk-21 \
        /www/server/java/jdk-17 \
        /www/server/java/jdk-11 \
        /www/server/java/jdk-8; do
        if [ -x "$candidate/bin/java" ]; then
            echo "$candidate"
            return 0
        fi
    done

    # Fallback: find any java
    if command -v java &>/dev/null; then
        JAVA_BIN=$(command -v java)
        JAVA_HOME=$(dirname "$(dirname "$(readlink -f "$JAVA_BIN")")")
        echo "$JAVA_HOME"
        return 0
    fi

    return 1
}

JDK_HOME=$(resolve_jdk) || true

if [ -z "$JDK_HOME" ]; then
    echo "Fetching latest OpenJDK 17 from Adoptium..."
    # Use Adoptium API to get the latest download URL (avoids hardcoded version)
    JDK_URL=$(curl -fsSL --connect-timeout 15 --retry 2 \
        "https://api.adoptium.net/v3/assets/latest/17/hotspot?architecture=x64&image_type=jdk&os=linux" 2>/dev/null \
        | python3 -c "import json,sys;d=json.load(sys.stdin);print(d[0]['binary']['package']['link'])" 2>/dev/null || echo "")
    if [ -z "$JDK_URL" ]; then
        echo "Adoptium API failed, trying direct download..."
        JDK_URL="https://github.com/adoptium/temurin17-binaries/releases/download/jdk-17.0.19%2B10/OpenJDK17U-jdk_x64_linux_hotspot_17.0.19_10.tar.gz"
    fi
    JDK_TGZ=$(basename "$JDK_URL" | sed 's/%2B/+/g')
    echo "Downloading JDK from $JDK_URL..."

    mkdir -p /usr/local/btjdk
    TMP_JDK="/tmp/jdk17_install_$$"
    mkdir -p "$TMP_JDK"
    cd "$TMP_JDK"

    CURL=$(command -v /usr/local/curl/bin/curl || echo curl)
    $CURL -fsSL --retry 3 --retry-delay 5 --max-time 300 -o "$JDK_TGZ" "$JDK_URL" || {
        echo "ERROR: JDK download failed from $JDK_URL"
        rm -rf "$TMP_JDK"
        exit 1
    }

    mkdir -p /usr/local/btjdk/jdk17
    tar -xzf "$JDK_TGZ" --strip-components=1 -C /usr/local/btjdk/jdk17
    rm -rf "$TMP_JDK"
    JDK_HOME="/usr/local/btjdk/jdk17"
    echo "JDK 17 installed to $JDK_HOME"
fi

# Verify JDK meets version requirement
JAVA_VER=$("$JDK_HOME/bin/java" -version 2>&1 | head -1 | sed 's/.*version "\([0-9]*\).*/\1/' || echo 0)
JAVA_VER=${JAVA_VER:-0}
if [ "$JAVA_VER" -lt "$MIN_JDK" ]; then
    echo "ERROR: JDK at $JDK_HOME is Java $JAVA_VER (requires $MIN_JDK+)"
    echo "       Specify a newer JDK with: $0 install $VERSION /path/to/jdk"
    exit 1
fi

echo "Using JDK: $JDK_HOME (Java $JAVA_VER)"

# ---- clean & download ----
echo "Installing Tomcat ${VERSION} (Apache Tomcat ${TOMCAT_MINOR})..."
if [ -f "${TC_PATH}/version.pl" ]; then
    INSTALLED_VER=$(cat "${TC_PATH}/version.pl" 2>/dev/null || echo "")
    if [ "$INSTALLED_VER" = "$TOMCAT_MINOR" ]; then
        echo "Tomcat ${TOMCAT_MINOR} already installed at ${TC_PATH}"
        echo "Use 'bash $0 uninstall ${VERSION}' to remove, then reinstall"
        exit 0
    fi
    echo "Upgrading from ${INSTALLED_VER} to ${TOMCAT_MINOR}..."
fi
rm -rf "$TC_PATH" "$BAK_PATH" "$BTT_LINK" "$BTT_BAK_LINK"

TMP_DIR="/tmp/tomcat${VERSION}_install_$$"
mkdir -p "$TMP_DIR"
cd "$TMP_DIR"

echo "Downloading ${MIRROR_BASE}/${TOMCAT_TGZ}..."
CURL=$(command -v /usr/local/curl/bin/curl || echo curl)
$CURL -fsSL --retry 3 --retry-delay 5 --max-time 300 -o "$TOMCAT_TGZ" "${MIRROR_BASE}/${TOMCAT_TGZ}" || {
    echo "WARNING: Primary mirror failed, trying archive..."
    $CURL -fsSL --retry 2 --max-time 300 -o "$TOMCAT_TGZ" \
        "https://archive.apache.org/dist/tomcat/tomcat-${TOMCAT_MAJOR}/v${TOMCAT_MINOR}/bin/${TOMCAT_TGZ}" || {
        echo "ERROR: Download failed from all mirrors"
        rm -rf "$TMP_DIR"
        exit 1
    }
}

# ---- extract ----
echo "Extracting..."
mkdir -p "$TC_PATH"
tar -xzf "$TOMCAT_TGZ" -C "$TC_PATH" --strip-components=1

# ---- configure JAVA_HOME ----
DAEMON_SH="${TC_PATH}/bin/daemon.sh"
if grep -q "^JAVA_HOME=" "$DAEMON_SH" 2>/dev/null; then
    sed -i "s|^JAVA_HOME=.*|JAVA_HOME=${JDK_HOME}|" "$DAEMON_SH"
else
    sed -i "2iJAVA_HOME=${JDK_HOME}" "$DAEMON_SH"
fi
# Use www user instead of tomcat (may not exist)
sed -i 's/TOMCAT_USER=tomcat/TOMCAT_USER=www/' "$DAEMON_SH"

# ---- build jsvc from commons-daemon ----
COMMONS_DAEMON="${TC_PATH}/bin/commons-daemon-native.tar.gz"
if [ -f "$COMMONS_DAEMON" ]; then
    echo "Building jsvc..."
    BUILD_DIR="/tmp/tomcat${VERSION}_jsvc_$$"
    mkdir -p "$BUILD_DIR"
    tar xzf "$COMMONS_DAEMON" -C "$BUILD_DIR"
    cd "$BUILD_DIR"/commons-daemon-*-native-src/unix 2>/dev/null || true
    if [ -f configure ]; then
        ./configure --with-java="$JDK_HOME" 2>/dev/null
        make 2>/dev/null && cp jsvc "$TC_PATH/bin/" && echo "jsvc built" || echo "WARNING: jsvc build failed, using catalina.sh"
    fi
    rm -rf "$BUILD_DIR"
fi

# ---- version marker ----
echo "${TOMCAT_MINOR}" > "${TC_PATH}/version.pl"

# ---- backup template ----
echo "Creating backup template..."
cp -r "$TC_PATH" "$BAK_PATH"
rm -rf "${BAK_PATH}/webapps/"*

# ---- symlinks for javaModel ----
mkdir -p /usr/local/bttomcat
ln -sfn "$TC_PATH" "$BTT_LINK"
ln -sfn "$BAK_PATH" "$BTT_BAK_LINK"

# ---- init script ----
cat > "$INIT_SCRIPT" << INITEOF
#!/bin/bash
TOM_VERSION=${VERSION}
TOM_PATH=/www/server/tomcat\${TOM_VERSION}
export JAVA_HOME=\$(head -1 \${TOM_PATH}/bin/daemon.sh | sed 's/JAVA_HOME=//')
export CATALINA_HOME=\${TOM_PATH}
export CATALINA_BASE=\${TOM_PATH}
case "\$1" in
    start)   \${CATALINA_HOME}/bin/daemon.sh start ;;
    stop)    \${CATALINA_HOME}/bin/daemon.sh stop ;;
    restart) \${CATALINA_HOME}/bin/daemon.sh stop; sleep 2; \${CATALINA_HOME}/bin/daemon.sh start ;;
    status)  \${CATALINA_HOME}/bin/daemon.sh status ;;
    *)       echo "Usage: \$0 {start|stop|restart|status}"; exit 1 ;;
esac
INITEOF
chmod +x "$INIT_SCRIPT"
ln -sfn "$INIT_SCRIPT" "$BTT_INIT_SCRIPT"

# ---- cleanup ----
rm -rf "$TMP_DIR"

echo "========== Installation complete =========="
echo "  Tomcat:  ${TC_PATH} (${TOMCAT_MINOR})"
echo "  JDK:     ${JDK_HOME} (Java ${JAVA_VER})"
echo "  Init:    ${INIT_SCRIPT}"
echo "==========================================="
