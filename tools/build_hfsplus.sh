#!/bin/bash
set -euo pipefail

cd /usr/src/WSL2-Linux-Kernel

echo "=== Preparing .config ==="
zcat /proc/config.gz > .config
sed -i 's/# CONFIG_HFSPLUS_FS is not set/CONFIG_HFSPLUS_FS=m/' .config
if ! grep -q 'CONFIG_HFSPLUS_FS=m' .config; then
    echo 'CONFIG_HFSPLUS_FS=m' >> .config
fi

echo "=== Running olddefconfig ==="
make olddefconfig

echo "=== Preparing modules ==="
make modules_prepare -j$(nproc)

echo "=== Building fs/hfsplus ==="
make M=fs/hfsplus -j$(nproc)

echo "=== Installing hfsplus.ko ==="
KVER=$(uname -r)
mkdir -p /lib/modules/$KVER/kernel/fs/hfsplus
cp fs/hfsplus/hfsplus.ko /lib/modules/$KVER/kernel/fs/hfsplus/
depmod -a

echo "=== Loading hfsplus.ko ==="
modprobe hfsplus

echo "=== Verifying /proc/filesystems ==="
grep hfsplus /proc/filesystems

echo "=== SUCCESS! hfsplus filesystem driver is loaded and working! ==="
