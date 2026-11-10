#!/bin/bash
set -euo pipefail

cd /mnt/c/Users/nisha/.gemini/antigravity/scratch/ios-pipa

IPSW_DIR="research/ipsw/iPhone10_3-20H392"
OS_DMG="$IPSW_DIR/087-86698-044.dmg"
SYSOS_DMG="$IPSW_DIR/087-86593-047.dmg"
APPOS_DMG="$IPSW_DIR/087-86715-047.dmg"
OUT_IMG="research/ios-root/ios16-root.img"
OUT_SIMG="research/ios-root/ios16-root.simg"
MNT="/mnt/iosroot"

echo "=== 1. Checking input DMGs ==="
for f in "$OS_DMG" "$SYSOS_DMG" "$APPOS_DMG"; do
    if [ ! -f "$f" ]; then
        echo "Error: $f does not exist!"
        exit 1
    fi
    echo "Found $f ($(du -h "$f" | cut -f1))"
done

echo "=== 2. Cleaning any previous build ==="
if mountpoint -q "$MNT" 2>/dev/null; then
    sudo umount "$MNT" || true
fi
rm -f "$OUT_IMG" "$OUT_SIMG"

echo "=== 3. Building HFS+ root image (16G) via mkiosroot.sh ==="
tools/mkiosroot.sh "$OS_DMG" "$SYSOS_DMG" "$APPOS_DMG" "$OUT_IMG" 16G

echo "=== 4. Applying SWEET tweaks and userland programs ==="
sudo fsck.hfsplus -fy "$OUT_IMG" >/dev/null 2>&1 || true
sudo mkdir -p "$MNT"
sudo mount -t hfsplus -o loop,force "$OUT_IMG" "$MNT"

sudo python3 tools/sweet-root-tweaks.py "$MNT"
sudo tools/sweet-root-install.sh "$MNT"

sync
sudo umount "$MNT"
sudo fsck.hfsplus -fn "$OUT_IMG" 2>&1 | tail -2 || true

echo "=== 5. Converting to Android Sparse Image (img2simg) ==="
img2simg "$OUT_IMG" "$OUT_SIMG"
ls -lh "$OUT_SIMG"

echo "=== Done! Sparse image ready at $OUT_SIMG ==="
