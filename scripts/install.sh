#!/usr/bin/env bash
# BMW NBT iDrive Extended — full installation script
# Target OS: Raspberry Pi OS (Bookworm, 64-bit) on Raspberry Pi 4/5
# Run as root:  sudo bash scripts/install.sh

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
INSTALL_USER="${SUDO_USER:-pi}"

log()  { echo "[install] $*"; }
die()  { echo "[ERROR] $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Run with sudo"

# ──────────────────────────────────────────────
# 1. System packages
# ──────────────────────────────────────────────
log "Installing system packages..."
apt-get update -qq
apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv \
    chromium-browser \
    mpv \
    xdotool \
    hostapd dnsmasq \
    can-utils \
    libgstreamer1.0-dev gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad gstreamer1.0-libav \
    gstreamer1.0-gl \
    libsdl2-dev \
    fonts-symbola \
    git cmake build-essential \
    avahi-daemon libavahi-client-dev \
    libssl-dev libplist-dev \
    libglib2.0-dev

# ──────────────────────────────────────────────
# 2. Build UxPlay from source (CarPlay server)
# ──────────────────────────────────────────────
log "Building UxPlay..."
UXPLAY_DIR="/tmp/UxPlay"
if [[ ! -d "$UXPLAY_DIR" ]]; then
    git clone --depth=1 https://github.com/FDH2/UxPlay.git "$UXPLAY_DIR"
fi
cmake -S "$UXPLAY_DIR" -B "$UXPLAY_DIR/build" \
    -DCMAKE_BUILD_TYPE=Release \
    -DGST_SINK=xvimagesink 2>&1 | tail -5
cmake --build "$UXPLAY_DIR/build" -j"$(nproc)"
cmake --install "$UXPLAY_DIR/build"
log "UxPlay installed at $(which uxplay)"

# ──────────────────────────────────────────────
# 3. Python virtual environment
# ──────────────────────────────────────────────
log "Setting up Python venv..."
VENV="$REPO_DIR/.venv"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip -q
"$VENV/bin/pip" install -r "$REPO_DIR/requirements.txt" -q
chown -R "$INSTALL_USER":"$INSTALL_USER" "$VENV"

# ──────────────────────────────────────────────
# 4. CAN bus kernel module + systemd-networkd
# ──────────────────────────────────────────────
log "Configuring MCP2515 CAN HAT..."
# Add overlay to /boot/config.txt if not already present
CONFIG_TXT="/boot/config.txt"
OVERLAY="dtoverlay=mcp2515-can0,oscillator=16000000,interrupt=25"
grep -qF "$OVERLAY" "$CONFIG_TXT" || echo "$OVERLAY" >> "$CONFIG_TXT"

# Create systemd-networkd config for can0
cat > /etc/systemd/network/80-can0.network <<'EOF'
[Match]
Name=can0

[CAN]
BitRate=100000
RestartSec=100ms
EOF

systemctl enable systemd-networkd
systemctl restart systemd-networkd

# ──────────────────────────────────────────────
# 5. hostapd / dnsmasq — CarPlay Wi-Fi AP
# ──────────────────────────────────────────────
log "Configuring CarPlay Wi-Fi AP..."

cat > /etc/hostapd/bmw-carplay.conf <<'EOF'
interface=wlan0
driver=nl80211
ssid=BMW_CarPlay
hw_mode=g
channel=6
wmm_enabled=0
macaddr_acl=0
auth_algs=1
ignore_broadcast_ssid=0
wpa=2
wpa_passphrase=driveconnected
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
EOF

cat > /etc/dnsmasq.d/bmw-carplay.conf <<'EOF'
interface=wlan0
dhcp-range=192.168.50.2,192.168.50.20,255.255.255.0,24h
EOF

# hostapd and dnsmasq are started on-demand by the app; disable auto-start
systemctl disable hostapd dnsmasq 2>/dev/null || true

# ──────────────────────────────────────────────
# 6. Install systemd service
# ──────────────────────────────────────────────
log "Installing systemd service..."
SERVICE_SRC="$SCRIPT_DIR/bmw-nbt-extend.service"
SERVICE_DST="/etc/systemd/system/bmw-nbt-extend.service"

sed "s|__REPO_DIR__|$REPO_DIR|g; s|__VENV__|$VENV|g; s|__USER__|$INSTALL_USER|g" \
    "$SERVICE_SRC" > "$SERVICE_DST"

systemctl daemon-reload
systemctl enable bmw-nbt-extend.service
log "Service installed. Start with: sudo systemctl start bmw-nbt-extend"

# ──────────────────────────────────────────────
# Done
# ──────────────────────────────────────────────
log "Installation complete."
log "1. Reboot for the MCP2515 CAN overlay to take effect."
log "2. After reboot: sudo systemctl start bmw-nbt-extend"
log "3. Or run directly: $VENV/bin/python -m src.main --verbose"
