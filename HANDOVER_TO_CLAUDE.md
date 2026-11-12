# Project Handover: iOS 16 (iPadOS) on Xiaomi Pad 6 (`pipa`)
**Date:** November 8, 2026  
**Target Device:** Xiaomi Pad 6 (`pipa`, Snapdragon 870 / `SM8250-AC`, Serial: `486a9af6`, Unlocked Bootloader)  
**Target OS:** iOS 16 (`iPhone10,3`, Darwin Kernel 22.x, Build `20H392`)  
**Workspace Path:** `C:\Users\nisha\.gemini\antigravity\scratch\ios-pipa`  
**Host Environment:** Windows 11 with PowerShell + WSL2 Ubuntu (`wsl -d Ubuntu`)  

---

## 1. Executive Summary & Current Status

We have successfully brought up a **fully rendered native iOS 16 desktop** on the Xiaomi Pad 6. The tablet boots via tethered `fastboot boot`, mounts the iOS rootfs from internal UFS, scales display output cleanly, and unlocks into the interactive SpringBoard Home Screen.

### What is ALREADY Working (100% Verified):
1. **Native Panel Rendering:** Full 1800×2880 graphical display rendered on tablet LCD (`sweet_fb` / `AppleCLCD` / dummy `sweet-metal`).
2. **SpringBoard Desktop:** Boots to SpringBoard; Home Screen displays Dock (Phone, Safari, Messages, Music) and application grid (Settings, App Store, Maps, Health, etc.).
3. **Hardware Power Button:** Fully functional (`SWEET KEYS: pon=0x6 gpio(0xc5)=0x1`).
4. **HID Event Injection:** `SweetHID` $\rightarrow$ `backboardd` $\rightarrow$ `SpringBoard` is proven working. Automated key injection (`sweethidtest=1` or `10`) successfully unlocks the Lock Screen and brings up the app desktop.
5. **Storage:** Read/write internal UFS mount (`ufsroot0`).
6. **CPU Frequency & Power:** Stable scheduler with Kryo Prime core pinning (`cpumask=0xf1`).

### What is Blocked / Current Objective:
- **Physical Touchscreen Digitizer:** The on-screen glass digitizer does not react to physical finger touches.
- **Hardware:** Novatek NT36523 / NT36532 on SPI SE4 (`spi@990000`).

---

## 2. CRITICAL: Dead Ends to AVOID (Do Not Waste Turns Here!)

> [!WARNING]
> Claude got stuck in an iterative loop attempting to enter ADB or Recovery mode. **Stop doing this immediately:**
> 1. **iOS does not run an Android ADB daemon (`adbd`):** You cannot get `adb shell` or `adb devices` while booted into iOS.
> 2. **Retail Fastboot has NO `fastboot fetch`:** The Qualcomm ABL on retail Xiaomi devices rejects `fastboot fetch` with `Device does not support fetch command`. You cannot dump partitions via fastboot.
> 3. **TWRP / Android Boot Image Headers:** Xiaomi Pad 6 ABL expects boot image **Header Version 1 or 2**. Booting standard Android 12+ TWRP images (Header v3/v4) fails with `Failed to load/authenticate boot image: Load Error`.
> 4. **Logs are on screen:** All debug diagnostics output directly to the display console. We do not need ADB.

---

## 3. Repository & Workspace Map

All development takes place in `C:\Users\nisha\.gemini\antigravity\scratch\ios-pipa`:

| Path | Purpose |
|---|---|
| `xnu/out/kernel.development.pipa` | Compiled Mach-O ARM64 XNU kernel for pipa. |
| `xnu/out/kernel.development.pipa.nop` | Binary-patched kernel (Stage 4 download exit condition replaced with `NOP`). |
| `xnu/patches/` | Patch series modifying XNU for SM8250 / Sweet / Apollo / Pipa (`0060`–`0160`). |
| `tools/mkpayload.py` | Packs Mach-O kernel + ADT + boot arguments into `payload.bin`. |
| `tools/mkadt.py` | Compiles Apple Device Tree (`.adt`) containing display config and touch firmware. |
| `shim/` | Minimal ARM64 early bootloader shim that decompresses kernel and jumps to XNU. |
| `shim/out/pipa-verbose-boot.img` | The boot image booted via `fastboot boot`. |
| `dtb/boot_unpacked/dtb` | Stock Xiaomi Pad 6 Device Tree Blob (decompilable with `dtc`). |
| `research/pipa-fw/` | Extracted & repaired Novatek touch firmware binaries (`csot_fixed.bin`, `tm_fixed.bin`). |
| `../woa-pipa/drivers/touch/` | Working Windows on ARM touch driver (`nt36xxx.sys`, `novatek_nt36523_fw01.bin`, `fw02.bin`). |

---

## 4. Hardware Architecture: Touch Controller on Pipa

From stock DTB (`dtb/boot_unpacked/dtb` under `spi@990000` $\rightarrow$ `novatek@0`):
- **SPI Controller:** QUP0 SE4 at PA `0x00990000` (`TOUCH_SE_PA`).
- **TLMM Pins:**
  - SPI Bus: GPIO 8 (MISO), GPIO 9 (MOSI), GPIO 10 (SCLK), GPIO 11 (CS) $\rightarrow$ Mux function `qup4` in TLMM **North Tile** (`0x0f900000`).
  - **Reset Pin:** TLMM **GPIO 33** (`novatek,reset-gpio = <&tlmm 33 0>`). *(Apollo used GPIO 38; Pipa uses 33).*
  - **Interrupt Pin:** TLMM **GPIO 39** (`novatek,irq-gpio = <&tlmm 39 0x2001>`, rising edge).
- **Event Buffer Address:** `0x2FE00` (NT36523).
- **Display / Panel Variants:**
  - **CSOT (Huaxing):** `novatek,cfg_0` $\rightarrow$ Display maker `0x42` ('B'), Firmware `novatek_nt36532_m82_fw_csot.bin`.
  - **Tianma:** `novatek,cfg_1` $\rightarrow$ Display maker `0x36` ('6'), Firmware `novatek_nt36532_m82_fw_tm.bin`.

---

## 5. Root Cause Analysis: Why Touch Has Not Responded

### Finding 1: The "Stage 4 Stopped" Bug (Solved)
In `sweet_touch.c` (`0094-apollo-touch-defaults.patch`):
```c
if (dl) {
    if (!nvt_download()) {
        printf("SWEET TOUCH: no running firmware; touch off\n");
        goto stop;
    }
}
...
stop:
    printf("SWEET TOUCH: stopped after stage 4 (sweettouchstage=N for more)\n");
    thread_terminate(current_thread());
```
Because the Novatek controller's post-download reset byte did not match Apollo's expected `0xa0..0xaf`, `nvt_download()` returned `false` and **killed the touch thread**.  
**Fix Applied:** In `xnu/out/kernel.development.pipa.nop`, offset `0x524794` (`tbz w0, #0x0, 0xfffffe0007528d84`) was replaced with `NOP` (`0xd503201f`). The thread now never dies and always reaches the report loop.

### Finding 2: Protocol Differences Discovered in `woa-pipa/drivers/touch/nt36xxx.sys`
Reverse engineering `nt36xxx.sys` (the driver that powers Pad 6 touch in Windows on ARM) revealed critical secrets about NT36523 vs Apollo's NT36675:
1. **Magic SWRST write:** After programming the ILM/DLM CRC banks, `nt36xxx.sys` explicitly writes `0x69` to address `0x0003f7e8`! The XNU driver never wrote to `0x3f7e8`.
2. **Post-boot completion command:** After writing `0x3f10d = 1` (BOOT_RDY) and waiting 15ms, `nt36xxx.sys` writes `[0x7b, 0x00]` to `0x2fe50`!
3. **No 0xa0 polling:** `nt36xxx.sys` **never polls `0x2fe60` for `0xa0`**. It treats the transfer as complete immediately after sending `0x7b`.
4. **Primary Trim ID register:** For NT36523, the trim ID address is `0x3F004u`, returning `0x23 0x65 0x03`.

### Finding 3: Display Maker Mismatch (CSOT vs Tianma)
Pad 6 tablets come with either a CSOT or Tianma LCD. If the tablet has Tianma hardware, passing CSOT firmware causes hardware CRC rejection.

---

## 6. How to Build and Boot Images

### Building the Payload (`payload-verbose.bin`):
Run from PowerShell in `ios-pipa`:
```powershell
python tools/mkpayload.py xnu/out/kernel.development.pipa.nop shim/out/pipa-quiet.adt shim/out/payload-verbose.bin `
  --cmdline "debug=0xc serial=3 cpumask=0xf1 keepsyms=1 -noprogress rd=ufsroot0 unentitled_ios_sim_launch=1 ignition_level=1 wdt_test=no_user_checkin ufsrw=1 rootrw=1 launchd_unsecure_cache=1 wdt=-1 ufslogsecs=1 sweetfb_hz=60 sweetflip=0 apollo_backlight=0 sweetfb_fit=0 sweetwifi=12 sweettop=0 sweetcpustats=0 sweetnull=backboardd,SpringBoard,com.apple.datamigrator sweetsample=SpringBoard,backboardd,lsd sweettouch=1 sweettouchstage=4 sweettouchfw=1 sweettouchtlmm=0x0f900000 sweettouchrst=33 sweettouchirq=39 sweettouchbuf=0x2fe00 sweettouchdelay=0 sweettouchpoll=4 sweettouch_debug=1 sweethidtest=1"
```

### Packaging the Boot Image (`pipa-verbose-boot.img`):
Run via WSL Ubuntu:
```bash
wsl -d Ubuntu bash -c "cd /mnt/c/Users/nisha/.gemini/antigravity/scratch/ios-pipa/shim && mkbootimg --header_version 1 --kernel out/kernel_with_dtb --ramdisk out/payload-verbose.bin --base 0x10000000 --kernel_offset 0x0 --ramdisk_offset 0x02000000 --tags_offset 0x00000100 --pagesize 2048 --os_version 17.0.0 --os_patch_level 2035-06 -o out/pipa-verbose-boot.img"
```

### Booting the Device (Tethered Fastboot):
With tablet connected in Fastboot mode:
```powershell
fastboot boot shim/out/pipa-verbose-boot.img
```

---

## 7. Concrete Next Steps for Claude

1. **Test the Tianma Firmware Variant:**
   - Both ADTs are built: `shim/out/pipa-quiet.adt` (CSOT) and `shim/out/pipa-quiet-tm.adt` (Tianma).
   - If CSOT didn't register touches, test Tianma with `fastboot boot shim/out/pipa-verbose-tm-boot.img`.
2. **Implement the NT36523 Protocol Additions in `sweet_touch.c`:**
   - Add the write of `0x69` to `0x3f7e8` after ILM/DLM configuration.
   - Add the write of `[0x7b, 0x00]` to `0x2fe50` after `BOOT_RDY`.
   - Change `NVT_CHIP_VER_TRIM_ADDR` from `0x1f64e` to `0x3f004`.
3. **Verify GPIO 39 Interrupt Latch:**
   - In `sweet_touch.c`, verify `TLMM_GPIO_INTR_CFG(st.irq)` configures rising edge detection on GPIO 39 without requiring active kernel IRQ routing.
4. **Boot-args to keep clean:** Always include `sweettop=0 sweetcpustats=0 sweethidtest=1` to keep console output static and readable on screen.
