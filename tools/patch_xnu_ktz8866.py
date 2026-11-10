#!/usr/bin/env python3
"""
Binary-patch xnu/out/kernel.development.pipa.nop (after patch_xnu_nt36532.py)
to support Xiaomi Pad 6 (pipa) KTZ8866 I2C backlight @ 0x11 on QUPv3 SE11 (0x00a8c000).

Reuses compiled sweet_haptic GENI I2C transport (_i2c_xfer, _regs_read, _regs_write)
since aw8697_haptic is disabled on pipa, and keeps sweet_backlight's fade thread +
AppleARMBacklight slider + SweetFramebuffer power-off integration intact.
"""
import os
import re
import shutil
import subprocess
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KERNEL = f"{BASE}/xnu/out/kernel.development.pipa.nop"
ORIG_KERNEL = f"{BASE}/xnu/out/kernel.development.pipa"
BAK_KERNEL = f"{BASE}/xnu/out/kernel.development.pipa.nop.bak_prektz"


def asm_at(vma, code_lines):
    """Assemble + link AArch64 instructions at a fixed VMA using gas + ld --defsym."""
    syms = {}
    patched_lines = []
    for line in code_lines:
        def repl(m):
            op = m.group(1)
            addr = m.group(2)
            sym = f"_abs_{addr}"
            syms[sym] = addr
            return f"{op} {sym}"

        new_line = re.sub(
            r"\b(b(?:\.[a-z]+)?|bl|cbnz\s+\w+,|cbz\s+\w+,|adrp\s+\w+,)\s*(0x[0-9a-fA-F]+)\b",
            repl,
            line,
        )
        patched_lines.append(new_line)

    src = ".text\n" + "\n".join(patched_lines) + "\n"
    defsym_args = []
    for sym, addr in sorted(syms.items()):
        defsym_args.extend(["--defsym", f"{sym}={addr}"])
    with tempfile.TemporaryDirectory() as d:
        sfile = os.path.join(d, "p.s")
        ofile = os.path.join(d, "p.o")
        efile = os.path.join(d, "p.elf")
        bfile = os.path.join(d, "p.bin")
        with open(sfile, "w") as f:
            f.write(src)
        subprocess.run(
            ["aarch64-linux-gnu-as", "-march=armv8-a", "-o", ofile, sfile],
            check=True,
        )
        subprocess.run(
            ["aarch64-linux-gnu-ld", f"-Ttext={hex(vma)}", "-N", "-e", "0", "--no-warn-rwx-segments"]
            + defsym_args
            + ["-o", efile, ofile],
            check=True,
        )
        subprocess.run(
            ["aarch64-linux-gnu-objcopy", "-O", "binary", efile, bfile],
            check=True,
        )
        return open(bfile, "rb").read()


def patch_block(data, off, max_end, orig_data, expected_prefix, new_bytes, label):
    assert off + len(new_bytes) <= max_end, (
        f"Overflow in {label}: {hex(off + len(new_bytes))} > {hex(max_end)}"
    )
    if expected_prefix is not None:
        cur = bytes(orig_data[off : off + len(expected_prefix)])
        assert cur == expected_prefix, (
            f"Mismatch at {label} ({hex(off)}):\n"
            f"  got: {cur.hex()}\n"
            f"  exp: {expected_prefix.hex()}"
        )
    data[off : off + len(new_bytes)] = new_bytes
    print(f"Patched {label} @ {hex(off)} ({len(new_bytes)} / {max_end - off} bytes)")


def main():
    assert os.path.exists(KERNEL), f"Missing {KERNEL}"
    if not os.path.exists(BAK_KERNEL):
        shutil.copy2(KERNEL, BAK_KERNEL)
        print(f"Backed up {KERNEL} -> {BAK_KERNEL}")

    orig = open(ORIG_KERNEL, "rb").read()
    data = bytearray(open(KERNEL, "rb").read())

    # Sanity check reused kernel rodata ("QUP0\0")
    assert data[0xA4000:0xA4005] == b"QUP0\x00", f"Unexpected bytes at 0xa4000: {data[0xA4000:0xA4008]!r}"

    # 1. Retarget _i2c_xfer (sweet_haptic.c, 0x56909c) from AW_ADDRESS 0x5a -> KTZ8866 0x11
    #    (0x5a << 9 = 0xb400; 0x11 << 9 = 0x2200)
    patch_block(
        data, 0x569174, 0x569184, orig,
        asm_at(
            0x569174,
            [
                "mov w9, #0xb400",
                "movk w9, #0x1000, lsl #16",
                "mov w10, #0xb400",
                "movk w10, #0x800, lsl #16",
            ],
        ),
        asm_at(
            0x569174,
            [
                "mov w9, #0x2200",
                "movk w9, #0x1000, lsl #16",
                "mov w10, #0x2200",
                "movk w10, #0x800, lsl #16",
            ],
        ),
        "_i2c_xfer slave addr 0x5a -> 0x11",
    )
    patch_block(
        data, 0x569380, 0x569384, orig,
        asm_at(0x569380, ["mov w10, #0x5a"]),
        asm_at(0x569380, ["mov w10, #0x11"]),
        "_i2c_xfer log addr 0x5a -> 0x11",
    )
    patch_block(
        data, 0x568F00, 0x568F08, orig,
        asm_at(0x568F00, ["mov w9, #0xb400", "movk w9, #0x1000, lsl #16"]),
        asm_at(0x568F00, ["mov w9, #0x2200", "movk w9, #0x1000, lsl #16"]),
        "_regs_read inlined RX slave addr 0x5a -> 0x11",
    )

    # 2. _read_level(uint32_t *value) @ 0x56b1d8 .. 0x56b240
    #    KTZ8866 reg 0x04 = BL_BRT_LSB[2:0], reg 0x05 = BL_BRT_MSB[10:3]
    read_level_code = asm_at(
        0x56B1D8,
        [
            "sub sp, sp, #0x30",
            "stp x20, x19, [sp, #0x10]",
            "stp x29, x30, [sp, #0x20]",
            "add x29, sp, #0x20",
            "mov x19, x0",
            "strh wzr, [sp, #0xe]",
            "mov w0, #0x04",
            "add x1, sp, #0xe",
            "mov w2, #1",
            "bl 0x568e20",          # _regs_read(4, &r4, 1)
            "cbz w0, 1f",
            "mov w0, #0x05",
            "add x1, sp, #0xf",
            "mov w2, #1",
            "bl 0x568e20",          # _regs_read(5, &r5, 1)
            "cbz w0, 1f",
            "ldrb w8, [sp, #0xe]",
            "ldrb w9, [sp, #0xf]",
            "and w8, w8, #0x7",
            "orr w8, w8, w9, lsl #3",
            "str w8, [x19]",
            "mov w0, #1",
            "1:",
            "ldp x29, x30, [sp, #0x20]",
            "ldp x20, x19, [sp, #0x10]",
            "add sp, sp, #0x30",
            "ret",
        ],
    )
    patch_block(
        data, 0x56B1D8, 0x56B240, orig,
        asm_at(0x56B1D8, ["sub sp, sp, #0x30", "stp x20, x19, [sp, #0x10]"]),
        read_level_code,
        "_read_level (KTZ8866 0x04/0x05)",
    )

    # 3. _write_level(uint32_t brightness) @ 0x56a3c0 .. 0x56a540
    #    Matches Linux ktz8866_brightness_set + sweet_backlight write_level:
    #      on:  if (!lit) reg 0x08 = 0x5f, lit=1; then reg 0x04 = b&7, reg 0x05 = b>>3, level = b
    #      off: if (lit)  reg 0x08 = 0x1f, lit=0, msleep(10); then reg 0x04 = 0, reg 0x05 = 0
    write_level_code = asm_at(
        0x56A3C0,
        [
            "sub sp, sp, #0x40",
            "stp x22, x21, [sp, #0x10]",
            "stp x20, x19, [sp, #0x20]",
            "stp x29, x30, [sp, #0x30]",
            "add x29, sp, #0x30",
            "mov w8, #0x7ff",
            "cmp w0, w8",
            "csel w19, w0, w8, lo",
            "cbz w19, 1f",
            "cmp w19, #2",
            "mov w8, #2",
            "csel w19, w8, w19, lo",
            "1:",
            "adrp x21, 0xc92000",   # _lit @ +0xdd0
            "ldrb w20, [x21, #0xdd0]",
            "cmp w19, #0",
            "cset w22, ne",
            "cmp w20, w22",
            "b.eq 2f",
            "cbz w22, 3f",
            "mov w8, #0x5f",
            "strb w8, [sp, #0xe]",
            "mov w0, #0x08",
            "add x1, sp, #0xe",
            "mov w2, #1",
            "bl 0x569570",          # _regs_write(0x08, &0x5f, 1)
            "cbz w0, 9f",
            "strb w22, [x21, #0xdd0]",
            "b 2f",
            "3:",
            "mov w8, #0x1f",
            "strb w8, [sp, #0xe]",
            "mov w0, #0x08",
            "add x1, sp, #0xe",
            "mov w2, #1",
            "bl 0x569570",          # _regs_write(0x08, &0x1f, 1)
            "cbz w0, 9f",
            "strb wzr, [x21, #0xdd0]",
            "mov w0, #10",
            "mov w1, #0x4240",
            "movk w1, #0xf, lsl #16",
            "bl 0x357770",          # _delay_for_interval(10, 1000000 ns)
            "2:",
            "and w8, w19, #7",
            "strb w8, [sp, #0xe]",
            "mov w0, #0x04",
            "add x1, sp, #0xe",
            "mov w2, #1",
            "bl 0x569570",
            "cbz w0, 9f",
            "lsr w8, w19, #3",
            "strb w8, [sp, #0xe]",
            "mov w0, #0x05",
            "add x1, sp, #0xe",
            "mov w2, #1",
            "bl 0x569570",
            "cbz w0, 9f",
            "cbz w19, 4f",
            "adrp x8, 0x11fc000",   # _level @ +0x214
            "str w19, [x8, #0x214]",
            "4:",
            "mov w0, #1",
            "9:",
            "ldp x29, x30, [sp, #0x30]",
            "ldp x20, x19, [sp, #0x20]",
            "ldp x22, x21, [sp, #0x10]",
            "add sp, sp, #0x40",
            "ret",
        ],
    )
    patch_block(
        data, 0x56A3C0, 0x56A540, orig,
        asm_at(0x56A3C0, ["sub sp, sp, #0x40", "stp x22, x21, [sp, #0x10]"]),
        write_level_code,
        "_write_level (KTZ8866 0x08/0x04/0x05)",
    )

    # 4. Helper rcg_rate(w0=cfg, w1=m, w2=not_n_minus_m) in dead _find_channel space @ 0x56aeb0
    rcg_rate_code = asm_at(
        0x56AEB0,
        [
            "ubfx w8, w0, #8, #3",
            "cbnz w8, 1f",
            "mov x9, #0xf800",
            "movk x9, #0x124, lsl #16",   # 19,200,000
            "b 4f",
            "1:",
            "cmp w8, #1",
            "b.ne 2f",
            "mov x9, #0x8c00",
            "movk x9, #0x23c3, lsl #16",  # 600,000,000
            "b 4f",
            "2:",
            "cmp w8, #6",
            "b.ne 3f",
            "mov x9, #0x4600",
            "movk x9, #0x11e1, lsl #16",  # 300,000,000
            "b 4f",
            "3:",
            "mov x0, #0",
            "ret",
            "4:",
            "and w10, w0, #0x1f",
            "cbz w10, 5f",
            "lsl x9, x9, #1",
            "add w10, w10, #1",
            "uxtw x10, w10",
            "udiv x9, x9, x10",
            "5:",
            "ubfx w11, w0, #12, #2",
            "cbz w11, 6f",
            "mvn w12, w2",
            "and w12, w12, #0xffff",
            "and w1, w1, #0xffff",
            "add w12, w12, w1",
            "cbz w12, 6f",
            "uxtw x1, w1",
            "mul x9, x9, x1",
            "uxtw x12, w12",
            "udiv x9, x9, x12",
            "6:",
            "mov w0, w9",
            "ret",
        ],
    )
    patch_block(
        data, 0x56AEB0, 0x56AF94, orig,
        asm_at(0x56AEB0, ["sub sp, sp, #0x40", "stp x20, x19, [sp, #0x20]"]),
        rcg_rate_code,
        "_rcg_rate helper",
    )

    # 5. se11_hw_setup(void) @ 0x56b404..0x56b740 (dead _spmi_write space)
    #    Maps TLMM(GPIO60..61), GCC, SE11(0xa8c000), votes QUP0/GCC, sets pinmux + GENI I2C 400kHz.
    se11_hw_setup_code = asm_at(
        0x56B404,
        [
            "sub sp, sp, #0x40",
            "stp x24, x23, [sp, #0x10]",
            "stp x22, x21, [sp, #0x20]",
            "stp x20, x19, [sp, #0x28]?_skip",
        ][:0] + [  # Build clean prologue with 0x50 stack
            "sub sp, sp, #0x50",
            "stp x24, x23, [sp, #0x10]",
            "stp x22, x21, [sp, #0x20]",
            "stp x20, x19, [sp, #0x30]",
            "stp x29, x30, [sp, #0x40]",
            "add x29, sp, #0x40",
            "adrp x19, 0x11fc000",  # sweet_haptic globals +0x168..+0x188
            # ml_io_map(0x0f53c000, 0x2000) -> _pins_i2c
            "mov w0, #0xc000",
            "movk w0, #0xf53, lsl #16",
            "mov w1, #0x2000",
            "bl 0x4c9308",
            "cbz x0, 99f",
            "str x0, [x19, #0x168]",
            # ml_io_map(0x00100000, 0x60000) -> _gcc
            "mov w0, #0x100000",
            "mov w1, #0x60000",
            "bl 0x4c9308",
            "cbz x0, 99f",
            "str x0, [x19, #0x170]",
            "mov x20, x0",          # x20 = gcc
            # ml_io_map(0x00a8c000, 0x4000) -> _se
            "mov w0, #0xc000",
            "movk w0, #0xa8, lsl #16",
            "mov w1, #0x4000",
            "bl 0x4c9308",
            "cbz x0, 99f",
            "str x0, [x19, #0x178]",
            "mov x21, x0",          # x21 = se
            # apollo_rpmh_bcm_vote("QUP0", 1, 1)
            "adrp x0, 0xa4000",
            "mov w1, #1",
            "mov w2, #1",
            "bl 0x553674",
            "cbz w0, 99f",
            # apollo_gcc_vote(gcc + 0x52008, 0x3c0000)
            "mov w8, #0x2008",
            "movk w8, #0x5, lsl #16",
            "add x0, x20, x8",
            "mov w1, #0x3c0000",
            "bl 0x553570",
            # poll bus clocks up to 1000 * 10us
            "mov w22, #1000",
            "1:",
            "mov w8, #0x3140",
            "movk w8, #0x2, lsl #16",
            "ldr w9, [x20, x8]",
            "tbnz w9, #31, 2f",
            "mov w8, #0x3138",
            "movk w8, #0x2, lsl #16",
            "ldr w9, [x20, x8]",
            "tbnz w9, #31, 2f",
            "mov w8, #0x8004",
            "movk w8, #0x1, lsl #16",
            "ldr w9, [x20, x8]",
            "tbnz w9, #31, 2f",
            "mov w8, #0x8008",
            "movk w8, #0x1, lsl #16",
            "ldr w9, [x20, x8]",
            "tbz w9, #31, 3f",
            "tbnz w9, #1, 3f",
            "2:",
            "subs w22, w22, #1",
            "b.eq 99f",
            "mov w0, #10",
            "mov w1, #1000",
            "bl 0x357770",
            "b 1b",
            "3:",
            # se_clock_on: check QUP1_S3_RCG + 0x14 (0x183b4)
            "mov w23, #0",          # select = 0
            "mov w8, #0x83b4",
            "movk w8, #0x1, lsl #16",
            "ldr w9, [x20, x8]",
            "tbz w9, #0, 6f",
            # DFS enabled: scan lvl = 0..7 for 19,200,000 Hz
            "mov w22, #0",
            "mov w24, #0xf800",
            "movk w24, #0x124, lsl #16",
            "4:",
            "mov w8, #0x83bc",
            "movk w8, #0x1, lsl #16",
            "add w8, w8, w22, lsl #2",
            "ldr w0, [x20, x8]",
            "add w8, w8, #0x40",
            "ldr w1, [x20, x8]",
            "add w8, w8, #0x40",
            "ldr w2, [x20, x8]",
            "bl 0x56aeb0",          # _rcg_rate
            "cmp w0, w24",
            "b.eq 5f",
            "add w22, w22, #1",
            "cmp w22, #8",
            "b.lo 4b",
            "b 99f",
            "5:",
            "mov w23, w22",
            "b 8f",
            "6:",
            # Non-DFS: configure RCG + 0x183a4 -> XO & trigger
            "mov w8, #0x83a4",
            "movk w8, #0x1, lsl #16",
            "ldr w9, [x20, x8]",
            "mov w10, #0x371f",
            "bic w9, w9, w10",
            "orr w9, w9, #1",
            "str w9, [x20, x8]",
            "mov w8, #0x83a0",
            "movk w8, #0x1, lsl #16",
            "ldr w9, [x20, x8]",
            "orr w9, w9, #1",
            "str w9, [x20, x8]",
            "mov w22, #500",
            "7:",
            "mov w8, #0x83a0",
            "movk w8, #0x1, lsl #16",
            "ldr w9, [x20, x8]",
            "tbz w9, #0, 8f",
            "subs w22, w22, #1",
            "b.eq 99f",
            "mov w0, #10",
            "mov w1, #1000",
            "bl 0x357770",
            "b 7b",
            "8:",
            # Vote VOTE_QUP1_S3 (1 << 25) and wait QUP1_S3_CBCR (0x1839c)
            "mov w8, #0x2008",
            "movk w8, #0x5, lsl #16",
            "add x0, x20, x8",
            "mov w1, #0x02000000",
            "bl 0x553570",
            "mov w22, #1000",
            "9:",
            "mov w8, #0x839c",
            "movk w8, #0x1, lsl #16",
            "ldr w9, [x20, x8]",
            "tbz w9, #31, 10f",
            "subs w22, w22, #1",
            "b.eq 99f",
            "mov w0, #10",
            "mov w1, #1000",
            "bl 0x357770",
            "b 9b",
            "10:",
            # TLMM GPIO60 + GPIO61 = qup11 (func=1 -> cfg=4, 2mA, no pull)
            "ldr x8, [x19, #0x168]",
            "mov w9, #4",
            "str w9, [x8]",
            "str w9, [x8, #0x1000]",
            # Check GENI SE11 proto == 3 (I2C), FIFO enabled, fifo_depth >= 4
            "ldr w8, [x21, #0x68]",
            "and w8, w8, #0xff00",
            "cmp w8, #0x300",
            "b.ne 99f",
            "ldr w8, [x21, #0x64]",
            "tbnz w8, #0, 99f",
            "ldr w8, [x21, #0xe24]",
            "ubfx w8, w8, #16, #6",
            "cmp w8, #4",
            "b.lo 99f",
            "str w8, [x19, #0x188]",  # _fifo_depth
            # GENI init register writes (matching sweet_haptic i2c_init)
            "str wzr, [x21, #0xe18]",
            "mov w9, #-1",
            "str w9, [x21, #0x618]",
            "str w9, [x21, #0x648]",
            "str w9, [x21, #0xc44]",
            "str w9, [x21, #0xd44]",
            "ldr w9, [x21, #0x28]",
            "orr w9, w9, #0x7f",
            "str w9, [x21, #0x28]",
            "ldr w9, [x21, #0xe30]",
            "orr w9, w9, #0xf",
            "str w9, [x21, #0xe30]",
            "mov w9, #0x7f",
            "str w9, [x21, #0x24]",
            "mov w9, #1",
            "str w9, [x21, #0x20]",
            "ldr w9, [x21, #0x258]",
            "bic w9, w9, #1",
            "str w9, [x21, #0x258]",
            "sub w9, w8, #3",
            "str w9, [x21, #0x810]",
            "sub w9, w8, #2",
            "str w9, [x21, #0x814]",
            "str wzr, [x21, #0x80c]",
            "ldr w9, [x21, #0xe1c]",
            "orr w9, w9, #0xf",
            "str w9, [x21, #0xe1c]",
            "ldr w9, [x21, #0x614]",
            "mov w10, #0x7f",
            "movk w10, #0x7fc0, lsl #16",
            "orr w9, w9, w10",
            "str w9, [x21, #0x614]",
            "mov w9, #0xf8fe",
            "movk w9, #0x7, lsl #16",
            "str w9, [x21, #0x260]",
            "str w9, [x21, #0x284]",
            "mov w9, #0xfefe",
            "movk w9, #0xf, lsl #16",
            "str w9, [x21, #0x264]",
            "str w9, [x21, #0x288]",
            "str wzr, [x21, #0x254]",
            "and w9, w23, #7",
            "str w9, [x21, #0x7c]",
            "mov w9, #0x21",
            "str w9, [x21, #0x48]",
            "mov w9, #0x3018",
            "movk w9, #0x50, lsl #16",
            "str w9, [x21, #0x278]",
            "mov w0, #1",
            "b 98f",
            "99:",
            "mov w0, #0",
            "98:",
            "ldp x29, x30, [sp, #0x40]",
            "ldp x20, x19, [sp, #0x30]",
            "ldp x22, x21, [sp, #0x20]",
            "ldp x24, x23, [sp, #0x10]",
            "add sp, sp, #0x50",
            "ret",
        ],
    )
    patch_block(
        data, 0x56B404, 0x56B740, orig,
        bytes.fromhex("ff4304d1fc6f0ba9"),
        se11_hw_setup_code,
        "_se11_hw_setup helper",
    )

    # 6. Patch _apollo_backlight_init body @ 0x56a8e0 .. 0x56ac80 -> runs se11_hw_setup(),
    #    probes KTZ8866 (0x04/0x05 + 0x08), populates globals, and jumps into existing
    #    logging/fade_thread setup @ 0x56ac80!
    bl_init_body = asm_at(
        0x56A8E0,
        [
            "bl 0x56b404",          # _se11_hw_setup()
            "cbz w0, 0x56ad30",     # failed -> left alone
            "adrp x19, 0x11fc000",
            "adrp x20, 0xc92000",
            "mov x21, x20",
            "str wzr, [x19, #0x210]",   # _module = 0 ('A')
            "mov w8, #0x7ff",
            "str w8, [x20, #0xdd4]",    # _maximum = 0x7ff (2047)
            "add x0, x19, #0x214",
            "bl 0x56b1d8",              # _read_level(&_level)
            "cbz w0, 0x56ad30",
            "mov w0, #0x08",
            "add x1, sp, #0x50",
            "mov w2, #1",
            "bl 0x568e20",              # _regs_read(0x08, sp+0x50, 1)
            "cbz w0, 0x56ad30",
            "ldrb w8, [sp, #0x50]",
            "ubfx w9, w8, #6, #1",      # KTZ8866 BL_EN = bit 6 (0x40)
            "lsl w10, w9, #7",          # encode in bit 7 for 0x56ac80 ldrsb/ubfx!
            "strb w10, [sp, #0x50]",
            "cbz w9, 1f",
            "ldr w11, [x19, #0x214]",
            "cbnz w11, 1f",
            "mov w11, #0x3f1",          # if BL_EN=1 and brt=0 (e.g. PWM default), seed 0x3f1
            "str w11, [x19, #0x214]",
            "1:",
            "b 0x56ac80",
        ],
    )
    patch_block(
        data, 0x56A8E0, 0x56AC80, orig,
        asm_at(0x56A8E0, ["mov w0, #0xc440000", "mov w1, #0x1100"]),
        bl_init_body,
        "_apollo_backlight_init (KTZ8866 probe)",
    )

    with open(KERNEL, "wb") as f:
        f.write(data)
    print(f"Wrote KTZ8866-patched kernel cleanly to {KERNEL} ({len(data)} bytes)")


if __name__ == "__main__":
    main()
