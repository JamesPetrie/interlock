#!/usr/bin/env python3
"""Derive the two-port MRMAC link-test design from the generated MRMAC example.

  gen_dual_link.py <imports_dir> <out_dir> --quad0 Q --ref0 R --quad1 Q --ref1 R
                   [--p0-src DIR] [--p0-mrmac mrmac_0] [--p0-gtwiz mrmac_0_gtwiz_d1]     [--p0-chan 2] [--stub0 F]
                   [--p1-src DIR] [--p1-mrmac mrmac_1] [--p1-gtwiz mrmac_0_gtwiz_versal] [--p1-chan 0] [--stub1 F]

<imports_dir> is the example project's imports/ (top + CIPS instance + XDC come from there);
--pN-src is the imports/ dir whose mrmac_0_exdes.sv becomes port N's wrapper (default: <imports_dir>),
e.g. port 0 from an example generated on the quad's second GT dual, port 1 from the first-dual one.
Writes into <out_dir>:
  mrmac_p0_exdes.sv, mrmac_p1_exdes.sv  each port's example wrapper bound to its MRMAC / GT-wizard IP
        (--pN-chan 2 additionally renames the wizard's QUAD0_TX/RX0,1 ports to channels 2,3)
  mrmac_dual_top.sv   both wrappers on the example's CIPS block; port 0 on the example's
        masters (MRMAC 0xA4090000, quad 0xA4E00000), port 1 on the spare masters
        M00_AXI_1 (MRMAC 0xA4A00000) and M00_AXI_2 (quad 0xA4B00000); all control GPIOs shared
  dual_link.xdc       the example constraints without its single-instance LOCs, plus
        per-instance quad/refclk placement and the second refclk clock
--stubN: a synthesis stub / template of that port's GT wizard, to check the renamed ports exist.
"""
import argparse, os, re

ap = argparse.ArgumentParser()
ap.add_argument("imports_dir"); ap.add_argument("out_dir")
for n in (0, 1):
    ap.add_argument(f"--p{n}-src", default=None)
    ap.add_argument(f"--p{n}-mrmac", default="mrmac_0" if n == 0 else "mrmac_1")
    ap.add_argument(f"--p{n}-gtwiz", default="mrmac_0_gtwiz_d1" if n == 0 else "mrmac_0_gtwiz_versal")
    ap.add_argument(f"--p{n}-chan", type=int, default=2 if n == 0 else 0, choices=[0, 2])
    ap.add_argument(f"--stub{n}", default=None)
    ap.add_argument(f"--quad{n}", required=True); ap.add_argument(f"--ref{n}", required=True)
a = ap.parse_args()
imp = a.imports_dir
os.makedirs(a.out_dir, exist_ok=True)
def wrapper_src(n):
    d = a.__dict__[f"p{n}_src"] or imp
    return open(os.path.join(d, "mrmac_0_exdes.sv")).read()

def stub_ports(path):
    if not path: return None
    return set(re.findall(r"\bQUAD0_(?:TX|RX)\d_\w+", open(path).read()))

def patch_wrapper(s, n, mrmac, gtwiz, chan, ports):
    s = re.sub(r"^module mrmac_0_exdes\b", f"module mrmac_p{n}_exdes", s, count=1, flags=re.M)
    s, k = re.subn(r"^(\s*)mrmac_0(\s+i_mrmac_0_DUT\b)", lambda m: m.group(1) + mrmac + m.group(2), s, flags=re.M)
    assert k == 1, "MRMAC instance"
    m = re.search(r"^\s*mrmac_0_gtwiz_versal\s+i_mrmac_0_gtwiz_versal\b", s, flags=re.M)
    assert m, "gtwiz instance"
    end = s.index(");", m.end())
    inst = s[m.start():end]
    inst = re.sub(r"mrmac_0_gtwiz_versal(\s+i_mrmac_0_gtwiz_versal)", lambda mm: gtwiz + mm.group(1), inst, count=1)
    if chan:
        inst, k = re.subn(r"\.QUAD0_(TX|RX)([01])_", lambda mm: f".QUAD0_{mm.group(1)}{int(mm.group(2)) + chan}_", inst)
        assert k >= 4, f"expected >=4 QUAD0 channel ports renamed, got {k}"
    if ports is not None:
        used = set(re.findall(r"\.(QUAD0_(?:TX|RX)\d_\w+)\s*\(", inst))
        missing = sorted(used - ports)
        assert not missing, f"port {n}: gtwiz ports not in stub {a.__dict__['stub%d' % n]}: {missing}"
    return s[:m.start()] + inst + s[end:]

for n in (0, 1):
    g = a.__dict__
    txt = patch_wrapper(wrapper_src(n), n, g[f"p{n}_mrmac"], g[f"p{n}_gtwiz"], g[f"p{n}_chan"], stub_ports(g[f"stub{n}"]))
    open(os.path.join(a.out_dir, f"mrmac_p{n}_exdes.sv"), "w").write(txt)

# ----------------------------------------------------------------- top ----
imp_top = open(os.path.join(imp, "mrmac_0_exdes_imp_top.sv")).read()
w_start = imp_top.index("mrmac_0_exdes i_mrmac_0_exdes(")
w_end = imp_top.index(");", w_start) + 2
winst = imp_top[w_start:w_end]
cips_start = imp_top.index("mrmac_0_cips_wrapper i_mrmac_0_cips_wrapper(")
cips_end = imp_top.index("endmodule", cips_start)
cips = imp_top[cips_start:cips_end]
decls = imp_top[imp_top.index("module mrmac_0_exdes_imp_top"):w_start]

AXI = [("awaddr", "[31:0]"), ("awvalid", ""), ("awready", ""), ("wdata", "[31:0]"), ("wvalid", ""), ("wready", ""),
       ("bresp", "[1:0]"), ("bvalid", ""), ("bready", ""), ("araddr", "[31:0]"), ("arvalid", ""), ("arready", ""),
       ("rdata", "[31:0]"), ("rresp", "[1:0]"), ("rvalid", ""), ("rready", "")]
KNOWN = {s for s, _ in AXI}

def port_inst(n):
    t = winst.replace("mrmac_0_exdes i_mrmac_0_exdes(", f"mrmac_p{n}_exdes i_mrmac_p{n}_exdes(", 1)
    def qlite(m):
        sig = m.group(1)
        return f".QUAD0_s_axi_lite_{sig} (q{n}_axi_lite_{sig}" + ("[17:0])" if sig in ("awaddr", "araddr") else ")")
    t, k1 = re.subn(r"\.QUAD0_s_axi_lite_(\w+)\s*\(\s*QUAD0_s_axi_lite_\w+\s*\)", qlite, t)
    t, k2 = re.subn(r"\.s_axi_(\w+)\s*\(\s*s_axi_\w+\s*\)", lambda m: f".s_axi_{m.group(1)} (s{n}_axi_{m.group(1)})", t)
    t, k3 = re.subn(r"\.stat_mst_reset_done\s*\([^)]*\)", f".stat_mst_reset_done (stat_mst_reset_done_{n})", t)
    t, k4 = re.subn(r"\.gt_(rxn_in|rxp_in|txn_out|txp_out|ref_clk_p|ref_clk_n)\s*\(\s*gt_\w+\s*\)",
                    lambda m: f".gt_{m.group(1)} (gt{n}_{m.group(1)})", t)
    assert (k1, k2, k3, k4) == (16, 16, 1, 6), (k1, k2, k3, k4)
    return t

# CIPS: port 0 on the example masters, port 1 on the spare ones
cips, k = re.subn(r"\.M00_AXI_0_(\w+)\s*\(\s*s_axi_(\w+)\s*\)", lambda m: f".M00_AXI_0_{m.group(1)} (s0_axi_{m.group(2)})", cips); assert k == 16, k
cips, k = re.subn(r"\.M00_AXI_8_(\w+)\s*\(\s*QUAD0_s_axi_lite_(\w+)\s*\)", lambda m: f".M00_AXI_8_{m.group(1)} (q0_axi_lite_{m.group(2)})", cips); assert k == 16, k
def spare(prefix, idx):
    global cips
    n = [0]
    def rep(m):
        sig = m.group(1)
        if sig in KNOWN:
            n[0] += 1; return f".M00_AXI_{idx}_{sig} ({prefix}_{sig})"
        return m.group(0)
    cips = re.sub(r"\.M00_AXI_%d_(\w+)\s*\([^)]*\)" % idx, rep, cips)
    assert n[0] == 16, (idx, n[0])
spare("s1_axi", 1); spare("q1_axi_lite", 2)
cips, k = re.subn(r"\.stat_mst_reset_done\s*\(\s*stat_mst_reset_done\s*\)",
                  ".stat_mst_reset_done (stat_mst_reset_done_0 & stat_mst_reset_done_1)", cips); assert k == 1

decls = decls.replace("module mrmac_0_exdes_imp_top", "module mrmac_dual_top", 1)
decls, k = re.subn(r"\(\s*input\s+wire\s+gt_ref_clk_p,.*?output wire \[3:0\] gt_txp_out\s*\);", """(
    input  wire       gt0_ref_clk_p,  input  wire       gt0_ref_clk_n,
    input  wire [3:0] gt0_rxn_in,     input  wire [3:0] gt0_rxp_in,
    output wire [3:0] gt0_txn_out,    output wire [3:0] gt0_txp_out,
    input  wire       gt1_ref_clk_p,  input  wire       gt1_ref_clk_n,
    input  wire [3:0] gt1_rxn_in,     input  wire [3:0] gt1_rxp_in,
    output wire [3:0] gt1_txn_out,    output wire [3:0] gt1_txp_out
);""", decls, count=1, flags=re.S); assert k == 1
extra = ["// ---- per-port AXI (port 0: example masters M00_AXI_0/8, port 1: spare masters M00_AXI_1/2) ----"]
for n in (0, 1):
    for sig, w in AXI:
        extra.append(f"wire {w:7s} s{n}_axi_{sig};")
        extra.append(f"wire {w:7s} q{n}_axi_lite_{sig};")
extra.append("wire [3:0] stat_mst_reset_done_0, stat_mst_reset_done_1;")
top = decls + "\n".join(extra) + "\n\n" + port_inst(0) + "\n\n" + port_inst(1) + "\n\n" + cips + "endmodule\n"
open(os.path.join(a.out_dir, "mrmac_dual_top.sv"), "w").write(top)

# ----------------------------------------------------------------- xdc ----
xdc_dirs = []
for d in (imp, a.p0_src, a.p1_src):
    if d and d not in xdc_dirs: xdc_dirs.append(d)
# dedupe whole constraints (a constraint may span several backslash-continued lines), never single lines
out, seen = [], set()
for d in xdc_dirs:
    lines = open(os.path.join(d, "mrmac_0_example_top.xdc")).read().splitlines()
    i = 0
    while i < len(lines):
        stmt = [lines[i]]
        while stmt[-1].rstrip().endswith("\\") and i + 1 < len(lines):
            i += 1; stmt.append(lines[i])
        i += 1
        stmt = [l.replace("[get_ports gt_ref_clk_p]", "[get_ports gt0_ref_clk_p]").replace("-name gt_ref_clk_p", "-name gt0_ref_clk_p") for l in stmt]
        if re.match(r"^set_property LOC (GTM_QUAD|GTM_REFCLK)", stmt[0]):
            continue
        key = " ".join(l.strip() for l in stmt)
        if key and not key.startswith("#") and key in seen:
            continue
        seen.add(key)
        out.extend(stmt)
        if a.p0_chan == 2 and len(stmt) == 1 and "CH0_TXOUTCLK" in stmt[0] and "set_max_delay" in stmt[0]:   # renamed-port mode only
            out.append(stmt[0].replace("CH0_TXOUTCLK", "CH2_TXOUTCLK"))
out += ["", "# ---- two-port link test: per-instance transceiver placement ----"]
for n, q, r in ((0, a.quad0, a.ref0), (1, a.quad1, a.ref1)):
    out.append(f"set_property LOC {q} [get_cells -hier -filter {{NAME =~ *i_mrmac_p{n}_exdes/*gt_quad_base*/inst/quad_inst}}]")
    out.append(f"set_property LOC {r} [get_cells -hier -filter {{NAME =~ *i_mrmac_p{n}_exdes/*IBUFDS_GTE5_REFCLK0}}]")
out.append("create_clock -period 6.400 -name gt1_ref_clk_p -waveform {0.000 3.200} [get_ports gt1_ref_clk_p]")
open(os.path.join(a.out_dir, "dual_link.xdc"), "w").write("\n".join(out) + "\n")
print("gen_dual_link: wrote", sorted(os.listdir(a.out_dir)))
