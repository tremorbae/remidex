#!/bin/bash
# Gets RemiDex and installs it on this Mac. Paste this in Terminal:
#
#   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/tremorbae/Remidex/main/get.sh)"
#
# It downloads the latest RemiDex into ~/RemiDex (replacing an older copy there) and runs its installer, which asks
# before installing anything. Run it again to update, or to sign in again: your sign-in and switches stay.
set -euo pipefail

REPO="tremorbae/Remidex"
ZIP_URL="${REMIDEX_ZIP_URL:-https://github.com/$REPO/archive/refs/heads/main.zip}"  # (another copy, for a test or a fork)
DEST="${REMIDEX_DIR:-$HOME/RemiDex}"

if [[ "$(uname)" != "Darwin" ]]; then
  echo "This is for a Mac. On Windows, use the PowerShell line in the README."
  exit 1
fi
if ! xcode-select -p >/dev/null 2>&1; then
  echo "RemiDex needs Apple's command line tools first. A window is opening to install them:"
  echo "click Install, wait until it's done, then paste the RemiDex line again."
  xcode-select --install 2>/dev/null || true
  exit 1
fi
if [[ ! -d "/Applications/Google Chrome.app" ]]; then
  echo "RemiDex needs Google Chrome. Install it from google.com/chrome, then paste the RemiDex line again."
  exit 1
fi
if [[ -e "$DEST" && ! -f "$DEST/install_browser.sh" ]]; then
  echo "There's already a folder at $DEST that isn't RemiDex. Rename or move it, then paste the RemiDex line again."
  exit 1
fi

echo "Downloading RemiDex..."
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
curl -fsSL "$ZIP_URL" -o "$tmp/remidex.zip"
unzip -q "$tmp/remidex.zip" -d "$tmp/unzipped"
rm -rf "$DEST"
mkdir -p "$(dirname "$DEST")"
mv "$tmp/unzipped/"* "$DEST"
cd "$DEST"
./install_browser.sh "$@"
