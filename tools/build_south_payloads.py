#!/usr/bin/env python3
import subprocess, os, sys

BASE = r"C:\Users\nisha\.gemini\antigravity\scratch\ios-pipa"
MKPAYLOAD = os.path.join(BASE, "tools", "mkpayload.py")
KERNEL = os.path.join(BASE, "xnu", "out", "kernel.development.pipa.nop")
ADT_CSOT = os.path.join(BASE, "shim", "out", "pipa-quiet.adt")
ADT_TM = os.path.join(BASE, "shim", "out", "pipa-quiet-tm.adt")

CMDLINE_TEMPLATE = (
    "debug=0xc serial=3 cpumask=0xf1 keepsyms=1 -noprogress rd=ufsroot0 "
    "unentitled_ios_sim_launch=1 ignition_level=1 wdt=-1 wdt_test=no_user_checkin "
    "ufsrw=1 rootrw=1 launchd_unsecure_cache=1 ufslogsecs=1 sweetfb_hz=60 sweetflip=0 "
    "sweetfb_noconsole=1 sweetfb_fit=0 sweetwifi=12 sweetfb_stats=0 "
    "sweetsample=- sweetnull=backboardd,SpringBoard,com.apple.datamigrator,AegirPoster,com.apple.PosterBoard "
    "sweettouch=1 sweettouchstage=4 sweettouchtlmm=0x0f500000 sweettouchrst=33 sweettouchirq=39 "
    "sweettouchbuf=0x125800 sweettouchdelay=0 sweettouchpoll=4 sweettouch_debug=1 {fw_arg}"
)

variants = [
    ("payload-clean-nofw.bin", "pipa-clean-nofw.img", ADT_CSOT, "sweettouchfw=0"),
    ("payload-clean-csot.bin", "pipa-clean-csot.img", ADT_CSOT, "sweettouchfw=1"),
    ("payload-clean-tm.bin",   "pipa-clean-tm.img",   ADT_TM,   "sweettouchfw=1"),
]

for pl_name, img_name, adt, fw_arg in variants:
    pl_path = os.path.join(BASE, "shim", "out", pl_name)
    img_path = os.path.join(BASE, "shim", "out", img_name)
    cmdline = CMDLINE_TEMPLATE.format(fw_arg=fw_arg)
    print(f"\n=== Building {pl_name} (length {len(cmdline)} / max 607) ===")
    assert len(cmdline) <= 607, f"Cmdline too long: {len(cmdline)}"

    res = subprocess.run([sys.executable, MKPAYLOAD, KERNEL, adt, pl_path, "--cmdline", cmdline], capture_output=True, text=True)
    if res.returncode != 0:
        print("ERROR mkpayload:", res.stderr)
        sys.exit(1)
    print("Payload built successfully:", os.path.getsize(pl_path), "bytes")

    wsl_shim = "/mnt/c/Users/nisha/.gemini/antigravity/scratch/ios-pipa/shim"
    mkboot_cmd = (
        f"cd {wsl_shim} && mkbootimg --header_version 1 "
        f"--kernel out/kernel_with_dtb "
        f"--ramdisk out/{pl_name} "
        f"--base 0x10000000 "
        f"--kernel_offset 0x0 "
        f"--ramdisk_offset 0x02000000 "
        f"--tags_offset 0x00000100 "
        f"--pagesize 2048 "
        f"--os_version 17.0.0 "
        f"--os_patch_level 2035-06 "
        f"-o out/{img_name}"
    )
    print(f"Building {img_name} via WSL mkbootimg...")
    res = subprocess.run(["wsl", "-d", "Ubuntu", "bash", "-c", mkboot_cmd], capture_output=True, text=True)
    if res.returncode != 0:
        print("ERROR mkbootimg:", res.stderr)
        sys.exit(1)
    print(f"Successfully generated {img_name}: {os.path.getsize(img_path)} bytes")

# Also copy pipa-clean-nofw.img to pipa-clean-boot.img
import shutil
shutil.copyfile(os.path.join(BASE, "shim", "out", "pipa-clean-nofw.img"),
                os.path.join(BASE, "shim", "out", "pipa-clean-boot.img"))
print("Updated pipa-clean-boot.img as well.")
print("\nALL BOOT IMAGES SUCCESSFULLY BUILT!")
