#!/usr/bin/env python3
"""Patch xnu/out/kernel.development.pipa.nop for Xiaomi Pad 6 (pipa) PM8150B battery telemetry:
  - _sweet_spmi_read (0x527fe0): force sid=2 (PM8150B) and allow FG-gen4 0x40..0x41 + SMB5 0x10..0x13
  - SweetBattery::start (0xae736c): default sweetbattery=1 (overridable via sweetbattery=0)
  - SweetBattery::poll (0xae6f60):
      * 0x4009 (BATT_SOC_FG_MONOTONIC_SOC, 0..255 -> 1..100%)
      * 0x41a0/a1 (BATT_INFO_VBATT, 122070 uV/1000 scaling, valid at 0x8000=4.000V)
      * 0x41a2/a3 (BATT_INFO_IBATT, -488281 nA/1000000 signed mA, valid at 0x8000)
      * 0x1310, 0x110b, 0x1006, 0x1007, 0x100d (PM8150B qpnp-smb5 charger/USB status)
  - SweetBattery::publish (0xae657c): battery design/max capacity 5020 -> 8840 mAh
"""
import os
import re
import shutil
import subprocess
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KERNEL = os.path.join(BASE, "xnu", "out", "kernel.development.pipa.nop")
BAK = KERNEL + ".prebatt"


def asm_at(vma_off: int, insns: list[str]) -> bytes:
    """Assemble AArch64 instructions linked at file offset vma_off using GNU binutils."""
    syms = set()

    def repl(m):
        addr = m.group(1).lower()
        sym = f"_T_{addr}"
        syms.add((sym, addr))
        return sym

    lines = [re.sub(r"(?<![#-])\b(0x[0-9a-fA-F]+)\b", repl, ln) for ln in insns]
    src = ".text\n" + "\n".join(lines) + "\n"
    with tempfile.TemporaryDirectory() as td:
        s_file = os.path.join(td, "p.s")
        o_file = os.path.join(td, "p.o")
        e_file = os.path.join(td, "p.elf")
        b_file = os.path.join(td, "p.bin")
        with open(s_file, "w", newline="\n") as f:
            f.write(src)
        subprocess.check_call(["aarch64-linux-gnu-as", "-o", o_file, s_file])
        ld_cmd = ["aarch64-linux-gnu-ld", "-e", "0", f"-Ttext={hex(vma_off)}"]
        for sym, addr in sorted(syms):
            ld_cmd.append(f"--defsym={sym}={addr}")
        ld_cmd += ["-o", e_file, o_file]
        subprocess.check_call(ld_cmd)
        subprocess.check_call(["aarch64-linux-gnu-objcopy", "-O", "binary", e_file, b_file])
        return open(b_file, "rb").read()


def patch_exact(data: bytearray, off: int, expected: bytes, replacement: bytes, label: str):
    assert len(expected) == len(replacement), (
        f"{label}: length mismatch {len(expected)} != {len(replacement)}"
    )
    actual = bytes(data[off : off + len(expected)])
    assert actual == expected, (
        f"{label} @ {hex(off)} mismatch:\n  expected: {expected.hex()}\n  actual:   {actual.hex()}"
    )
    data[off : off + len(replacement)] = replacement
    print(f"Patched {label} @ {hex(off)} ({len(replacement)} bytes)")


def main():
    assert os.path.exists(KERNEL), f"Missing {KERNEL}"
    if not os.path.exists(BAK):
        shutil.copy2(KERNEL, BAK)
        print(f"Backed up pre-battery kernel -> {BAK}")

    data = bytearray(open(BAK, "rb").read())

    # 1. _sweet_spmi_read @ 0x527fe0: allow sid!=0 (cave forces sid=2)
    patch_exact(
        data,
        0x52800C,
        asm_at(0x52800C, ["cbnz w0, 0x528178"]),
        asm_at(0x52800C, ["nop"]),
        "_sweet_spmi_read allow sid!=0",
    )

    # 2. _sweet_spmi_read peripheral check -> cave in unused SweetBattery::estimatePercent (0xae6e4c)
    exp_spmi_chk = asm_at(
        0x528028,
        [
            "lsr w21, w1, #8",
            "sub w8, w21, #0x10",
            "cmp w8, #4",
            "b.lo 0x528040",
            "cmp w21, #0x48",
            "b.ne 0x52815c",
        ],
    )
    new_spmi_chk = asm_at(
        0x528028,
        ["b 0xae6e4c", "nop", "nop", "nop", "nop", "nop"],
    )
    patch_exact(data, 0x528028, exp_spmi_chk, new_spmi_chk, "_sweet_spmi_read -> estimatePercent cave")

    exp_est_head = asm_at(
        0xAE6E4C,
        ["cmp w0, #0xd49", "b.hs 0xae6e5c", "mov w0, #1", "ret"],
    )
    assert bytes(data[0xAE6E4C : 0xAE6E4C + len(exp_est_head)]) == exp_est_head, "estimatePercent head mismatch"

    spmi_cave = asm_at(
        0xAE6E4C,
        [
            "lsr w21, w1, #8",      # periph = addr >> 8
            "sub w8, w21, #0x10",
            "cmp w8, #4",           # 0x10..0x13 (PM8150B smb5)
            "b.lo 1f",
            "sub w8, w21, #0x40",
            "cmp w8, #2",           # 0x40..0x41 (PM8150B fg-gen4)
            "b.lo 1f",
            "b 0x52815c",
            "1:",
            "orr w21, w21, #0x200", # sid = 2 -> w21 = (2 << 8) | periph
            "b 0x528040",
            # Start default cave @ +0x2c (0xae6e78)
            "mov w8, #1",
            "str w8, [sp, #0xc]",   # default real = 1 (sweetbattery=0 disables)
            "b 0xae739c",
        ],
    )
    data[0xAE6E4C : 0xAE6E4C + len(spmi_cave)] = spmi_cave
    print(f"Wrote SPMI + Start caves @ 0xae6e4c ({len(spmi_cave)} bytes)")

    # 3. SweetBattery::start @ 0xae7398: default real = 1 via cave (at +10 insns = 0xae6e74)
    start_cave = 0xAE6E4C + 10 * 4
    patch_exact(
        data,
        0xAE7398,
        asm_at(0xAE7398, ["str wzr, [sp, #0xc]"]),
        asm_at(0xAE7398, [f"b {hex(start_cave)}"]),
        "SweetBattery::start default sweetbattery=1",
    )

    # 4. SweetBattery::publish: 5020 (0x139c) -> 8840 (0x2288) mAh at all 5 sites
    for reg, off in [("w8", 0xAE65BC), ("w1", 0xAE65F0), ("w0", 0xAE6774), ("w0", 0xAE67D8), ("w0", 0xAE6B04)]:
        patch_exact(
            data,
            off,
            asm_at(off, [f"mov {reg}, #0x139c"]),
            asm_at(off, [f"mov {reg}, #0x2288"]),
            f"SweetBattery::publish capacity {reg} 5020->8840",
        )

    # 5. SweetBattery::poll @ 0xae6f94..0xae6fd0 (15 slots): read MONOTONIC_SOC 0x4009 into [sp, #0x4]
    exp_probe = asm_at(
        0xAE6F94,
        [
            "add x2, sp, #0xf",
            "mov w0, #0",
            "mov w1, #0x4804",
            "bl 0x527fe0",
            "cbnz w0, 0xae70c0",
            "ldrb w8, [sp, #0xf]",
            "cmp w8, #0xd",
            "b.ne 0xae70c0",
            "add x2, sp, #0xe",
            "mov w0, #0",
            "mov w1, #0x4808",
            "bl 0x527fe0",
            "cbnz w0, 0xae70c0",
            "ldrb w8, [sp, #0xe]",
            "tbz w8, #0, 0xae70c0",
        ],
    )
    new_probe = asm_at(
        0xAE6F94,
        [
            "add x2, sp, #0x4",
            "mov w0, #2",
            "mov w1, #0x4009",
            "bl 0x527fe0",
            "cbnz w0, 0xae70c0",
        ]
        + ["nop"] * 10,
    )
    patch_exact(data, 0xAE6F94, exp_probe, new_probe, "SweetBattery::poll FG-gen4 MONOTONIC_SOC 0x4009")

    # 6. VBATT register 0x48c0 -> 0x41a0, remove 0x8000 sentinel, scale 194637 -> 122070
    patch_exact(
        data,
        0xAE6FD8,
        asm_at(0xAE6FD8, ["mov w0, #0x48c0"]),
        asm_at(0xAE6FD8, ["mov w0, #0x41a0"]),
        "SweetBattery::poll VBATT 0x41a0",
    )
    patch_exact(
        data,
        0xAE6FE8,
        asm_at(0xAE6FE8, ["cmp w20, #0x8000", "b.eq 0xae70c0"]),
        asm_at(0xAE6FE8, ["nop", "nop"]),
        "SweetBattery::poll remove VBATT 0x8000 sentinel",
    )
    patch_exact(
        data,
        0xAE7044,
        asm_at(0xAE7044, ["mov w8, #0xf84d", "movk w8, #2, lsl #16"]),
        asm_at(0xAE7044, ["mov w8, #0xdcd6", "movk w8, #1, lsl #16"]),
        "SweetBattery::poll VBATT scale 122070 uV",
    )

    # 7. IBATT @ 0xae7128..0xae71bc (37 slots): read 0x41a2, scale -488281 / 1000000
    exp_ibatt = asm_at(
        0xAE7128,
        [
            "strb wzr, [sp, #0x5]",
            "strh wzr, [sp, #0x2]",
            "add x2, sp, #0x5",
            "mov w0, #0",
            "mov w1, #0x4805",
            "bl 0x527fe0",
            "cbnz w0, 0xae71c0",
            "ldrb w8, [sp, #0x5]",
            "sub w8, w8, #3",
            "cmp w8, #1",
            "b.hi 0xae71c0",
            "add x1, sp, #0x2",
            "mov w0, #0x48c2",
            "bl 0xae6ce8",
            "mov w8, #0",
            "cbz w0, 0xae71e0",
            "ldrh w9, [sp, #0x2]",
            "mov x3, x8",
            "cmp w9, #0x8000",
            "b.eq 0xae71c8",
            "mov w10, #0x4240",
            "movk w10, #0xf, lsl #16",
            "sxth w8, w9",
            "orr w11, w9, #0xffff0000",
            "cmp w8, #0",
            "csel w8, w11, w9, lt",
            "ldrb w9, [sp, #0x5]",
            "cmp w9, #3",
            "mov x9, #-0xa818",
            "movk x9, #0xfffb, lsl #16",
            "mov x11, #-0x540c",
            "movk x11, #0xfffd, lsl #16",
            "csel x9, x11, x9, eq",
            "smull x9, w9, w8",
            "mov w8, #1",
            "sdiv x3, x9, x10",
            "tbnz w23, #0, 0xae71cc",
        ],
    )
    new_ibatt = asm_at(
        0xAE7128,
        [
            "strh wzr, [sp, #0x2]",
            "add x1, sp, #0x2",
            "mov w0, #0x41a2",
            "bl 0xae6ce8",
            "mov w8, #0",
            "cbz w0, 0xae71e0",
            "ldrh w9, [sp, #0x2]",
            "sxth w8, w9",
            "mov w10, #0x4240",
            "movk w10, #0xf, lsl #16",
            "movn x9, #0x7358",
            "movk x9, #0xfff8, lsl #16",
            "smull x9, w9, w8",
            "mov w8, #1",
            "sdiv x3, x9, x10",
            "tbnz w23, #0, 0xae71cc",
            "b 0xae71e8",
        ]
        + ["nop"] * 20,
    )
    patch_exact(data, 0xAE7128, exp_ibatt, new_ibatt, "SweetBattery::poll IBATT 0x41a2 (-488281/1e6)")

    # 8. MONOTONIC_SOC pct calculation @ 0xae71e8..0xae72f0 (66 slots)
    soc_insns = [
        "ldrb w9, [sp, #0x4]",
        "cbnz w9, 1f",
        "mov w1, #1",
        "b 0xae72f0",
        "1:",
        "cmp w9, #255",
        "b.lo 2f",
        "mov w1, #100",
        "b 0xae72f0",
        "2:",
        "sub w9, w9, #1",
        "mov w10, #98",
        "mov w11, #126",
        "madd w9, w9, w10, w11",
        "mov w10, #253",
        "udiv w9, w9, w10",
        "add w1, w9, #1",
        "b 0xae72f0",
    ]
    n_soc_real = sum(1 for s in soc_insns if not s.endswith(":"))
    soc_code = asm_at(0xAE71E8, soc_insns + ["nop"] * (66 - n_soc_real))
    assert len(soc_code) == 66 * 4
    assert bytes(data[0xAE71E8 : 0xAE71F0]) == asm_at(0xAE71E8, ["cmp w20, #0xd49", "b.hs 0xae71f8"])
    assert bytes(data[0xAE72EC : 0xAE72F0]) == asm_at(0xAE72EC, ["add w1, w9, w13"])
    data[0xAE71E8 : 0xAE72F0] = soc_code
    print(f"Patched SweetBattery::poll MONOTONIC_SOC -> % @ 0xae71e8 ({len(soc_code)} bytes)")

    with open(KERNEL, "wb") as f:
        f.write(data)
    print(f"DONE: updated {KERNEL}")


if __name__ == "__main__":
    main()
