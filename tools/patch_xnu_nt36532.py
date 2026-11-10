#!/usr/bin/env python3
"""
Binary-patch xnu/out/kernel.development.pipa -> xnu/out/kernel.development.pipa.nop
to support Xiaomi Pad 6 (pipa) Novatek NT36532 digitizer + 1800x2880 touch coordinates.
"""
import os
import re
import shutil
import subprocess
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_KERNEL = f"{BASE}/xnu/out/kernel.development.pipa"
DST_KERNEL = f"{BASE}/xnu/out/kernel.development.pipa.nop"
BAK_KERNEL = f"{BASE}/xnu/out/kernel.development.pipa.nop.bak_pre532"


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


def patch_block(data, off, expected_bytes, new_bytes, label):
    if expected_bytes is not None:
        cur = bytes(data[off : off + len(expected_bytes)])
        assert cur == expected_bytes, (
            f"Mismatch at {label} ({hex(off)}):\n"
            f"  got: {cur.hex()}\n"
            f"  exp: {expected_bytes.hex()}"
        )
    if expected_bytes is not None:
        assert len(new_bytes) == len(expected_bytes), (
            f"Size mismatch at {label}: {len(new_bytes)} != {len(expected_bytes)}"
        )
    data[off : off + len(new_bytes)] = new_bytes
    print(f"Patched {label} @ {hex(off)} ({len(new_bytes)} bytes)")


def main():
    if os.path.exists(DST_KERNEL) and not os.path.exists(BAK_KERNEL):
        shutil.copy2(DST_KERNEL, BAK_KERNEL)
        print(f"Backed up {DST_KERNEL} -> {BAK_KERNEL}")

    data = bytearray(open(SRC_KERNEL, "rb").read())

    # 1. touch_parse_report (0x524c70..0x524ca8): 16-bit BE 0.1-px -> (0..1799, 0..2879)
    exp_parse = asm_at(
        0x524C70,
        [
            "add x8, x22, x24",
            "ldrb w9, [x8, #1]",
            "ldrb w10, [x8, #3]",
            "lsr w11, w10, #4",
            "orr w9, w11, w9, lsl #4",
            "ldrb w8, [x8, #2]",
            "and w10, w10, #0xf",
            "orr w8, w10, w8, lsl #4",
            "cmp w9, #0x437",
            "mov w10, #0x437",
            "csel w2, w9, w10, cc",
            "cmp w8, #0x95f",
            "mov w9, #0x95f",
            "csel w3, w8, w9, cc",
        ],
    )
    new_parse = asm_at(
        0x524C70,
        [
            "add x8, x22, x24",
            "ldrb w9, [x8, #1]",
            "ldrb w10, [x8, #2]",
            "orr w9, w10, w9, lsl #8",
            "ldrb w10, [x8, #3]",
            "ldrb w11, [x8, #4]",
            "orr w8, w11, w10, lsl #8",
            "mov w10, #10",
            "udiv w2, w9, w10",
            "udiv w3, w8, w10",
            "mov w10, #0x707",
            "cmp w2, w10",
            "csel w2, w2, w10, cc",
            "nop",
        ],
    )
    patch_block(data, 0x524C70, exp_parse, new_parse, "touch_parse_report")

    # 2. SweetHID::touch (0xaedf68..0xaedf84, 0xaee004..0xaee02c, 0xaee0a4..0xaee0ac)
    patch_block(
        data,
        0xAEDF68,
        asm_at(0xAEDF68, ["mov w8, #0x437", "cmp w23, #0x437", "csel w8, w23, w8, lt"]),
        asm_at(0xAEDF68, ["mov w8, #0x707", "cmp w23, #0x707", "csel w8, w23, w8, lt"]),
        "SweetHID::touch clamp X",
    )
    patch_block(
        data,
        0xAEDF78,
        asm_at(0xAEDF78, ["mov w8, #0x95f", "cmp w22, #0x95f", "csel w8, w22, w8, lt"]),
        asm_at(0xAEDF78, ["mov w8, #0xb3f", "cmp w22, #0xb3f", "csel w8, w22, w8, lt"]),
        "SweetHID::touch clamp Y",
    )
    patch_block(
        data,
        0xAEE004,
        asm_at(
            0xAEE004,
            [
                "ldp w9, w10, [x19, #184]",
                "add w9, w9, w23",
                "sbfiz x11, x9, #16, #32",
                "ldrsw x12, [x19, #176]",
                "add w9, w10, w22",
                "sbfiz x9, x9, #16, #32",
                "sdiv x10, x11, x12",
                "str x10, [sp, #88]",
                "ldrb w26, [x20, #200]!",
                "sdiv x25, x9, x8",
            ],
        ),
        asm_at(
            0xAEE004,
            [
                "mov w12, #1800",
                "lsl x11, x23, #16",
                "mov w8, #2880",
                "sdiv x10, x11, x12",
                "lsl x9, x22, #16",
                "nop",
                "nop",
                "str x10, [sp, #88]",
                "ldrb w26, [x20, #200]!",
                "sdiv x25, x9, x8",
            ],
        ),
        "SweetHID::touch scale 1800x2880",
    )
    patch_block(
        data,
        0xAEE0A4,
        asm_at(0xAEE0A4, ["sub w8, w23, #0x408", "sub w9, w22, #0x930"]),
        asm_at(0xAEE0A4, ["sub w8, w23, #0x6d8", "sub w9, w22, #0xb10"]),
        "SweetHID::touch corner box",
    )

    # 3. nvt_probe_id (0x524dbc..0x524f20): NT36532 SWRST=0x1FB43E, RDFAST=0x1FB535, TRIM=0x1FB104
    patch_block(
        data, 0x524DBC, asm_at(0x524DBC, ["mov w8, #0x69fe"]),
        asm_at(0x524DBC, ["mov w8, #0x69be"]), "probe_id swrst reg",
    )
    patch_block(
        data, 0x524DC4, asm_at(0x524DC4, ["mov w20, #0x7ff"]),
        asm_at(0x524DC4, ["mov w20, #0x3fff"]), "probe_id page hi 0x3f",
    )
    patch_block(
        data, 0x524DCC, asm_at(0x524DCC, ["mov w8, #0xe1"]),
        asm_at(0x524DCC, ["mov w8, #0x68"]), "probe_id swrst page mid",
    )
    patch_block(
        data, 0x524E40, asm_at(0x524E40, ["mov w8, #0x90"]),
        asm_at(0x524E40, ["mov w8, #0xb5"]), "probe_id rdfast reg",
    )
    patch_block(
        data, 0x524E4C, asm_at(0x524E4C, ["mov w8, #0xe6"]),
        asm_at(0x524E4C, ["mov w8, #0x6a"]), "probe_id rdfast page mid",
    )
    patch_block(
        data, 0x524EB0, asm_at(0x524EB0, ["mov w8, #0x3ff"]),
        asm_at(0x524EB0, ["mov w8, #0x3fff"]), "probe_id trim page hi",
    )
    patch_block(
        data, 0x524EB8, asm_at(0x524EB8, ["mov w8, #0xec"]),
        asm_at(0x524EB8, ["mov w8, #0x62"]), "probe_id trim page mid",
    )
    patch_block(
        data, 0x524ED8, asm_at(0x524ED8, ["mov w8, #0x4e"]),
        asm_at(0x524ED8, ["mov w8, #0x04"]), "probe_id trim reg",
    )
    patch_block(
        data, 0x524F14, asm_at(0x524F14, ["mov w15, #0x66"]),
        asm_at(0x524F14, ["mov w15, #0x65"]), "probe_id id4 check",
    )
    patch_block(
        data, 0x524F1C, asm_at(0x524F1C, ["mov w15, #0x72"]),
        asm_at(0x524F1C, ["mov w15, #0x32"]), "probe_id id3 check",
    )

    # 4. Partition parser (0x5250f0..0x525114, 0x52524c..0x52525c): dual header banks (0xf0 & 0x1f0)
    exp_part_cnt = asm_at(
        0x5250F0,
        [
            "ldrb w10, [x24, #40]",
            "lsl w11, w10, #27",
            "and w10, w10, w11, asr #31",
            "and w10, w10, #0xf",
            "sub w11, w9, #0x31",
            "lsr w11, w11, #4",
            "cmp w9, #0x30",
            "csinc w11, wzr, w11, ls",
            "add w14, w11, #2",
            "add w9, w14, w10",
        ],
    )
    new_part_cnt = asm_at(
        0x5250F0,
        [
            "ldrb w12, [x24, #32]",
            "tst w12, #2",
            "lsr w11, w9, #1",
            "csel w9, w11, w9, ne",
            "sub w11, w9, #0x21",
            "lsr w11, w11, #4",
            "ubfx w10, w12, #1, #1",
            "add w14, w11, #2",
            "add w9, w14, w10",
            "nop",
        ],
    )
    patch_block(data, 0x5250F0, exp_part_cnt, new_part_cnt, "partition parser header count")

    exp_part_hdr2 = asm_at(
        0x52524C,
        [
            "ldr w17, [x2, #1968]",
            "sub w3, w16, w11",
            "add w17, w17, w3, lsl #4",
            "sub w17, w17, #0x20",
        ],
    )
    new_part_hdr2 = asm_at(
        0x52524C,
        [
            "ldr w17, [x24]",
            "sub w17, w17, #0x10",
            "nop",
            "nop",
        ],
    )
    patch_block(data, 0x52524C, exp_part_hdr2, new_part_hdr2, "partition parser hdr2 slot 0x1f0")

    # 5. nvt_download page constants + reorder CRC before SRAM writes + HW_CRC 0x1FC925=0x56
    for addr, old_i, new_i, lbl in [
        (0x5252DC, "mov w25, #0x7ff", "mov w25, #0x3fff", "dl page hi init"),
        (0x5252E4, "mov w27, #0xe6",  "mov w27, #0x6a",   "dl page mid init"),
        (0x525F18, "mov w25, #0x7ff", "mov w25, #0x3fff", "dl retry page hi"),
        (0x525F1C, "mov w27, #0xe6",  "mov w27, #0x6a",   "dl retry page mid"),
        (0x525308, "mov w8, #0x69fe", "mov w8, #0x69be",  "dl swrst reg"),
        (0x525314, "mov w8, #0xe1",   "mov w8, #0x68",    "dl swrst page mid"),
        (0x525350, "mov w0, #5",      "mov w0, #20",      "dl swrst 20ms sleep"),
        (0x52537C, "mov w8, #0x90",   "b 0x52585c",       "dl jump to CRC+0x1FC925 before SRAM"),
        (0x525404, "b 0x52585c",      "b 0x525cd0",       "dl jump to poll 0x1FC925 after SRAM"),
        (0x525864, "mov w27, #0x7ff", "mov w27, #0x3fff", "crc page hi 0x3f"),
        (0x52586C, "mov w8, #0xe2",   "mov w8, #0x6a",    "crc page mid 0x6a"),
        (0x5258D0, "ubfx w8, w23, #16, #1", "lsr w8, w23, #16", "ilm length bits 16..23"),
        (0x525930, "mov w26, #0xe2",  "mov w26, #0x6a",   "dlm crc page mid 0x6a"),
        (0x525998, "ubfx w8, w23, #16, #1", "lsr w8, w23, #16", "dlm length bits 16..23"),
        (0x525AEC, "mov w2, #2",      "mov w2, #7",       "clear 6 bytes at 0x125860"),
        (0x525B44, "mov w2, #2",      "mov w2, #3",       "write 0xAE 0x00 at 0x125850"),
        (0x525B94, "mov w0, #5",      "mov w0, #50",      "boot_rdy 50ms sleep"),
    ]:
        patch_block(data, addr, asm_at(addr, [old_i]), asm_at(addr, [new_i]), lbl)

    # 6. Block X at 0x5259f0: write 0x56 -> 0x1FC925, restore x19/x26/w28, branch -> 0x5253b8
    block_x = asm_at(
        0x5259F0,
        [
            "mov w8, #0x3fff",
            "strh w8, [sp, #192]",
            "mov w8, #0x92",
            "strb w8, [sp, #194]",
            "stur xzr, [x29, #-176]",
            "add x0, sp, #0xc0",
            "sub x1, x29, #0xb0",
            "mov w2, #3",
            "bl 0x525fe4",
            "mov w8, #0x56a5",
            "strh w8, [sp, #192]",
            "stur xzr, [x29, #-176]",
            "add x0, sp, #0xc0",
            "sub x1, x29, #0xb0",
            "mov w2, #2",
            "bl 0x525fe4",
            "adrp x19, 0x1192000",
            "add x26, x19, #0x798",
            "mov w28, #0xff",
            "b 0x5253b8",
        ],
    )
    assert 0x5259F0 + len(block_x) <= 0x525AA4
    patch_block(data, 0x5259F0, None, block_x, "Block X (write 0x1FC925=0x56 -> SRAM loop)")

    # 7. Reset-fail jump stub at 0x525cac..0x525cc8 + Block Y at 0x525cd0..0x525e9c
    fail_stub = asm_at(
        0x525CAC,
        [
            "mov w27, #0",
            "mov w26, #0",
            "mov w28, #0",
            "stp xzr, xzr, [sp, #128]",
            "str xzr, [sp, #144]",
            "str xzr, [sp, #112]",
            "b 0x525ea0",
        ],
    )
    assert 0x525CAC + len(fail_stub) <= 0x525CD0
    patch_block(data, 0x525CAC, None, fail_stub, "reset-timeout retry stub")

    block_y = asm_at(
        0x525CD0,
        [
            "mov w8, #0x3fff",
            "strh w8, [sp, #192]",
            "mov w8, #0x92",
            "strb w8, [sp, #194]",
            "stur xzr, [x29, #-176]",
            "add x0, sp, #0xc0",
            "sub x1, x29, #0xb0",
            "mov w2, #3",
            "bl 0x525fe4",
            "cbnz w0, 0x525cac",
            "mov w19, #200",
            "1:",
            "stur xzr, [x29, #-176]",
            "mov w8, #0x25",
            "sturb w8, [x29, #-176]",
            "sub x0, x29, #0xb0",
            "add x1, sp, #0xc0",
            "mov w2, #3",
            "bl 0x525fe4",
            "cbnz w0, 0x525cac",
            "ldrb w8, [sp, #194]",
            "cbz w8, 2f",
            "subs w19, w19, #1",
            "b.eq 0x525cac",
            "stur xzr, [x29, #-176]",
            "sub x2, x29, #0xb0",
            "mov w0, #1",
            "mov w1, #0x4240",
            "movk w1, #0xf, lsl #16",
            "bl 0x357868",
            "ldur x2, [x29, #-176]",
            "adrp x0, 0x1192000",
            "add x0, x0, #0x790",
            "mov w1, #0",
            "bl 0x38dfb0",
            "mov x0, #0",
            "bl 0x38e6a4",
            "b 1b",
            "2:",
            "adrp x23, 0x1192000",
            "mov w27, #0x3fff",
            "mov w26, #0x6a",
            "mov w28, #0xff",
            "str xzr, [x29, #-104]",
            "b 0x525aa4",
        ],
    )
    assert 0x525CD0 + len(block_y) <= 0x525EA0, f"Block Y overflow: {hex(0x525CD0 + len(block_y))}"
    patch_block(data, 0x525CD0, None, block_y, "Block Y (poll 0x1FC925==0 -> boot handoff)")

    with open(DST_KERNEL, "wb") as f:
        f.write(data)
    print(f"Wrote patched kernel cleanly to {DST_KERNEL} ({len(data)} bytes)")


if __name__ == "__main__":
    main()
