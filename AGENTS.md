# Working on ios-pipa

- Read `NOTES.md` before starting; it contains the project roadmap, Xiaomi Pad 6 (`pipa`, Snapdragon 870 / SM8250-AC) hardware facts, current bring-up milestones, and local artifact locations.
- Target device: Xiaomi Pad 6 (`pipa`, SM8250-AC "kona", Adreno 650, 11-inch 2880×1800 144Hz display, Novatek NT36523 SPI touch, quad speakers).
- The user switches agents frequently. Keep `NOTES.md` updated after each milestone with commit hashes, GitHub Actions CI run IDs, build logs, and hardware test observations.
- Kernel builds are automated through GitHub Actions (`.github/workflows/xnu.yml`) using `blacktop/darwin-xnu-build` on `macos-15` runners. Use `gh workflow run xnu.yml` to trigger CI builds.
- Device bring-up uses temporary `fastboot boot` images (`shim/out/shim-boot-pipa.img`) or UEFI multiboot (`loader/xnuloader.efi`). Do NOT overwrite stock bootloaders, recovery, modem, or persist partitions.
- The iOS / iPadOS root volume is placed on internal UFS in the existing `userdata` partition.
- Keep proprietary firmware dumps, device serial numbers, and Apple copyrighted files out of git.
