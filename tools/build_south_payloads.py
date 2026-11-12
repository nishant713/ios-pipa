#!/usr/bin/env python3
import subprocess, os, sys, shutil

IPAD_MODE = "--ipad" in sys.argv
BASE = r"C:\Users\nisha\.gemini\antigravity\scratch\ios-pipa"
MKADT = os.path.join(BASE, "tools", "mkadt.py")
MKPAYLOAD = os.path.join(BASE, "tools", "mkpayload.py")
KERNEL = os.path.join(BASE, "xnu", "out", "kernel.development.pipa.nop")
TAG = "ipados" if IPAD_MODE else "quiet"
ADT_CSOT = os.path.join(BASE, "shim", "out", f"pipa-{TAG}.adt")
ADT_TM = os.path.join(BASE, "shim", "out", f"pipa-{TAG}-tm.adt")
TOUCH_CSOT = os.path.join(BASE, "research", "pipa-fw", "novatek_nt36532_m82_fw_csot.bin")
TOUCH_TM = os.path.join(BASE, "research", "pipa-fw", "novatek_nt36532_m82_fw_tm.bin")
WIFI_FW = os.path.join(BASE, "research", "pipa-wifi")
BT_FW = os.path.join(BASE, "research", "pipa-bt")
WIFI_MAC = "cc:42:10:b1:a0:a0"

CMDLINE_TEMPLATE = (
    "debug=0xc serial=3 cpumask=0xf1 keepsyms=1 -noprogress rd=ufsroot0 "
    "unentitled_ios_sim_launch=1 wdt=-1 ufsrw=1 rootrw=1 launchd_unsecure_cache=1 "
    "ufslogsecs=1 sweetfb_hz=60 sweetfb_fit=0 sweetfb_noconsole=1 sweetfb_stats=0 "
    "sweetwifi=12 sweetbt=6 sweetgpu=21 sweetgpukeep=1 sweetgpulevel=6 sweetmtl=2 "
    "sweetsample=- sweetnull=backboardd,SpringBoard,bluetoothd,BlueTool,mediaserverd,com.apple.WebKit,VTDecoderXPCServ "
    "sweetinject=/usr/local/lib/libsweet-bt.dylib:bluetoothd,BlueTool "
    "sweettouch=1 sweettouchtlmm=0x0f500000 sweettouchrst=33 sweettouchirq=39 "
    "sweettouchbuf=0x125800 sweettouchdelay=0 {fw_arg}"
)

def build_adt(out_adt, touch_fw):
    scale = "2" if IPAD_MODE else "3"
    cmd = [
        sys.executable, MKADT, out_adt,
        "--highmem", "--width", "1800", "--height", "2880", "--scale", scale,
        "--gic", "0x16a00000", "--touch-fw", touch_fw,
        "--files-from", os.path.join(BASE, "tools", "root-files", "pipa-files.txt"),
        "--files-from", os.path.join(BASE, "tools", "root-files", "aegir-off.txt"),
    ]
    if IPAD_MODE:
        cmd += ["--product-adt", os.path.join(BASE, "research", "ipsw-x", "j98aap.adt")]
    if os.path.isdir(WIFI_FW):
        cmd += ["--wifi-fw", WIFI_FW, "--wifi-mac", WIFI_MAC]
    if os.path.isdir(BT_FW):
        cmd += ["--bt-fw", BT_FW]
    gpu_dir = os.path.join(BASE, "research", "pipa-gpu")
    if os.path.exists(os.path.join(gpu_dir, "a650_gmu.bin")):
        cmd += [
            "--gmu-fw", os.path.join(gpu_dir, "a650_gmu.bin"),
            "--sqe-fw", os.path.join(gpu_dir, "a650_sqe.fw"),
            "--zap-mdt", os.path.join(gpu_dir, "a650_zap.mdt"),
            "--zap-seg", os.path.join(gpu_dir, "a650_zap.b02"),
        ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print("ERROR mkadt:", res.stderr)
        sys.exit(1)
    print(res.stderr.strip())

build_adt(ADT_CSOT, TOUCH_CSOT)
build_adt(ADT_TM, TOUCH_TM)

PREFIX = "ipados" if IPAD_MODE else "clean"
variants = [
    (f"payload-{PREFIX}-nofw.bin", f"pipa-{PREFIX}-nofw.img", ADT_CSOT, "sweettouchfw=0"),
    (f"payload-{PREFIX}-csot.bin", f"pipa-{PREFIX}-csot.img", ADT_CSOT, "sweettouchfw=1"),
    (f"payload-{PREFIX}-tm.bin",   f"pipa-{PREFIX}-tm.img",   ADT_TM,   "sweettouchfw=1"),
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

# Also copy nofw variant to boot image
boot_img = f"pipa-{PREFIX}-boot.img"
shutil.copyfile(os.path.join(BASE, "shim", "out", f"pipa-{PREFIX}-nofw.img"),
                os.path.join(BASE, "shim", "out", boot_img))
print(f"Updated {boot_img} as well.")
print("\nALL BOOT IMAGES SUCCESSFULLY BUILT!")
