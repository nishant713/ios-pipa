#!/bin/bash
set -euo pipefail
cd /mnt/c/Users/nisha/.gemini/antigravity/scratch/ios-pipa/shim
rm -f out/*.o out/shim.elf out/Image out/Image.gz out/kernel_with_dtb out/test-shim-boot.img out/pipa-logdump.img

CC=aarch64-linux-gnu-gcc
OBJCOPY=aarch64-linux-gnu-objcopy
CFLAGS="-O2 -ffreestanding -nostdlib -fno-pic -mgeneral-regs-only -mstrict-align -fno-stack-protector -Wall -Wextra"

for f in start.S shim.c console.c mmu.c fdt.c loader.c ufs.c fw_blobs.S; do
    $CC $CFLAGS -c $f -o out/${f%.*}.o
done

$CC $CFLAGS -static -T shim.ld -Wl,--build-id=none -Wl,--no-warn-rwx-segments -Wl,-N \
    out/start.o out/shim.o out/console.o out/mmu.o out/fdt.o out/loader.o out/ufs.o out/fw_blobs.o -o out/shim.elf

$OBJCOPY -O binary out/shim.elf out/Image
size=$(aarch64-linux-gnu-nm out/shim.elf | awk '$3=="_end"{print strtonum("0x"$1)}')
truncate -s "$size" out/Image

gzip -c out/Image > out/Image.gz
cat out/Image.gz ../dtb/boot_unpacked/dtb > out/kernel_with_dtb

# 1. pipa-logdump.img (Diagnostic log dumper: reads UFS SWEETLOG and prints to screen)
echo "LOGDUMP_NO_PAYLOAD" > out/dummy_rd.bin
mkbootimg --header_version 1 \
    --kernel out/kernel_with_dtb \
    --ramdisk out/dummy_rd.bin \
    --base 0x10000000 \
    --kernel_offset 0x0 \
    --ramdisk_offset 0x02000000 \
    --tags_offset 0x00000100 \
    --pagesize 2048 \
    --os_version 17.0.0 \
    --os_patch_level 2035-06 \
    -o out/pipa-logdump.img

# 2. test-shim-boot.img (Full bootloader with payload)
if [ -f out/payload.bin ]; then
    mkbootimg --header_version 1 \
        --kernel out/kernel_with_dtb \
        --ramdisk out/payload.bin \
        --base 0x10000000 \
        --kernel_offset 0x0 \
        --ramdisk_offset 0x02000000 \
        --tags_offset 0x00000100 \
        --pagesize 2048 \
        --os_version 17.0.0 \
        --os_patch_level 2035-06 \
        -o out/test-shim-boot.img
fi

ls -lh out/pipa-logdump.img
