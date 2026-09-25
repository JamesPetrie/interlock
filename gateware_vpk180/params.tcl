# ---------------------------------------------------------------------------
# VPK180 interlock — LINK PARAMETERS. Fill in before synthesis.
#
# Everything the port cannot decide on its own lives here: what optics sit in
# the QSFP-DD cages, what the far end runs, which cages/quads are cabled. The
# RTL (hdl/) is rate-agnostic; these values configure the MRMAC + GT and pick
# the AXI-Stream width the shim is built for. See docs/vpk180-port.md §4 for
# how each value is chosen and where it comes from.
#
# Allowed values below are taken from the MRMAC v3.2 IP definition (Vivado
# 2025.2 catalog); the GUI shows the same lists.
# ---------------------------------------------------------------------------

# --- device / board --------------------------------------------------------
set ILOCK_PART        xcvp1802-lsvc4072-2MP-e-S
set ILOCK_BOARD_PART  xilinx.com:vpk180:part0:1.2     ;# 1.0/1.1/1.2 installed on the build machine (no GT interfaces in any)
set ILOCK_BOARD_REPO  ""                              ;# XilinxBoardStore/boards path if not default

# --- interlock core --------------------------------------------------------
set ILOCK_TOP_KIND    0          ;# 0 = recomp_ilock_core (what main builds), 1 = fabric_bridge (prod)
set ILOCK_TIMER_END   7999999    ;# 100 ms buckets at 80 MHz (testing override); prod = 79999
set ILOCK_BKTS_PER_CERT 10       ;# prod = 1000
set CORE_CLK_MHZ      80         ;# TIMER_END above assumes 80 MHz

# --- Ethernet rate / MAC (same for both ports) -----------------------------
# 2026-09-24: the plugged modules are FS QDD-DR8-800G (800G DR8: 8 optical
# lanes of 100G PAM4, host 800GAUI-8). Their lanes only run 100G PAM4, so the
# per-port link is one lane at 106.25 Gb/s: MRMAC 100GAUI-1 with KP4 FEC.
# MRMAC preset. Pick the one matching optics + peer. Common choices:
#   "4x25GE Wide"                          25GbE, 1 NRZ lane per port  (needs NRZ-capable optics)
#   "1x100GE CAUI-4 Wide"                  100GbE, 4 x 25.78G NRZ, KR4 RS-FEC (100G SR4/LR4/CWDM4 QSFP28)
#   "1x100GE 100GAUI-2 (KP4 FEC) Narrow"   100GbE, 2 x 53.125G PAM4       (400G SR8-type optics in 4x100G breakout)
#   "1x100GE 100GAUI-1 Wide"               100GbE, 1 x 106.25G PAM4       (400G DR4/FR4-type optics in 4x100G breakout)
set MRMAC_PRESET      "1x100GE 100GAUI-1 Wide"
set MRMAC_PORT_RATE   100GE                              ;# 10GE | 25GE | 40GE | 50GE | 100GE
set MRMAC_AXIS_IF     "Independent 384b Non-Segmented"   ;# 64b for 10/25GE, 128b for 40/50GE, 384b for 100GE
set MAC_AXIS_W        384                                ;# MUST match MRMAC_AXIS_IF (ilock_pl MAC_AXIS_W)
set MRMAC_FEC         "100G (IEEE P802.3cd/D3.5 CL91) - RS(544 514)"  ;# KP4, mandatory for 100GAUI-1 / 100GBASE-DR; selects MAC+PCS+FEC mode
set GT_SIG_MODE       PAM4                               ;# NRZ (10/25G lanes) | PAM4 (50/100G lanes)
set GT_REFCLK_MHZ     156.25                             ;# VPK180 GTM refclks (U298/U299 RC21008A) default 156.25
set GT_LINE_RATE_GBPS 106.25                             ;# per lane

# --- which cages / quads --------------------------------------------------
# From the UG1582 Transceivers table (lane = connector lane 1..8, ch = GTM
# channel). QSFPDD1/2 interleave their lanes across quads 208-211 (lane 1 on
# 208 ch1, lane 2 on 208 ch3, lane 3 on 209 ch0 ...), which no single-quad MAC
# can use for a 4-lane link, so the interlock uses QSFPDD3 and QSFPDD4:
#   QSFPDD3 lanes 1-4 = quad 111 (GTM_QUAD_X0Y9)  ch0-3, lanes 5-8 = quad 112 (X0Y10)
#   QSFPDD4 lanes 1-4 = quad 117 (GTM_QUAD_X0Y15) ch0-3, lanes 5-8 = quad 118 (X0Y16)
# refclk0 of each quad (site GTM_REFCLK_X0Y<2n>) is driven by an RC21008A
# output: 111 <- GTCLK2_OUT2, 112 <- GTCLK2_OUT3, 117 <- GTCLK1_OUT7,
# 118 <- GTCLK2_OUT7; refclk1 of 111/112 goes to SMAs. Default 156.25 MHz.
# The MRMAC example design only offers the quad next to each MRMAC site
# (MRMAC_X0Y2/3 -> X0Y8, X0Y4/5 -> X0Y14, X0Y6/7 -> X0Y20, X1Y1 -> X1Y4), so
# the design is generated on EXDES_GT_QUAD and the GT is relocated to the
# cage quad afterwards (one clock region away in both cases).
set PORT0_CAGE        QSFPDD3
set PORT0_GT_QUAD     GTM_QUAD_X0Y9        ;# quad 111, QSFPDD3 lanes 1-4
set PORT0_GT_REFCLK   GTM_REFCLK_X0Y18     ;# quad 111 refclk0 <- RC21008A GTCLK2_OUT2
set MRMAC0_LOC        MRMAC_X0Y2           ;# clock region X0Y6; quad X0Y9 is X0Y7
set PORT0_GT_CHAN     2                    ;# channels 2,3 = module lane 3, the proven cage 3 <-> cage 1 pair (channels 0,1 = lane 1 for a cage 4 partner)
set PORT0_MRMAC_LOC   MRMAC_X0Y2
set PORT0_EXDES_GT_QUAD   GTM_QUAD_X0Y8    ;# what the example design generates on
set PORT0_EXDES_GT_REFCLK GTM_REFCLK_X0Y16
set PORT1_CAGE        QSFPDD1
set PORT1_GT_QUAD     GTM_QUAD_X1Y7        ;# quad 209: QSFPDD1 lanes 3,4 (lanes 1,2 sit on quad 208's odd channels, unreachable in half-density mode)
set PORT1_GT_REFCLK   GTM_REFCLK_X1Y14     ;# quad 209 refclk0 <- RC21008A GTCLK1_OUT1 (156.25)
set PORT1_GT_CHAN     0                    ;# channels 0,1 = module lane 3 (half-density lane on the even channel)
set PORT1_MRMAC_LOC   MRMAC_X1Y1           ;# the MRMAC site next to quad X1Y7 (clock region X9Y4 vs X9Y6)
set PORT1_IP_EXDES_GT_QUAD   GTM_QUAD_X1Y4    ;# the only example-design GT site the MRMAC IP accepts for MRMAC_X1Y1 (example XDC only)
set PORT1_IP_EXDES_GT_REFCLK GTM_REFCLK_X1Y8

# ---- in-line test endpoints (traffic generator/monitor MACs): cage 2 "1-4" (lane 1, pairs with cage 3
# lane 1) and cage 4 "5-8" (lane 7, pairs with cage 1 lane 3); Type B cable: lane n <-> lane n
set PORT2_CAGE        QSFPDD2              ;# cage 2 "1-4" port: quads X0Y10 (cage 3 "5-8") and X0Y15 (cage 4 "1-4") are DRC-barred at 106G
set PORT2_LANE        1                    ;# module lane 1 = quad 208 channel 0 (position 1 of the port)
set PORT2_GT_QUAD     GTM_QUAD_X1Y6
set PORT2_GT_REFCLK   GTM_REFCLK_X1Y12     ;# quad 208 refclk0 <- RC21008A GTCLK1_OUT0 (156.25)
set PORT2_GT_CHAN     0
set PORT2_MRMAC_LOC   MRMAC_X1Y0           ;# clock region X9Y1, four regions from quad X1Y6 (X9Y5): the only free MRMAC on that side
set PORT3_CAGE        QSFPDD4
set PORT3_LANE        7                    ;# module lane 7 = quad 118 channel 2 = position 3 of the "5-8" MPO port
set PORT3_GT_QUAD     GTM_QUAD_X0Y16
set PORT3_GT_REFCLK   GTM_REFCLK_X0Y32     ;# quad 118 refclk0 <- RC21008A GTCLK2_OUT7
set PORT3_GT_CHAN     2
set PORT3_MRMAC_LOC   MRMAC_X0Y5           ;# clock region X0Y9, next to quad X0Y16 (X0Y10)
# Which MRMAC data words port 0 of each block drives for MRMAC_AXIS_IF (check
# the generated example design's packet generator; 0 for a 1-port config)
set P0_WORD_BASE      0
set P1_WORD_BASE      0

# --- 400G path (DCMAC), for the FS QDD-DR8-800G modules -------------------
# The DR8s only run 100G PAM4 lanes grouped 4 at a time, so each port is one
# 400GE (4 x 106.25 Gb/s, RS-544 CL119). Only QSFPDD1 and QSFPDD5 give every
# lane its own GTM dual (docs/vpk180-port.md §2 table).
set DCMAC_RATE        400G
set PORTA_CAGE        QSFPDD1
set PORTA_DCMAC_LOC   DCMAC_X1Y1          ;# clock region X9Y6, next to the quads below
set PORTA_GT_QUADS    {GTM_QUAD_X1Y6 GTM_QUAD_X1Y7 GTM_QUAD_X1Y8}   ;# lanes 1,2 on 208 (ch1,ch3), lanes 3,4 on 209 (ch0,ch2); third = the example's leftover 200G port, parked on 210
set PORTA_GT_REFCLKS  {GTM_REFCLK_X1Y12 GTM_REFCLK_X1Y14 GTM_REFCLK_X1Y16} ;# refclk0 of each: RC21008A GTCLK1_OUT0/1/2 (156.25)
set PORTA_LANE_GT_LOCS {X0Y1 X0Y3 X0Y4 X0Y6}         ;# DCMAC LANEn_GT_LOC: channel index across the two quads (0-3 first, 4-7 second)
set PORTB_CAGE        QSFPDD5
set PORTB_DCMAC_LOC   DCMAC_X0Y6          ;# the IP's default site, clock region X0Y13
set PORTB_GT_QUADS    {GTM_QUAD_X0Y19 GTM_QUAD_X0Y20} ;# lanes 1,2 on 121 (ch1,ch2), 3,4 on 122 (ch1,ch2)
set PORTB_GT_REFCLKS  {GTM_REFCLK_X0Y38 GTM_REFCLK_X0Y40} ;# refclk0 of each: 8A34001 Q8
