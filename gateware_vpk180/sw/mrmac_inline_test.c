/* In-line pass-through test on the VPK180 (four MRMACs, 100GAUI-1, RS-544):
 *   P0 = QSFPDD3 lane 1 (0xA4090000)   pass-through port 0   \  ilock_pl TOP_KIND=2
 *   P1 = QSFPDD1 lane 3 (0xA4A00000)   pass-through port 1   /  (crossover or reflect)
 *   E0 = QSFPDD2 lane 1 (0xA4B00000)   traffic endpoint, fibre to P0
 *   E1 = QSFPDD4 lane 7 (0xA4C00000)   traffic endpoint, fibre to P1
 *   CTL          (0xA4D00000)   passthru_ctl_axil: [3:0] ext_sel, [8] crossover
 * The program infers which fibres are present from RX alignment, then runs: (A) plain link
 * checks on every present fibre, (B) reflect through the shim at P0 / P1 with the far end's
 * generator as source, (C) the in-line crossover E0 -> P0 -> P1 -> E1 and back when both
 * endpoint fibres are present. Built on the example program (mrmac_exdes_test_patched.inc). */
#include "mrmac_exdes_test_patched.inc"
#include "xil_exception.h"
#include "sleep.h"

#ifndef NPORT
#define NPORT 4                  /* 2 = pass-through pair only: never touch the endpoint AXI windows (no slave there) */
#endif
enum { P0 = 0, P1 = 1, E0 = 2, E1 = 3 };
static const unsigned long PORT_BASE[4] = { 0xA4090000UL, 0xA4A00000UL, 0xA4B00000UL, 0xA4C00000UL };
static const char *PORT_NAME[4] = { "P0 cage3.3", "P1 cage1.3", "E0 cage2.1", "E1 cage4.7" };
#define CTL_BASE 0xA4D00000UL
#define CTL_REG  (*(volatile U32 *)(CTL_BASE + 0x0))
#define STAT_REG (*(volatile U32 *)(CTL_BASE + 0x4))
#define ID_REG   (*(volatile U32 *)(CTL_BASE + 0x8))

static void sync_abort(void *d) {
    unsigned long long esr, elr, far;
    asm volatile("mrs %0, esr_el3" : "=r"(esr));
    asm volatile("mrs %0, elr_el3" : "=r"(elr));
    asm volatile("mrs %0, far_el3" : "=r"(far));
    xil_printf("\n\rSYNC ABORT esr=%08x%08x elr=%08x%08x far=%08x%08x\n\r",
        (unsigned)(esr >> 32), (unsigned)esr, (unsigned)(elr >> 32), (unsigned)elr, (unsigned)(far >> 32), (unsigned)far);
    while (1) ;
}
static void mac_config(void) {
    *(U32 *)(MRMAC_0_RESET_REG_0) = 0xFFFFFFFF;
    *(U32 *)(MRMAC_0_MODE_REG_0) = 0x40000A64;
    *(U32 *)(MRMAC_0_CONFIGURATION_RX_REG1_0) = 0x00000033;
    *(U32 *)(MRMAC_0_CONFIGURATION_TX_REG1_0) = 0x00000C03;
    *(U32 *)(MRMAC_0_FEC_CONFIGURATION_REG1_0) = 0x0000000A;
    *(U32 *)(MRMAC_0_RESET_REG_0) = 0x00000000;
}
static unsigned mac_rx_status(void) {
    *(U32 *)(MRMAC_0_STAT_RX_STATUS_REG1_0) = 0xFFFFFFFF;
    return (*(U32 *)(MRMAC_0_STAT_RX_STATUS_REG1_0)) & 0x7;
}
static int wait_aligned(int p, int ms) {
    mrmac_base = PORT_BASE[p];
    for (int t = 0; t < ms; t += 10) { if (mac_rx_status() == 0x7) return 1; usleep(10000); }
    return 0;
}
typedef struct { uint64_t tx_pkts, tx_good_pkts, tx_bytes, tx_good_bytes, rx_pkts, rx_good_pkts, rx_bytes, rx_good_bytes; } stats_t;
static uint64_t rd64(unsigned long lsb, unsigned long msb) {
    uint32_t l = *(volatile U32 *)lsb, h = *(volatile U32 *)msb;
    return ((uint64_t)h << 32) | l;
}
static int mac_stats(stats_t *s) {
    *(U32 *)(MRMAC_0_TICK_REG_0) = 0x00000001;
    int t;
    for (t = 0; t < 100000; t++) if (((*(volatile U32 *)(MRMAC_0_STAT_STATISTICS_READY_0)) & 0x3) == 0x3) break;
    if (t == 100000) return 0;
    s->tx_pkts       = rd64(MRMAC_0_STAT_TX_TOTAL_PACKETS_0_LSB,      MRMAC_0_STAT_TX_TOTAL_PACKETS_0_MSB);
    s->tx_good_pkts  = rd64(MRMAC_0_STAT_TX_TOTAL_GOOD_PACKETS_0_LSB, MRMAC_0_STAT_TX_TOTAL_GOOD_PACKETS_0_MSB);
    s->tx_bytes      = rd64(MRMAC_0_STAT_TX_TOTAL_BYTES_0_LSB,        MRMAC_0_STAT_TX_TOTAL_BYTES_0_MSB);
    s->tx_good_bytes = rd64(MRMAC_0_STAT_TX_TOTAL_GOOD_BYTES_0_LSB,   MRMAC_0_STAT_TX_TOTAL_GOOD_BYTES_0_MSB);
    s->rx_pkts       = rd64(MRMAC_0_STAT_RX_TOTAL_PACKETS_0_LSB,      MRMAC_0_STAT_RX_TOTAL_PACKETS_0_MSB);
    s->rx_good_pkts  = rd64(MRMAC_0_STAT_RX_TOTAL_GOOD_PACKETS_0_LSB, MRMAC_0_STAT_RX_TOTAL_GOOD_PACKETS_0_MSB);
    s->rx_bytes      = rd64(MRMAC_0_STAT_RX_TOTAL_BYTES_0_LSB,        MRMAC_0_STAT_RX_TOTAL_BYTES_0_MSB);
    s->rx_good_bytes = rd64(MRMAC_0_STAT_RX_TOTAL_GOOD_BYTES_0_LSB,   MRMAC_0_STAT_RX_TOTAL_GOOD_BYTES_0_MSB);
    return 1;
}
static void snapshot_all(stats_t s[4]) {
    for (int p = 0; p < NPORT; p++) { mrmac_base = PORT_BASE[p]; if (!mac_stats(&s[p])) xil_printf("  %s: statistics not ready\n\r", PORT_NAME[p]); }
}
static void print_stats(const stats_t s[4]) {
    for (int p = 0; p < NPORT; p++)
        xil_printf("  %s: TX %u pkts (good %u) %u B | RX %u pkts (good %u) %u B (good %u)\n\r", PORT_NAME[p],
            (unsigned)s[p].tx_pkts, (unsigned)s[p].tx_good_pkts, (unsigned)s[p].tx_bytes,
            (unsigned)s[p].rx_pkts, (unsigned)s[p].rx_good_pkts, (unsigned)s[p].rx_bytes, (unsigned)s[p].rx_good_bytes);
}
static void ctl_set(unsigned ext_sel, int xover) { CTL_REG = (xover ? 0x100u : 0u) | (ext_sel & 0xFu); usleep(20000); }
static void measure(stats_t s[4]) {                     /* clear, one generator burst on every port, read */
    snapshot_all(s);
    *(U32 *)(MRMAC_0_GENMON_CTL_BASEADDR_LSB) = 0x00004501;
    *(U32 *)(MRMAC_0_GENMON_CTL_BASEADDR_LSB) = 0x00004500;
    sleep(1);
    snapshot_all(s);
}
static int eq(uint64_t a, uint64_t b) { return a != 0 && a == b; }
static const char *pf(int ok) { return ok ? "PASS" : "FAIL"; }

int main(void)
{
    Xil_ExceptionRegisterHandler(XIL_EXCEPTION_ID_SYNC_INT, sync_abort, NULL);
    xil_printf("\n\r*** VPK180 in-line pass-through test: E0 -> P0 -> [ilock_pl] -> P1 -> E1 ***\n\r");
    xil_printf("CTL id 0x%08x (expect 0x494C4B31), status 0x%08x\n\r", (unsigned)ID_REG, (unsigned)STAT_REG);
    for (int p = 0; p < NPORT; p++) {
        mrmac_base = PORT_BASE[p];
        xil_printf("%s @ 0x%08x: MRMAC revision 0x%08x\n\r", PORT_NAME[p], (unsigned)PORT_BASE[p], (unsigned)*(volatile U32 *)(MRMAC_0_CONFIGURATION_REVISION_REG));
        mac_config();
    }
    ctl_set(0, 1);                                            /* generators own every TX; crossover armed */
    xil_printf("GT reset, external mode, all four quads\n\r");
    *(U32 *)(MRMAC_0_GT_LINERATE_RESET) = 0x4B000F02;
    *(U32 *)(MRMAC_0_GT_LINERATE_RESET) = 0x4B000002;
    sleep(2);
    wait_gt_rxresetdone(2);
    gt_rx_datapath_reset(2);
    int al[4] = { 0, 0, 0, 0 };
    for (int attempt = 0; attempt < 4; attempt++) {
        for (int p = 0; p < NPORT; p++) if (!al[p]) al[p] = wait_aligned(p, 2000);
        xil_printf("attempt %d: RX aligned P0=%d P1=%d E0=%d E1=%d\n\r", attempt, al[P0], al[P1], al[E0], al[E1]);
        if (al[P0] && al[P1] && al[E0] && al[E1]) break;
        gt_rx_datapath_reset(2);
    }
    /* fibres present, inferred from alignment (E0 can only face P0, E1 only P1) */
    int f_e0p0 = NPORT > 2 && al[E0] && al[P0], f_e1p1 = NPORT > 2 && al[E1] && al[P1];
    int f_p0p1 = al[P0] && al[P1] && !f_e0p0 && !f_e1p1;
    xil_printf("fibres: E0-P0=%d  E1-P1=%d  P0-P1=%d\n\r", f_e0p0, f_e1p1, f_p0p1);
    stats_t s[4] = { 0 };
    int all_ok = 1, any = 0;

    /* ---- A: plain links, generator to far-end monitor ---- */
    ctl_set(0, 1);
    measure(s);
    xil_printf("A: link checks (all generators)\n\r"); print_stats(s);
    int pairs[3][2] = { { E0, P0 }, { E1, P1 }, { P0, P1 } };
    int have[3] = { f_e0p0, f_e1p1, f_p0p1 };
    for (int i = 0; i < 3; i++) {
        if (!have[i]) continue;
        int a = pairs[i][0], b = pairs[i][1];
        int ok1 = eq(s[a].tx_pkts, s[b].rx_good_pkts) && eq(s[a].tx_bytes, s[b].rx_good_bytes);
        int ok2 = eq(s[b].tx_pkts, s[a].rx_good_pkts) && eq(s[b].tx_bytes, s[a].rx_good_bytes);
        xil_printf("  %s -> %s: %s   %s -> %s: %s\n\r", PORT_NAME[a], PORT_NAME[b], pf(ok1), PORT_NAME[b], PORT_NAME[a], pf(ok2));
        all_ok = all_ok && ok1 && ok2; any = 1;
    }

    /* ---- B: reflect through the shim at a pass-through port, its fibre peer as source ---- */
    for (int x = P0; x <= P1; x++) {
        int peer = (x == P0) ? (f_e0p0 ? E0 : (f_p0p1 ? P1 : -1)) : (f_e1p1 ? E1 : (f_p0p1 ? P0 : -1));
        if (peer < 0) continue;
        ctl_set(1u << x, 0);
        measure(s);
        int ok = eq(s[peer].tx_pkts, s[x].rx_good_pkts) && eq(s[x].tx_pkts, s[x].rx_good_pkts) && eq(s[peer].rx_good_pkts, s[x].tx_pkts)
              && eq(s[peer].tx_bytes, s[x].rx_good_bytes) && eq(s[x].tx_bytes, s[x].rx_good_bytes) && eq(s[peer].rx_good_bytes, s[x].tx_bytes);
        xil_printf("B: reflect at %s, source %s: sent %u, shim in %u, shim out %u, back %u -> %s  (drop flag %u)\n\r",
            PORT_NAME[x], PORT_NAME[peer], (unsigned)s[peer].tx_pkts, (unsigned)s[x].rx_good_pkts, (unsigned)s[x].tx_pkts,
            (unsigned)s[peer].rx_good_pkts, pf(ok), (unsigned)((STAT_REG >> 3) & 1));
        all_ok = all_ok && ok; any = 1;
    }

    /* ---- C: in-line crossover E0 -> P0 -> P1 -> E1 and E1 -> P1 -> P0 -> E0 ---- */
    if (f_e0p0 && f_e1p1) {
        ctl_set(0x3, 1);
        measure(s);
        xil_printf("C: in-line crossover\n\r"); print_stats(s);
        int ok01 = eq(s[E0].tx_pkts, s[P0].rx_good_pkts) && eq(s[P0].rx_good_pkts, s[P1].tx_pkts) && eq(s[P1].tx_pkts, s[E1].rx_good_pkts)
                && eq(s[E0].tx_bytes, s[E1].rx_good_bytes);
        int ok10 = eq(s[E1].tx_pkts, s[P1].rx_good_pkts) && eq(s[P1].rx_good_pkts, s[P0].tx_pkts) && eq(s[P0].tx_pkts, s[E0].rx_good_pkts)
                && eq(s[E1].tx_bytes, s[E0].rx_good_bytes);
        xil_printf("  E0 -> E1: %s   E1 -> E0: %s   (drop flag %u)\n\r", pf(ok01), pf(ok10), (unsigned)((STAT_REG >> 3) & 1));
        all_ok = all_ok && ok01 && ok10; any = 1;
    } else {
        xil_printf("C: in-line crossover skipped (needs both endpoint fibres)\n\r");
    }
    ctl_set(0, 1);
    xil_printf(any ? (all_ok ? "RESULT: PASS\n\r" : "RESULT: FAIL\n\r") : "RESULT: NO FIBRE ALIGNED\n\r");
    return 0;
}
