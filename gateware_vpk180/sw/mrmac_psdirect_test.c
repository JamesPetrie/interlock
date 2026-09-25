/* PS-direct in-line test on the VPK180 (INLINE_TOPO=psdirect):
 *   frame port A (0xA4B00000) sits behind the cage 3 MRMAC (P0, 0xA4090000): its frames go out on
 *     the fibre when CTL ext_sel[0] = 1, and whatever the MAC receives is captured there;
 *   frame port B (0xA4C00000) is the virtual MAC on ilock_pl port 0 (no MRMAC, no fibre);
 *   ilock_pl port 1 is the cage 1 MRMAC (P1, 0xA4A00000), CTL ext_sel[1] = 1.
 *   CTL (0xA4D00000): [1:0] ext_sel, [8] crossover, [9] core mode (recomp_ilock_core vs pass-through).
 * Pass-through, crossover: B -> ilock_pl p0 RX chain -> p1 TX chain -> cage 1 -> fibre -> cage 3 -> A,
 *                          A -> cage 3 -> fibre -> cage 1 -> p1 RX chain -> p0 TX chain -> B.
 * Every frame is compared byte for byte at the far end. Then the core is switched in and the same
 * frames are offered again, just to see whether the core forwards or drops them (no verdict). */
#include "mrmac_exdes_test_patched.inc"
#include "xil_exception.h"
#include "sleep.h"

#define NPORT 2
static const unsigned long PORT_BASE[2] = { 0xA4090000UL, 0xA4A00000UL };
static const char *PORT_NAME[2] = { "P0 cage3.3", "P1 cage1.3" };
#define CTL_BASE 0xA4D00000UL
#define CTL_REG  (*(volatile U32 *)(CTL_BASE + 0x0))
#define STAT_REG (*(volatile U32 *)(CTL_BASE + 0x4))
#define PS_A 0xA4B00000UL
#define PS_B 0xA4C00000UL
#define PS_ID 0x00
#define PS_TX_LEN 0x04
#define PS_TX_COUNT 0x08
#define PS_TX_CTL 0x0C
#define PS_TX_STAT 0x10
#define PS_TX_SENT 0x14
#define PS_RX_CTL 0x20
#define PS_RX_STAT 0x24
#define PS_RX_COUNT 0x28
#define PS_TXBUF 0x1000
#define PS_RXBUF 0x2000
#define PSR(b, o) (*(volatile U32 *)((b) + (o)))

static void sync_abort(void *d) {
    unsigned long long esr, elr, far;
    asm volatile("mrs %0, esr_el3" : "=r"(esr)); asm volatile("mrs %0, elr_el3" : "=r"(elr)); asm volatile("mrs %0, far_el3" : "=r"(far));
    xil_printf("\n\rSYNC ABORT esr=%08x%08x elr=%08x%08x far=%08x%08x\n\r", (unsigned)(esr >> 32), (unsigned)esr,
               (unsigned)(elr >> 32), (unsigned)elr, (unsigned)(far >> 32), (unsigned)far);
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
static unsigned mac_rx_status(void) { *(U32 *)(MRMAC_0_STAT_RX_STATUS_REG1_0) = 0xFFFFFFFF; return (*(U32 *)(MRMAC_0_STAT_RX_STATUS_REG1_0)) & 0x7; }
static int wait_aligned(int p, int ms) {
    mrmac_base = PORT_BASE[p];
    for (int t = 0; t < ms; t += 10) { if (mac_rx_status() == 0x7) return 1; usleep(10000); }
    return 0;
}
static uint64_t rd64(unsigned long lsb, unsigned long msb) { uint32_t l = *(volatile U32 *)lsb, h = *(volatile U32 *)msb; return ((uint64_t)h << 32) | l; }
static void mac_counts(int p, unsigned *tx, unsigned *rxg) {
    mrmac_base = PORT_BASE[p];
    *(U32 *)(MRMAC_0_TICK_REG_0) = 1;
    for (int t = 0; t < 100000; t++) if (((*(volatile U32 *)(MRMAC_0_STAT_STATISTICS_READY_0)) & 0x3) == 0x3) break;
    *tx  = (unsigned)rd64(MRMAC_0_STAT_TX_TOTAL_PACKETS_0_LSB, MRMAC_0_STAT_TX_TOTAL_PACKETS_0_MSB);
    *rxg = (unsigned)rd64(MRMAC_0_STAT_RX_TOTAL_GOOD_PACKETS_0_LSB, MRMAC_0_STAT_RX_TOTAL_GOOD_PACKETS_0_MSB);
}

/* ---- frame ports ---- */
static void ps_load(unsigned long b, const unsigned char *f, unsigned len) {
    for (unsigned i = 0; i < len; i += 4) {
        U32 w = 0;
        for (unsigned k = 0; k < 4 && i + k < len; k++) w |= (U32)f[i + k] << (8 * k);
        PSR(b, PS_TXBUF + i) = w;
    }
}
static int ps_send(unsigned long b, unsigned len, unsigned count) {
    PSR(b, PS_TX_LEN) = len; PSR(b, PS_TX_COUNT) = count; PSR(b, PS_TX_CTL) = 1;
    for (int t = 0; t < 200000; t++) if (!(PSR(b, PS_TX_STAT) & 1)) return 1;
    return 0;
}
static void ps_arm(unsigned long b) { PSR(b, PS_RX_CTL) = 0b011; }   /* clear + arm */
static int ps_captured(unsigned long b, unsigned char *f, unsigned *len, int ms) {
    for (int t = 0; t < ms; t += 5) {
        U32 st = PSR(b, PS_RX_STAT);
        if (st & 1) {
            *len = (st >> 16) & 0x1FFF;
            for (unsigned i = 0; i < *len; i += 4) { U32 w = PSR(b, PS_RXBUF + i); for (unsigned k = 0; k < 4 && i + k < *len; k++) f[i + k] = (unsigned char)(w >> (8 * k)); }
            return (st & 0b10) ? -1 : 1;
        }
        usleep(5000);
    }
    return 0;
}
static void make_frame(unsigned char *f, unsigned len, unsigned seed) {      /* plausible Ethernet: dst, src, type 0x88B5, payload */
    static const unsigned char dst[6] = { 0x02, 0x00, 0x00, 0x00, 0x00, 0x02 }, src[6] = { 0x02, 0x00, 0x00, 0x00, 0x00, 0x01 };
    for (int i = 0; i < 6; i++) { f[i] = dst[i]; f[6 + i] = src[i]; }
    f[12] = 0x88; f[13] = 0xB5;
    unsigned x = seed * 2654435761u + 12345u;
    for (unsigned i = 14; i < len; i++) { x = x * 1103515245u + 12345u; f[i] = (unsigned char)(x >> 16); }
}
static unsigned char txf[4096], rxf[4096];
static int xfer(const char *what, unsigned long from, unsigned long to, unsigned len, unsigned seed) {
    make_frame(txf, len, seed);
    ps_load(from, txf, len);
    ps_arm(to);
    if (!ps_send(from, len, 1)) { xil_printf("  %s %u B: injector stuck busy\n\r", what, len); return 0; }
    unsigned rlen = 0;
    int r = ps_captured(to, rxf, &rlen, 500);
    if (r == 0) { xil_printf("  %s %u B: nothing captured (RX_COUNT %u, drops %u)\n\r", what, len, (unsigned)PSR(to, PS_RX_COUNT), (unsigned)((PSR(to, PS_RX_STAT) >> 2) & 1)); return 0; }
    int same = (rlen == len);
    for (unsigned i = 0; same && i < len; i++) if (rxf[i] != txf[i]) same = 0;
    xil_printf("  %s %u B: captured %u B, %s\n\r", what, len, rlen, same ? "identical" : "DIFFERENT");
    return same;
}

int main(void)
{
    Xil_ExceptionRegisterHandler(XIL_EXCEPTION_ID_SYNC_INT, sync_abort, NULL);
    xil_printf("\n\r*** VPK180 PS-direct in-line test: B -> ilock_pl -> cage 1 == fibre == cage 3 -> A, and back ***\n\r");
    xil_printf("CTL status 0x%08x, frame port A id 0x%08x, B id 0x%08x (expect 0x50534650)\n\r", (unsigned)STAT_REG, (unsigned)PSR(PS_A, PS_ID), (unsigned)PSR(PS_B, PS_ID));
    for (int p = 0; p < NPORT; p++) { mrmac_base = PORT_BASE[p]; mac_config(); }
    CTL_REG = 0x100;                                              /* generators own the MACs, crossover, pass-through */
    xil_printf("GT reset, external mode\n\r");
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
    if (!(al0 && al1)) { xil_printf("RESULT: NO LINK (cage 1 <-> cage 3 fibre needed)\n\r"); return 0; }

    CTL_REG = 0x103;                                              /* A drives cage 3 TX, ilock_pl drives cage 1 TX, crossover, pass-through */
    usleep(50000);
    unsigned tx0, rx0, tx1, rx1;
    mac_counts(0, &tx0, &rx0); mac_counts(1, &tx1, &rx1);          /* clear */
    static const unsigned sizes[] = { 60, 64, 65, 200, 999, 1500, 1514 };
    int pass = 1;
    xil_printf("pass-through, B -> A (through ilock_pl p0 RX, p1 TX, the fibre):\n\r");
    for (unsigned i = 0; i < sizeof sizes / sizeof sizes[0]; i++) pass &= xfer("B->A", PS_B, PS_A, sizes[i], 100 + i);
    xil_printf("pass-through, A -> B (the fibre, ilock_pl p1 RX, p0 TX):\n\r");
    for (unsigned i = 0; i < sizeof sizes / sizeof sizes[0]; i++) pass &= xfer("A->B", PS_A, PS_B, sizes[i], 200 + i);
    mac_counts(0, &tx0, &rx0); mac_counts(1, &tx1, &rx1);
    xil_printf("MAC counters: cage 3 TX %u RX good %u | cage 1 TX %u RX good %u | ilock drop flag %u\n\r", tx0, rx0, tx1, rx1, (unsigned)((STAT_REG >> 3) & 1));
    xil_printf(pass ? "RESULT: PASS-THROUGH PASS\n\r" : "RESULT: PASS-THROUGH FAIL\n\r");

    xil_printf("interlock core mode (recomp_ilock_core, plain frames; informational):\n\r");
    CTL_REG = 0x303;                                              /* + core mode */
    usleep(50000);
    xfer("B->A", PS_B, PS_A, 200, 300);
    xfer("A->B", PS_A, PS_B, 200, 301);
    CTL_REG = 0x103;
    xil_printf("done\n\r");
    return 0;
}
