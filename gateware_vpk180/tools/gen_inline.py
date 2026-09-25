#!/usr/bin/env python3
"""Derive the in-line pass-through design (4 MRMACs) from the generated MRMAC example.

  gen_inline.py <imports_dir> <out_dir>
      --port N:<mrmac>:<gtwiz>:<chan>:<quad>:<refclk>   (four times, N = 0..3)
      [--stub-d0 F] [--stub-d1 F]

Ports 0,1 are the pass-through pair (cage 3, cage 1) wired to ilock_pl (TOP_KIND=2); ports 2,3 are
the traffic endpoints (both MPO ports of the cage 4 module). Every wrapper gets an external
client interface (ext_sel: TX from the external client instead of the example generator; RX
exposed alongside the monitor). Writes mrmac_p{0..3}_exdes.sv, mrmac_inline_top.sv, inline.xdc.
AXI map (example CIPS block): port 0 MRMAC M00_AXI_0 0xA4090000 (+ its quad on M00_AXI_8),
port 1 M00_AXI_1 0xA4A00000, port 2 M00_AXI_2 0xA4B00000, port 3 M00_AXI_3 0xA4C00000,
control register block M00_AXI_4 0xA4D00000. Quads of ports 1-3 are left unconnected.
"""
import argparse, os, re

ap = argparse.ArgumentParser()
ap.add_argument("imports_dir"); ap.add_argument("out_dir")
ap.add_argument("--port", action="append", required=True)
ap.add_argument("--stub-d0", default=None); ap.add_argument("--stub-d1", default=None)
ap.add_argument("--nports", type=int, default=4, choices=[2, 4])
ap.add_argument("--ps-direct", action="store_true",
                help="2-port topology with PS frame ports: A behind port 0's MRMAC (via ext), B attached straight to ilock_pl port 0")
ap.add_argument("--core-kind", type=int, default=2, help="ilock_pl TOP_KIND: 0 recomp core, 1 fabric bridge, 2 pass-through only")
ap.add_argument("--no-gen", action="store_true",
                help="peer-facing design: every MRMAC TX is driven by ilock_pl (ext_sel tied to 1); the example generator/monitor and the PS frame ports are left out")
ap.add_argument("--runtime-bypass", action="store_true", help="ilock_pl RUNTIME_BYPASS (core + pass-through switch, CTL[9] selects)")
ap.add_argument("--timer-end", type=int, default=99_999, help="ilock_pl TIMER_END in core_clk cycles (100 MHz PL clock: 99_999 = 1 ms buckets)")
ap.add_argument("--bkts-per-cert", type=int, default=1000, help="ilock_pl BKTS_PER_CERT (1000 x 1 ms = one certificate per second)")
a = ap.parse_args()
imp = a.imports_dir
os.makedirs(a.out_dir, exist_ok=True)
ports = {}
for spec in a.port:
    n, mrmac, gtwiz, chan, quad, ref = spec.split(":")
    ports[int(n)] = dict(mrmac=mrmac, gtwiz=gtwiz, chan=int(chan), quad=quad, ref=ref)
assert sorted(ports) == list(range(a.nports)), ports
NP = a.nports
PSD = a.ps_direct
assert not PSD or NP == 2, "--ps-direct is a 2-port topology"
W = range(6)
src = open(os.path.join(imp, "mrmac_0_exdes.sv")).read()

def stub_ports(path):
    return None if not path else set(re.findall(r"\bQUAD0_(?:TX|RX)\d_\w+", open(path).read()))

def patch_wrapper(s, n, p, stub):
    s = re.sub(r"^module mrmac_0_exdes\b", f"module mrmac_p{n}_exdes", s, count=1, flags=re.M)
    s, k = re.subn(r"^(\s*)mrmac_0(\s+i_mrmac_0_DUT\b)", lambda m: m.group(1) + p["mrmac"] + m.group(2), s, flags=re.M)
    assert k == 1
    m = re.search(r"^\s*mrmac_0_gtwiz_versal\s+i_mrmac_0_gtwiz_versal\b", s, flags=re.M); assert m
    end = s.index(");", m.end())
    inst = s[m.start():end]
    inst = re.sub(r"mrmac_0_gtwiz_versal(\s+i_mrmac_0_gtwiz_versal)", lambda mm: p["gtwiz"] + mm.group(1), inst, count=1)
    if p["chan"]:
        inst, k = re.subn(r"\.QUAD0_(TX|RX)([01])_", lambda mm: f".QUAD0_{mm.group(1)}{int(mm.group(2)) + p['chan']}_", inst)
        assert k >= 4
    if stub is not None:
        used = set(re.findall(r"\.(QUAD0_(?:TX|RX)\d_\w+)\s*\(", inst))
        assert not (used - stub), f"port {n}: wizard ports missing: {sorted(used - stub)}"
    s = s[:m.start()] + inst + s[end:]
    # ---- external client interface ----
    m = re.search(r"^(\s*input\s+wire\s+pl_resetn)\s*$", s, flags=re.M); assert m
    ext = [m.group(1) + ",",
           "    // ---- external client (pass-through / interlock); ext_sel: 1 = TX from ext_*, 0 = from the example generator ----",
           "    input  wire        ext_sel,",
           "    output wire        ext_axi_clk,",
           "    output wire        ext_axi_rst_n,",
           "    output wire        ext_rx_axis_tvalid,",
           "    output wire        ext_rx_axis_tlast,"]
    ext += [f"    output wire [63:0] ext_rx_axis_tdata{i}," for i in W]
    ext += [f"    output wire [10:0] ext_rx_axis_tkeep_user{i}," for i in W]
    ext += ["    input  wire        ext_tx_axis_tvalid,",
            "    input  wire        ext_tx_axis_tlast,",
            "    output wire        ext_tx_axis_tready,"]
    ext += [f"    input  wire [63:0] ext_tx_axis_tdata{i}," for i in W]
    ext += [f"    input  wire [10:0] ext_tx_axis_tkeep_user{i}" + ("," if i < 5 else "") for i in W]
    s = s[:m.start()] + "\n".join(ext) + s[m.end():]
    for i in W:
        for old, new in [(f".tx_axis_tdata{i} (axis_buffer_tx_tdata[{i}][63:0]),", f".tx_axis_tdata{i} (ext_sel_q ? ext_tx_axis_tdata{i} : axis_buffer_tx_tdata[{i}][63:0]),"),
                         (f".tx_axis_tkeep_user{i} (axis_buffer_tx_tkeep[{i}][10:0]),", f".tx_axis_tkeep_user{i} (ext_sel_q ? ext_tx_axis_tkeep_user{i} : axis_buffer_tx_tkeep[{i}][10:0]),")]:
            assert old in s, old; s = s.replace(old, new, 1)
    for old, new in [(".tx_axis_tlast_0 (axis_buffer_tx_tlast[0]),",  ".tx_axis_tlast_0 (ext_sel_q ? ext_tx_axis_tlast : axis_buffer_tx_tlast[0]),"),
                     (".tx_axis_tvalid_0 (axis_buffer_tx_tvalid[0]),", ".tx_axis_tvalid_0 (ext_sel_q ? ext_tx_axis_tvalid : axis_buffer_tx_tvalid[0]),"),
                     (".tx_axis_tready_0 (tx_axis_tready_0),",         ".tx_axis_tready_0 (tx_axis_tready_0_mac),")]:
        assert old in s, old; s = s.replace(old, new, 1)
    glue = f"""
  // ---- external client glue (mrmac_p{n}_exdes) ----
  wire tx_axis_tready_0_mac;
  (* ASYNC_REG = "TRUE" *) reg [1:0] ext_sel_sync = 2'b00;
  always @(posedge tx_axi_clk_mmcm) ext_sel_sync <= {{ext_sel_sync[0], ext_sel}};
  wire ext_sel_q = ext_sel_sync[1];
  assign tx_axis_tready_0   = tx_axis_tready_0_mac & ~ext_sel_q;   // example generator path
  assign ext_tx_axis_tready = tx_axis_tready_0_mac &  ext_sel_q;   // external client path
  assign ext_axi_clk        = tx_axi_clk_mmcm;
  assign ext_axi_rst_n      = ~tx_axi_rst[0];
  assign ext_rx_axis_tvalid = rx_axis_tvalid_0;
  assign ext_rx_axis_tlast  = rx_axis_tlast_0;
""" + "".join(f"  assign ext_rx_axis_tdata{i} = rx_axis_tdata{i}[63:0];\n  assign ext_rx_axis_tkeep_user{i} = rx_axis_tkeep_user{i}[10:0];\n" for i in W)
    m = re.search(r"^\s*" + re.escape(p["mrmac"]) + r"\s+i_mrmac_0_DUT\b", s, flags=re.M); assert m
    s = s[:m.start()] + glue + s[m.start():]
    for w in ("tx_axi_clk_mmcm", "tx_axi_rst", "rx_axis_tvalid_0", "rx_axis_tlast_0", "rx_axis_tdata5", "rx_axis_tkeep_user5"):
        assert re.search(r"\b" + w + r"\b", s), w
    return s

stubs = {0: stub_ports(a.stub_d0), 2: stub_ports(a.stub_d1)}
for n, p in ports.items():
    open(os.path.join(a.out_dir, f"mrmac_p{n}_exdes.sv"), "w").write(patch_wrapper(src, n, p, stubs[p["chan"]]))

# ----------------------------------------------------------------- top ----
imp_top = open(os.path.join(imp, "mrmac_0_exdes_imp_top.sv")).read()
w_start = imp_top.index("mrmac_0_exdes i_mrmac_0_exdes("); w_end = imp_top.index(");", w_start) + 2
winst = imp_top[w_start:w_end]
cips_start = imp_top.index("mrmac_0_cips_wrapper i_mrmac_0_cips_wrapper("); cips_end = imp_top.index("endmodule", cips_start)
cips = imp_top[cips_start:cips_end]
decls = imp_top[imp_top.index("module mrmac_0_exdes_imp_top"):w_start]
AXI = [("awaddr", "[31:0]"), ("awvalid", ""), ("awready", ""), ("wdata", "[31:0]"), ("wvalid", ""), ("wready", ""),
       ("bresp", "[1:0]"), ("bvalid", ""), ("bready", ""), ("araddr", "[31:0]"), ("arvalid", ""), ("arready", ""),
       ("rdata", "[31:0]"), ("rresp", "[1:0]"), ("rvalid", ""), ("rready", "")]
KNOWN = {s for s, _ in AXI}
QIN = {"araddr", "arvalid", "rready", "awaddr", "awvalid", "wdata", "wvalid", "bready"}

def port_inst(n):
    t = winst.replace("mrmac_0_exdes i_mrmac_0_exdes(", f"mrmac_p{n}_exdes i_mrmac_p{n}_exdes(", 1)
    def qlite(m):
        sig = m.group(1)
        if n == 0:
            return f".QUAD0_s_axi_lite_{sig} (q0_axi_lite_{sig}" + ("[17:0])" if sig in ("awaddr", "araddr") else ")")
        return f".QUAD0_s_axi_lite_{sig} (" + ("'0)" if sig in QIN else ")")
    t, k1 = re.subn(r"\.QUAD0_s_axi_lite_(\w+)\s*\(\s*QUAD0_s_axi_lite_\w+\s*\)", qlite, t)
    t, k2 = re.subn(r"\.s_axi_(\w+)\s*\(\s*s_axi_\w+\s*\)", lambda m: f".s_axi_{m.group(1)} (s{n}_axi_{m.group(1)})", t)
    t, k3 = re.subn(r"\.stat_mst_reset_done\s*\([^)]*\)", f".stat_mst_reset_done (stat_mst_reset_done_{n})", t)
    t, k4 = re.subn(r"\.gt_(rxn_in|rxp_in|txn_out|txp_out|ref_clk_p|ref_clk_n)\s*\(\s*gt_\w+\s*\)",
                    lambda m: f".gt_{m.group(1)} (gt{n}_{m.group(1)})", t)
    assert (k1, k2, k3, k4) == (16, 16, 1, 6), (k1, k2, k3, k4)
    sel = "1'b1" if a.no_gen else f"ext_sel[{n}]"
    ext = [f"    .ext_sel ({sel}),", f"    .ext_axi_clk (p{n}_axi_clk),", f"    .ext_axi_rst_n (p{n}_axi_rst_n),",
           f"    .ext_rx_axis_tvalid (p{n}_rx_tvalid),", f"    .ext_rx_axis_tlast (p{n}_rx_tlast),",
           f"    .ext_tx_axis_tvalid (p{n}_tx_tvalid),", f"    .ext_tx_axis_tlast (p{n}_tx_tlast),", f"    .ext_tx_axis_tready (p{n}_tx_tready),"]
    ext += [f"    .ext_rx_axis_tdata{i} (p{n}_rx_tdata{i})," for i in W] + [f"    .ext_rx_axis_tkeep_user{i} (p{n}_rx_tkeep_user{i})," for i in W]
    ext += [f"    .ext_tx_axis_tdata{i} (p{n}_tx_tdata{i})," for i in W] + [f"    .ext_tx_axis_tkeep_user{i} (p{n}_tx_tkeep_user{i})" + ("," if i < 5 else "") for i in W]
    body = t[:t.rindex(");")].rstrip()
    # the example's last port line ends in a // comment: the comma must go after the ')' and before it
    body, k = re.subn(r"\)\s*,?(\s*//[^\n]*)?$", lambda m: ")," + (m.group(1) or ""), body, count=1)
    assert k == 1, "could not close the example port list"
    t = body + "\n" + "\n".join(ext) + "\n);\n"
    return t

cips, k = re.subn(r"\.M00_AXI_0_(\w+)\s*\(\s*s_axi_(\w+)\s*\)", lambda m: f".M00_AXI_0_{m.group(1)} (s0_axi_{m.group(2)})", cips); assert k == 16
cips, k = re.subn(r"\.M00_AXI_8_(\w+)\s*\(\s*QUAD0_s_axi_lite_(\w+)\s*\)", lambda m: f".M00_AXI_8_{m.group(1)} (q0_axi_lite_{m.group(2)})", cips); assert k == 16
def spare(prefix, idx, extra=()):
    global cips
    n = [0]
    def rep(m):
        sig = m.group(1)
        if sig in KNOWN or sig in extra:
            n[0] += 1; return f".M00_AXI_{idx}_{sig} ({prefix}_{sig})"
        return m.group(0)
    cips = re.sub(r"\.M00_AXI_%d_(\w+)\s*\([^)]*\)" % idx, rep, cips)
    assert n[0] == 16 + len(extra), (idx, n[0])
spare("s1_axi", 1)
if NP == 4:
    spare("s2_axi", 2); spare("s3_axi", 3)
elif PSD:
    spare("psa_axi", 2, extra=("wstrb",)); spare("psb_axi", 3, extra=("wstrb",))
spare("ctl_axi", 4, extra=("wstrb",))
cips, k = re.subn(r"\.stat_mst_reset_done\s*\(\s*stat_mst_reset_done\s*\)", ".stat_mst_reset_done (mst_reset_done_all)", cips); assert k == 1

decls = decls.replace("module mrmac_0_exdes_imp_top", "module mrmac_inline_top", 1)
gt_ports = "\n".join(f"    input  wire       gt{n}_ref_clk_p,  input  wire       gt{n}_ref_clk_n,\n"
                     f"    input  wire [3:0] gt{n}_rxn_in,     input  wire [3:0] gt{n}_rxp_in,\n"
                     f"    output wire [3:0] gt{n}_txn_out,    output wire [3:0] gt{n}_txp_out" + ("," if n < NP - 1 else "") for n in range(NP))
decls, k = re.subn(r"\(\s*input\s+wire\s+gt_ref_clk_p,.*?output wire \[3:0\] gt_txp_out\s*\);", "(\n" + gt_ports + "\n);", decls, count=1, flags=re.S); assert k == 1
extra = ["// ---- per-port AXI (port 0: example masters M00_AXI_0/8; ports 1-3: spare masters M00_AXI_1..3; control: M00_AXI_4) ----"]
for n in range(NP):
    for sig, w in AXI:
        extra.append(f"wire {w:7s} s{n}_axi_{sig};")
for sig, w in AXI:
    extra.append(f"wire {w:7s} q0_axi_lite_{sig};")
    extra.append(f"wire {w:7s} ctl_axi_{sig};")
extra.append("wire [3:0]   ctl_axi_wstrb;")
if PSD:
    for pre in ("psa_axi", "psb_axi"):
        for sig, w in AXI:
            extra.append(f"wire {w:7s} {pre}_{sig};")
        extra.append(f"wire [3:0]   {pre}_wstrb;")
    extra.append("// virtual MAC for ilock_pl port 0: the PS frame port B, clocked from the PL clock")
    extra.append("wire v0_rx_tvalid, v0_rx_tlast, v0_tx_tvalid, v0_tx_tlast, v0_tx_tready;")
    extra += [f"wire [63:0] v0_rx_tdata{i}, v0_tx_tdata{i};" for i in W] + [f"wire [10:0] v0_rx_tkeep_user{i}, v0_tx_tkeep_user{i};" for i in W]
extra.append("wire [3:0] " + ", ".join(f"stat_mst_reset_done_{n}" for n in range(NP)) + ";")
extra.append("wire [3:0] mst_reset_done_all = " + " & ".join(f"stat_mst_reset_done_{n}" for n in range(NP)) + ";")
extra.append(f"wire [{NP-1}:0] ext_sel;\nwire       pt_xover, mode_core;\nwire [3:0] ilock_led;")
for n in range(NP):
    extra.append(f"wire p{n}_axi_clk, p{n}_axi_rst_n, p{n}_rx_tvalid, p{n}_rx_tlast, p{n}_tx_tvalid, p{n}_tx_tlast, p{n}_tx_tready;")
    extra += [f"wire [63:0] p{n}_rx_tdata{i}, p{n}_tx_tdata{i};" for i in W] + [f"wire [10:0] p{n}_rx_tkeep_user{i}, p{n}_tx_tkeep_user{i};" for i in W]
# endpoints 2,3: no external client (their TX stays with the example generator unless software says otherwise)
for n in range(2, NP):
    extra.append(f"assign p{n}_tx_tvalid = 1'b0; assign p{n}_tx_tlast = 1'b0;")
    extra += [f"assign p{n}_tx_tdata{i} = 64'h0; assign p{n}_tx_tkeep_user{i} = 11'h0;" for i in W]

ilock = [f"// ---- ilock_pl between ports 0 and 1: TOP_KIND={a.core_kind}, RUNTIME_BYPASS={int(a.runtime_bypass)} (CTL[9] = core mode), crossover/reflect = CTL[8] ----",
         "// core_clk = the 100 MHz PL clock: TIMER_END and BKTS_PER_CERT set the bucket and certificate cadence",
         f"ilock_pl #(.MAC_AXIS_W(384), .TOP_KIND({a.core_kind}), .RUNTIME_BYPASS(1'b{int(a.runtime_bypass)}), .TIMER_END({a.timer_end}), .BKTS_PER_CERT({a.bkts_per_cert})) i_ilock_pl (",
         "    .core_clk(pl0_ref_clk_0), .core_rst_n(pl0_resetn_0), .pt_xover(pt_xover), .mode_core(mode_core),"]
for n in (0, 1):
    src = "v0" if (PSD and n == 0) else f"p{n}"
    clk, rst = ("pl0_ref_clk_0", "pl0_resetn_0") if (PSD and n == 0) else (f"p{n}_axi_clk", f"p{n}_axi_rst_n")
    ilock += [f"    .p{n}_rx_axi_clk({clk}), .p{n}_rx_rst_n({rst}), .p{n}_tx_axi_clk({clk}), .p{n}_tx_rst_n({rst}),",
              f"    .p{n}_rx_axis_tvalid({src}_rx_tvalid), .p{n}_rx_axis_tlast({src}_rx_tlast),"]
    ilock += [f"    .p{n}_rx_axis_tdata{i}({src}_rx_tdata{i}), .p{n}_rx_axis_tkeep_user{i}({src}_rx_tkeep_user{i})," for i in W]
    ilock += [f"    .p{n}_tx_axis_tvalid({src}_tx_tvalid), .p{n}_tx_axis_tready({src}_tx_tready), .p{n}_tx_axis_tlast({src}_tx_tlast),"]
    ilock += [f"    .p{n}_tx_axis_tdata{i}({src}_tx_tdata{i}), .p{n}_tx_axis_tkeep_user{i}({src}_tx_tkeep_user{i})," for i in W]
ilock += ["    .led(ilock_led)", ");"]
if PSD:
    def ps_inst(name, pre, aclk, arst, mclk, mrst, inp, outp, out_tready, in_tready):
        cat = lambda p, sig: "{" + ", ".join(f"{p}_{sig}{i}" for i in reversed(range(6))) + "}"
        return [f"ps_frame_port #(.W(384)) {name} (",
                f"    .aclk({aclk}), .aresetn({arst}),",
                f"    .s_axi_awaddr({pre}_awaddr), .s_axi_awvalid({pre}_awvalid), .s_axi_awready({pre}_awready),",
                f"    .s_axi_wdata({pre}_wdata), .s_axi_wstrb({pre}_wstrb), .s_axi_wvalid({pre}_wvalid), .s_axi_wready({pre}_wready),",
                f"    .s_axi_bresp({pre}_bresp), .s_axi_bvalid({pre}_bvalid), .s_axi_bready({pre}_bready),",
                f"    .s_axi_araddr({pre}_araddr), .s_axi_arvalid({pre}_arvalid), .s_axi_arready({pre}_arready),",
                f"    .s_axi_rdata({pre}_rdata), .s_axi_rresp({pre}_rresp), .s_axi_rvalid({pre}_rvalid), .s_axi_rready({pre}_rready),",
                f"    .mac_clk({mclk}), .mac_rst_n({mrst}),",
                f"    .in_tvalid({inp}_tvalid), .in_tlast({inp}_tlast), .in_tdata({cat(inp, 'tdata')}), .in_tkeep_user({cat(inp, 'tkeep_user')}), .in_tready({in_tready}),",
                f"    .out_tvalid({outp}_tvalid), .out_tready({out_tready}), .out_tlast({outp}_tlast), .out_tdata({cat(outp, 'tdata')}), .out_tkeep_user({cat(outp, 'tkeep_user')})",
                ");"]
    ilock += ["// ---- PS frame port A: behind port 0's MRMAC (ext_sel[0] = 1 puts its frames on the MAC TX; MAC RX is captured) ----"]
    ilock += ps_inst("i_ps_a", "psa_axi", "pl0_ref_clk_0", "pl0_resetn_0", "p0_axi_clk", "p0_axi_rst_n", "p0_rx", "p0_tx", "p0_tx_tready", "")
    ilock += ["// ---- PS frame port B: the virtual MAC on ilock_pl port 0 (injects into p0 RX, captures p0 TX) ----"]
    ilock += ps_inst("i_ps_b", "psb_axi", "pl0_ref_clk_0", "pl0_resetn_0", "pl0_ref_clk_0", "pl0_resetn_0", "v0_tx", "v0_rx", "1'b1", "v0_tx_tready")
ilock += [
          f"passthru_ctl_axil #(.NPORT({NP}), .ID(32'h{0x494C4B50 if a.no_gen else 0x494C4B31:08X})) i_ctl (",   # ID: ILKP peer-facing, ILK1 otherwise
          "    .aclk(pl0_ref_clk_0), .aresetn(pl0_resetn_0),",
          "    .s_axi_awaddr(ctl_axi_awaddr), .s_axi_awvalid(ctl_axi_awvalid), .s_axi_awready(ctl_axi_awready),",
          "    .s_axi_wdata(ctl_axi_wdata), .s_axi_wstrb(ctl_axi_wstrb), .s_axi_wvalid(ctl_axi_wvalid), .s_axi_wready(ctl_axi_wready),",
          "    .s_axi_bresp(ctl_axi_bresp), .s_axi_bvalid(ctl_axi_bvalid), .s_axi_bready(ctl_axi_bready),",
          "    .s_axi_araddr(ctl_axi_araddr), .s_axi_arvalid(ctl_axi_arvalid), .s_axi_arready(ctl_axi_arready),",
          "    .s_axi_rdata(ctl_axi_rdata), .s_axi_rresp(ctl_axi_rresp), .s_axi_rvalid(ctl_axi_rvalid), .s_axi_rready(ctl_axi_rready),",
          "    .ext_sel(ext_sel), .pt_xover(pt_xover), .mode_core(mode_core), .led(ilock_led), .mst_reset_done(mst_reset_done_all)", ");"]
top = decls + "\n".join(extra) + "\n\n" + "\n\n".join(port_inst(n) for n in range(NP)) + "\n\n" + cips + "\n".join(ilock) + "\nendmodule\n"
open(os.path.join(a.out_dir, "mrmac_inline_top.sv"), "w").write(top)

# ----------------------------------------------------------------- xdc ----
lines = open(os.path.join(imp, "mrmac_0_example_top.xdc")).read().splitlines()
out, i = [], 0
while i < len(lines):
    stmt = [lines[i]]
    while stmt[-1].rstrip().endswith("\\") and i + 1 < len(lines):
        i += 1; stmt.append(lines[i])
    i += 1
    stmt = [l.replace("[get_ports gt_ref_clk_p]", "[get_ports gt0_ref_clk_p]").replace("-name gt_ref_clk_p", "-name gt0_ref_clk_p") for l in stmt]
    if re.match(r"^set_property LOC (GTM_QUAD|GTM_REFCLK)", stmt[0]):
        continue
    out.extend(stmt)
    if len(stmt) == 1 and "CH0_TXOUTCLK" in stmt[0] and "set_max_delay" in stmt[0]:
        out.append(stmt[0].replace("CH0_TXOUTCLK", "CH2_TXOUTCLK"))
# placement goes to a separate, implementation-only XDC (synthesis does not need it, and a LOC on the
# refclk buffer was the one difference between a clean synthesis and a Vivado 2025.2 synthesis crash)
loc = ["# ---- in-line pass-through test: per-instance transceiver placement (implementation only) ----"]
for n, p in ports.items():
    loc.append(f"set_property LOC {p['quad']} [get_cells -hier -filter {{NAME =~ *i_mrmac_p{n}_exdes/*gt_quad_base*/inst/quad_inst}}]")
    loc.append(f"set_property LOC {p['ref']} [get_cells -hier -filter {{NAME =~ *i_mrmac_p{n}_exdes/*IBUFDS_GTE5_REFCLK0}}]")
open(os.path.join(a.out_dir, "inline_loc.xdc"), "w").write("\n".join(loc) + "\n")
for n in range(1, NP):
    out.append(f"create_clock -period 6.400 -name gt{n}_ref_clk_p -waveform {{0.000 3.200}} [get_ports gt{n}_ref_clk_p]")
out += ["# GTM crosstalk DRC (AR 35326): the eval board's cage channels are rated for 112G/lane and the active",
        "# lanes are channel 2 of each quad, not the quad-boundary neighbours; keep it as a warning",
        "set_property SEVERITY {Warning} [get_drc_checks GTMXTLK-2]",
        "# quasi-static controls and the sticky-drop flag cross clock domains through 2-FF synchronisers",
        "set_false_path -to [get_pins -hierarchical -filter {NAME =~ *ext_sel_sync_reg[0]/D}]",
        "set_false_path -to [get_pins -hierarchical -filter {NAME =~ *xo_sync_reg[0]/D}]",
        "set_false_path -to [get_pins -hierarchical -filter {NAME =~ *mode_sync_reg[0]/D}]",
        "set_false_path -to [get_pins -hierarchical -filter {NAME =~ *clr_sync_reg[0]/D}]",
        "set_false_path -to [get_pins -hierarchical -filter {NAME =~ *drop_sync_reg[0]/D}]"]
open(os.path.join(a.out_dir, "inline.xdc"), "w").write("\n".join(out) + "\n")
print("gen_inline: wrote", sorted(f for f in os.listdir(a.out_dir) if not f.startswith("mrmac_p") or int(f[7]) < NP))
