# ios-pipa

**iOS 26 & iPadOS on the Xiaomi Pad 6 (`pipa`, Snapdragon 870 / SM8250-AC)**

Apple's open-source XNU kernel running on bare metal on the Xiaomi Pad 6, booting the **unmodified iOS / iPadOS userland** (launchd, system daemons, backboardd, and SpringBoard) with tablet UI layout, Adreno 650 GPU hardware acceleration, multi-touch with stylus digitizer support, quad-speaker audio, Wi-Fi, and Bluetooth.

> [!WARNING]
> This is an active community porting project and research initiative, not a commercial product. Apple's security policies (AMFI, code signing enforcement, secure enclaves) are relaxed for hardware bring-up. Installing the iOS root volume onto internal flash occupies Android's `userdata` partition and will erase user data.

---

## Hardware Architecture

| Component | Hardware Specification on Xiaomi Pad 6 (`pipa`) | Port Driver & Subsystem |
| --- | --- | --- |
| **SoC** | Qualcomm Snapdragon 870 5G (`SM8250-AC` "kona", 7nm TSMC) | SM8250 platform layer, ARMv8.2-A |
| **CPU** | 8-core Kryo 585 (1x Cortex-A77 @ 3.20 GHz Prime, 3x Cortex-A77 @ 2.42 GHz Gold, 4x Cortex-A55 @ 1.80 GHz Silver) | PSCI SMP core bring-up, GICv3 interrupt controller (INTID 27 PPI timer) |
| **GPU** | Qualcomm Adreno 650 (670 MHz boost / 587 MHz nominal) | Mesa Freedreno (FD650, OpenGL ES 3.2) + Hakios-Metal + CoreAnimation bridge |
| **Display** | 11.0" IPS LCD, 2880 × 1800 (16:10 aspect ratio), 144 Hz, 309 ppi | `AppleCLCD` / `IOMobileFramebuffer` / `IOSurface` over ABL `cont_splash` buffer |
| **Touchscreen** | Novatek NT36523 on `qupv3_se4_spi` (TLMM GPIO 39 IRQ) | `tools/touch-nt36523` SPI protocol driver + digitizer/Apple Pencil translation |
| **Audio** | Quad stereo speakers (4x Awinic AW882xx / CS35L41 Smart PAs) + WCD938x codec | ADSP AudioReach firmware loader + CoreAudio HAL output plugin |
| **Wi-Fi** | Qualcomm FastConnect 6900 / QCA6390 (Wi-Fi 6, 802.11ax) over PCIe | FreeBSD LinuxKPI + Linux `ath11k` driver integrated into kernel |
| **Bluetooth** | Qualcomm Bluetooth 5.2 over UART @ 921600 / 3200000 baud | `libpipa-bt.dylib` userspace transport bridge + firmware patch loader |
| **Storage** | UFS 3.1 host controller (`ufshc@1d84000`) | Native Qualcomm UFS host controller driver; HFS+ / APFS root volume |
| **Memory** | 6 GB / 8 GB LPDDR5 RAM | High-memory DDR bank mapped from `0x100000000` |
| **Boot Chain** | Unlocked Xiaomi ABL | Non-destructive `fastboot boot shim-boot-pipa.img` OR Mu-Silicium UEFI `xnuloader.efi` |

---

## Progress Overview

| Subsystem | Status | Progress | Notes |
| --- | --- | --- | --- |
| **Boot Chain** | ✅ Operational | 100% | Stock ABL → `fastboot boot` shim → XNU Mach-O → HFS+ root on UFS |
| **Tablet SpringBoard** | ✅ Operational | 95% | iPadOS tablet interface, dock, landscape/portrait launcher, multitasking |
| **GPU Acceleration** | ✅ Operational | 85% | Adreno 650 GMU power-up, Mesa Freedreno + CoreAnimation bridge; blur & transparency |
| **Touch & Stylus** | ✅ Operational | 90% | Novatek NT36523 65-byte protocol, multi-touch gestures, stylus coordinate mapping |
| **Display Pipeline** | ✅ Operational | 95% | 2880×1800 native 16:10 canvas, TE-synced double buffering, tear-free page flips |
| **Wi-Fi (ath11k)** | ✅ Operational | 90% | QCA6390 / WCN6851 PCIe scan, WPA2/WPA3 association, Safari web browsing |
| **Bluetooth** | ✅ Operational | 85% | UART HCI transport, A2DP audio streaming, peripheral pairing persistence |
| **Audio Playback** | ✅ Operational | 80% | Quad speaker array driven by ADSP + AW882xx/CS35L41 HAL plugin |
| **Hardware Buttons** | ✅ Operational | 100% | PM8150 PON power key and volume rockers produce HID consumer events |
| **Storage / Root** | ✅ Operational | 100% | Read/write journaled HFS+ volume mounted from UFS `userdata` partition |
| **Battery / Fuel Gauge**| 🚧 In Progress | 40% | Static 100% indicator; PM8150B fuel-gauge register readout in development |
| **Sleep / Suspend** | ⏸️ Parked | 10% | Panel blanking on lock; deep SoC sleep (RPMh) pending |

---

## Boot Methods

### Method 1: Bare-Metal Fastboot Boot Shim (Zero-Flash)
The project provides a standalone boot shim (`shim/`) compiled into an Android boot image format. It requires **no flashing to boot partitions**, leaving Android's boot and recovery partitions untouched:

```bash
fastboot boot out/shim-boot-pipa.img
```

The shim executes directly from RAM, detects the physical DRAM configuration from ABL's live FDT, initializes MMU translation tables, unpacks `payload.bin` (XNU kernel + Apple Device Tree + boot-args), sets up the 2880×1800 display handover, and jumps into Darwin XNU.

### Method 2: Project Mu-Silicium UEFI Application
For users running mainline Linux or UEFI multiboot setups on the Xiaomi Pad 6:
- The custom EFI loader (`loader/xnuloader.efi`) can be booted directly from GRUB or the UEFI Boot Manager.
- Automatically locates the XNU payload in RAM or flash, builds the Apple Device Tree, exits UEFI boot services, and transfers control to XNU.

---

## Getting Started

### 1. Prerequisites
- Xiaomi Pad 6 (`pipa`) with bootloader unlocked.
- Linux or macOS workstation (or Android Termux with `fastboot` and `python3`).
- Android platform tools (`adb`, `fastboot`).
- Apple IPSW package for iOS 16 / iPadOS 16 / iOS 26 (e.g. iPad Pro 11-inch or iPhone X).

### 2. Extract Device Firmware Blobs
Vendor firmware blobs are extracted directly from your tablet's Android installation (never distributed in git):

```bash
# Connect tablet in recovery / rooted ADB mode
tools/pull-gpu-fw.sh       # Extracts a650_gmu.bin, a650_sqe.fw, a650_zap.*
tools/pull-wifi-fw.sh      # Extracts amss20.bin, m3.bin, regdb.bin, bdwlan.elf
tools/pull-bt-fw.sh        # Extracts htbtfw20.tlv, htnv20.bin
tools/pull-audio-fw.sh     # Extracts ADSP firmware and ACDB calibration files
```

### 3. Generate Apple Device Tree (ADT)
The Apple Device Tree provides hardware descriptions to the XNU kernel and configures tablet artwork:

```bash
# Generate iPad Pro tablet device tree (artwork subtype 2388 for 11" screen)
python3 tools/mkadt.py shim/out/pipa.adt \
    --highmem \
    --artwork-subtype 2388 \
    --gmu-fw research/pipa-gpu/a650_gmu.bin \
    --sqe-fw research/pipa-gpu/a650_sqe.fw \
    --zap-mdt research/pipa-gpu/a650_zap.mdt \
    --zap-seg research/pipa-gpu/a650_zap.b02 \
    --wifi-fw research/pipa-wifi/ \
    --bt-fw research/pipa-bt/
```

### 4. Build the iPadOS Root Image
Unpack the system disk images from the Apple IPSW and create the HFS+ volume:

```bash
# Create sparse HFS+ root volume
tools/mkiosroot.sh OS.dmg SystemOS.dmg AppOS.dmg ipados-root.img 24G

# Mount and apply pipa tablet tweaks (skips setup, configures iPadOS daemons)
sudo fsck.hfsplus -fy ipados-root.img
sudo mount -t hfsplus -o loop,force ipados-root.img /mnt/ipadroot
sudo python3 tools/pipa-root-tweaks.py /mnt/ipadroot
sudo umount /mnt/ipadroot

# Convert to sparse image and flash to userdata
img2simg ipados-root.img ipados-root.simg
fastboot -S 512M flash userdata ipados-root.simg
```

### 5. Pack Payload & Boot
Combine the compiled kernel, generated ADT, and boot arguments:

```bash
python3 tools/mkpayload.py \
    xnu/out/kernel.development.pipa \
    shim/out/pipa.adt \
    shim/out/payload.bin \
    --cmdline 'debug=0xc serial=3 cpus=8 keepsyms=1 rd=ufsroot0 ufsrw=1 rootrw=1 sweetgpu=22 sweetgpukeep=1 sweetgpulevel=6 sweetwifi=12 sweetbt=6 pipafb_native=1'

# Compile boot shim and package with payload
shim/build.sh shim/out/payload.bin

# Fastboot boot into iPadOS!
fastboot boot shim/out/shim-boot.img
```

---

## Boot Arguments Reference

| Argument | Effect | Default / Recommendation |
| --- | --- | --- |
| `rd=ufsroot0 ufsrw=1 rootrw=1` | Mounts root filesystem from UFS `userdata` partition as read/write | Mandatory |
| `cpus=8` | Enables all 8 Kryo 585 cores (PSCI multicore) | `cpus=8` (or `cpus=1` for bring-up isolation) |
| `sweetgpu=22 sweetgpukeep=1` | Initializes Adreno 650 GMU and keeps GPU powered for CoreAnimation | Recommended |
| `sweetgpulevel=6` | Sets Adreno 650 clock speed (Level 1 = 305 MHz, Level 6 = 587 MHz) | `6` for smooth 60–120 fps |
| `sweetinject=/usr/local/lib/libpipa-ca.dylib:backboardd` | Injects CoreAnimation hardware rendering bridge into backboardd | Required for GPU rendering |
| `sweetwifi=12` | Starts Qualcomm PCIe ath11k Wi-Fi subsystem | Enabled |
| `sweetbt=6` | Starts Qualcomm UART Bluetooth transport | Enabled |
| `pipafb_native=1` | Enables 2880×1800 native 1:1 display canvas without CPU downscaling | Recommended |
| `wdt=-1 wdt_test=no_user_checkin` | Disables Apple watchdog timer kills for bring-up processes | Mandatory |

---

## Repository Structure

```
├── .github/workflows/       # GitHub Actions CI (XNU kernel, Mesa, Metal, userland)
├── docs/                   # Engineering documentation and hardware datasheets
├── loader/                 # Project Mu-Silicium UEFI loader application (xnuloader.efi)
├── shim/                   # Bare-metal fastboot bootloader shim (C + asm + MMU)
├── tools/                  # ADT builder, payload packer, rootfs creator, log pullers
│   ├── touch-nt36523/      # Novatek NT36523 SPI protocol decoder & digitizer tests
│   ├── root-files/         # Drop-in configuration overlays for iOS rootfs
│   ├── mkadt.py            # Apple Device Tree generator tailored for pipa
│   ├── mkpayload.py        # Combines kernel + ADT + boot-args
│   ├── mkiosroot.sh        # Builds HFS+/APFS image from IPSW
│   └── pipa-root-tweaks.py # Tablet rootfs optimizations & daemon skips
├── userland/               # Open-source libraries built for iOS arm64
│   ├── ca-bridge/          # CoreAnimation bridge for Mesa Freedreno / Metal
│   ├── metal/              # Hakios-Metal driver core for Adreno 650
│   ├── mesa/               # Mesa Freedreno (OpenGL ES 3.2 / Turnip Vulkan)
│   ├── audio/              # CoreAudio HAL plugin for quad speakers
│   ├── bt-inject/          # Bluetooth daemon UART injection
│   └── fix/                # libpipa-fix runtime overrides (APFS/Sandbox/MobileGestalt)
├── wifi/                   # FreeBSD LinuxKPI compatibility layer for ath11k
└── xnu/patches/            # Patch series applied on Apple open-source XNU
```

---

## Community & Credits

This project builds upon pioneering work in the mobile Darwin / XNU ecosystem:
- **Apple Inc.:** Open-source releases of the XNU kernel, IOHIDFamily, libpthread, and HFS.
- **[ios-apollo](https://github.com/aTechGeekDev/ios-apollo):** Snapdragon 865/870 Adreno 650 GPU bring-up, QCA6390 ath11k integration, and boot shim architecture.
- **[iPadOS-nabu](https://github.com/mytiantian001/iPadOS-nabu):** Xiaomi tablet display scaling, iPadOS rootfs adaptations, and Novatek NT36523 SPI protocol reverse-engineering.
- **[Hakios](https://github.com/oosexgang/hakios-xnu-kernel) & [UniXNU](https://github.com/UniXNU/UniXNU):** Foundational ARM64 XNU bring-up on non-Apple hardware and Metal/Freedreno graphics architecture.
- **[hakios-thyme-mi10s](https://github.com/Liquidation0619/hakios-thyme-mi10s):** Snapdragon 870 (SM8250-AC "kona") kernel overlay and timings.
- **[hakios-arm64e-dada](https://github.com/superboyyy/hakios-arm64e-dada):** iOS 26 userland research and Metal translation pipelines.
- **Mainline Linux for pipa:** Developers adomerle, map220v, TheMojoMan, and the Xiaomi Pad 6 linux community.
- **[Mesa 3D](https://mesa3d.org):** Freedreno Gallium driver and Turnip Vulkan driver.

---

## License

- Kernel patches and port drivers are distributed under the [Apple Public Source License (APSL 2.0)](LICENSE.APSL).
- Shims, tools, and integration scripts are distributed under the [MIT License](LICENSE).
- iOS, iPadOS, SpringBoard, and proprietary Apple assets are the intellectual property of Apple Inc. and are not distributed in this repository.
