#!/usr/bin/env python3
"""Prepare a multi-MRMAC PS program: <example mrmac_exdes_test.c> <out_dir> [<app.c in sw/>]
Writes mrmac_exdes_test_patched.inc (the example with MRMAC_0_BASEADDR made a variable, the
GPIO base macros fixed, main renamed) and copies sw/mrmac_dual_test.c next to it."""
import os, re, shutil, sys
src, out = sys.argv[1], sys.argv[2]
os.makedirs(out, exist_ok=True)
s = open(src).read()
s, k = re.subn(r"#define\s+MRMAC_0_BASEADDR\s+0xA4090000",
               "static unsigned long mrmac_base = 0xA4090000UL;   /* switched per port by mrmac_dual_test.c */\n#define MRMAC_0_BASEADDR mrmac_base", s)
assert k == 1, "MRMAC_0_BASEADDR define"
s, k = re.subn(r"(#define\s+MRMAC_0_(?:GENMON_CTL|COMMON_CTL_STAT|GPIO_DEBUG)_BASEADDR\s+XPAR_\w+?)_DEVICE_ID\b", r"\1_BASEADDR", s)
assert k == 3, ("GPIO base macros", k)
s, k = re.subn(r"^int\s+main\s*\(\s*(void)?\s*\)", "int example_main(void)", s, flags=re.M)
assert k == 1, "main"
open(os.path.join(out, "mrmac_exdes_test_patched.inc"), "w").write(s)
app = sys.argv[3] if len(sys.argv) > 3 else "mrmac_dual_test.c"
shutil.copy(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "sw", app), out)
print("gen_dual_sw: wrote", sorted(os.listdir(out)))
