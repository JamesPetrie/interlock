/* VPK180 peer-facing interlock image (topology "ilock"): the workload core (fabric_bridge, 1 ms buckets) sits
 * between cage 3 (port 0, client side) and cage 1 (port 1, server side). No frame generators, no PS frame ports:
 * the PS only brings up the two MACs, releases the core and reports MAC statistics.
 * FCS: the core's reframer emits none, the MRMAC appends it (TX 0xC03); the core's deframer drops the last 4 bytes
 * of every frame, so both RX sides keep the FCS (RX 0x31). With the two cages cabled to each other the core's own
 * sync/cert traffic circulates: expect ~1000 syncs/s (+1 cert/s on cage 3) per TX, RX good == total, no FCS or
 * length errors. */
#include "mrmac_exdes_test_patched.inc"
#include "xil_exception.h"
#include "sleep.h"

#define NPORT 2
static const unsigned long PORT_BASE[2] = { 0xA4090000UL, 0xA4A00000UL };
#define CTL_BASE   0xA4D00000UL
#define CTL_REG    (*(volatile U32 *)(CTL_BASE + 0x0))    /* bit8 pt_xover, bit9 mode_core (ext_sel bits tied in this image) */
#define STATUS_REG (*(volatile U32 *)(CTL_BASE + 0x4))    /* [3:0] led, [7:4] reset_done */
#define ID_REG     (*(volatile U32 *)(CTL_BASE + 0x8))
#define MR(off)    (*(volatile U32 *)(MRMAC_0_BASEADDR + (off)))

static void sync_abort(void *d) { xil_printf("\n\rSYNC ABORT\n\r"); while (1) ; }
static void mac_config(void) {
    *(U32 *)(MRMAC_0_RESET_REG_0) = 0xFFFFFFFF; *(U32 *)(MRMAC_0_MODE_REG_0) = 0x40000A64;
    *(U32 *)(MRMAC_0_CONFIGURATION_RX_REG1_0) = 0x00000031;                  /* rx enable, keep FCS, check SFD/preamble */
    *(U32 *)(MRMAC_0_CONFIGURATION_TX_REG1_0) = 0x00000C03;                  /* tx enable, MAC FCS insertion */
    *(U32 *)(MRMAC_0_FEC_CONFIGURATION_REG1_0) = 0x0000000A; *(U32 *)(MRMAC_0_RESET_REG_0) = 0x00000000;
}
static unsigned mac_rx_status(void) { *(U32 *)(MRMAC_0_STAT_RX_STATUS_REG1_0) = 0xFFFFFFFF; return (*(U32 *)(MRMAC_0_STAT_RX_STATUS_REG1_0)) & 0x7; }
static int wait_aligned(int p, int ms) { mrmac_base = PORT_BASE[p]; for (int t = 0; t < ms; t += 10) { if (mac_rx_status() == 0x7) return 1; usleep(10000); } return 0; }
static void tick(int p) { mrmac_base = PORT_BASE[p]; *(U32 *)(MRMAC_0_TICK_REG_0) = 1; for (int t = 0; t < 100000; t++) if (((*(volatile U32 *)(MRMAC_0_STAT_STATISTICS_READY_0)) & 0x3) == 0x3) break; }
static unsigned st(int p, unsigned off) { mrmac_base = PORT_BASE[p]; return MR(off); }
static void report(const char *what, unsigned window_ms) {
    tick(0); tick(1); usleep(window_ms * 1000); tick(0); tick(1);
    xil_printf("  %s (%u ms) status=0x%02x\n\r", what, window_ms, STATUS_REG & 0xFF);
    for (int p = 0; p < NPORT; p++)
        xil_printf("    cage %s: TX total %u good %u bytes %u bad_fcs %u | RX total %u good %u bytes %u bad_fcs %u inrange_err %u trunc %u framing_err %u toolong %u\n\r",
                   p ? "1 (port 1)" : "3 (port 0)",
                   st(p, 0x818), st(p, 0x820), st(p, 0x828), st(p, 0x8D0),
                   st(p, 0xE30), st(p, 0xE38), st(p, 0xE40), st(p, 0xEE8), st(p, 0xF30), st(p, 0xF38), st(p, 0xCA8), st(p, 0xED8));
}

int main(void)
{
    Xil_ExceptionRegisterHandler(XIL_EXCEPTION_ID_SYNC_INT, sync_abort, NULL);
    xil_printf("\n\r*** VPK180 peer-facing interlock (fabric_bridge, 1 ms buckets): cage 3 = port 0, cage 1 = port 1 ***\n\r");
    xil_printf("control block ID 0x%08x\n\r", ID_REG);
    for (int p = 0; p < NPORT; p++) { mrmac_base = PORT_BASE[p]; mac_config(); }
    CTL_REG = 0x100;                                                         /* core held in reset (bypass mode) during link bring-up */
    *(U32 *)(MRMAC_0_GT_LINERATE_RESET) = 0x4B000F02;
    *(U32 *)(MRMAC_0_GT_LINERATE_RESET) = 0x4B000002;
    sleep(2);
    wait_gt_rxresetdone(2);
    gt_rx_datapath_reset(2);
    int al0 = 0, al1 = 0;
    for (int attempt = 0; attempt < 6 && !(al0 && al1); attempt++) {
        al0 = wait_aligned(0, 2000); al1 = wait_aligned(1, 2000);
        xil_printf("attempt %d: RX aligned P0=%d P1=%d\n\r", attempt, al0, al1);
        if (!(al0 && al1)) gt_rx_datapath_reset(2);
    }
    if (!(al0 && al1)) { xil_printf("RESULT: NO LINK\n\r"); return 0; }
    report("bypass mode, core in reset: nothing should flow", 1000);
    CTL_REG = 0x300;                                                         /* release the core: interlock in the path */
    usleep(20000);
    xil_printf("core released (CTL=0x%03x)\n\r", CTL_REG);
    for (int i = 0; i < 8; i++) report(i == 0 ? "core mode, first second" : "core mode", 1000);
    int ok = 1;
    for (int p = 0; p < NPORT; p++) { tick(p); }
    usleep(1000000);
    for (int p = 0; p < NPORT; p++) { tick(p); unsigned t = st(p, 0xE30), g = st(p, 0xE38); if (t == 0 || t != g || st(p, 0xEE8) || st(p, 0xF30)) ok = 0; }
    xil_printf("RESULT: %s\n\r", ok ? "PASS (both RX sides: good == total, no FCS/length errors)" : "CHECK (see counters)");
    xil_printf("done\n\r");
    return 0;
}
