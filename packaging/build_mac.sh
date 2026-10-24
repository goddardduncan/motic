#!/bin/zsh
# Build "Moticam Viewer.app" and a shareable zip in dist/.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install --quiet -e . pyinstaller

.venv/bin/pyinstaller --noconfirm --clean --windowed \
  --name "Moticam Viewer" \
  --osx-bundle-identifier io.github.goddardduncan.moticam-viewer \
  --collect-all libusb_package \
  --distpath dist --workpath build/pyinstaller --specpath build \
  packaging/motic_live_app.py

arch=$(uname -m)
cp packaging/README-mac.txt "dist/READ ME FIRST.txt"
rm -f "dist/Moticam-Viewer-mac-$arch.zip"
(cd dist && ditto -c -k --sequesterRsrc --keepParent "Moticam Viewer.app" "Moticam-Viewer-mac-$arch.zip" \
  && zip -q "Moticam-Viewer-mac-$arch.zip" "READ ME FIRST.txt")
echo "Built dist/Moticam Viewer.app and dist/Moticam-Viewer-mac-$arch.zip"
