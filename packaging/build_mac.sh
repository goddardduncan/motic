#!/bin/zsh
# Build "Motic Live.app" and a shareable zip in dist/.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install --quiet -e . pyinstaller

.venv/bin/pyinstaller --noconfirm --clean --windowed \
  --name "Motic Live" \
  --osx-bundle-identifier org.motic-cam.live \
  --collect-all libusb_package \
  --distpath dist --workpath build/pyinstaller --specpath build \
  packaging/motic_live_app.py

arch=$(uname -m)
cp packaging/README-friend.txt "dist/READ ME FIRST.txt"
rm -f "dist/Motic-Live-mac-$arch.zip"
(cd dist && ditto -c -k --sequesterRsrc --keepParent "Motic Live.app" "Motic-Live-mac-$arch.zip" \
  && zip -q "Motic-Live-mac-$arch.zip" "READ ME FIRST.txt")
echo "Built dist/Motic Live.app and dist/Motic-Live-mac-$arch.zip"
