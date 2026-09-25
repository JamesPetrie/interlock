/* How fast can the A72 drive the frame ports over AXI-Lite? Measures single writes/reads, a full
 * 1518-byte frame load, and the "patch header + start + poll busy" per-frame loop through the
 * pass-through (B -> ilock_pl -> cage 1 -> fibre -> cage 3), counted by the MRMAC statistics. */
#include "mrmac_exdes_test_patched.inc"
#include "xil_exception.h"
#include "sleep.h"
#include "xtime_l.h"

#define NPORT 2
static const unsigned long PORT_BASE[2] = { 0xA4090000UL, 0xA4A00000UL };
#define CTL_BASE 0xA4D00000UL
#define CTL_REG  (*(volatile U32 *)(CTL_BASE + 0x0))
#define PS_B 0xA4C00000UL
#define PS_TX_LEN 0x04
#define PS_TX_COUNT 0x08
#define PS_TX_CTL 0x0C
#define PS_TX_STAT 0x10
#define PS_TXBUF 0x1000
#define PSR(b, o) (*(volatile U32 *)((b) + (o)))

static void sync_abort(void *d) { xil_printf("\n\rSYNC ABORT\n\r"); while (1) ; }
static void mac_config(void) {
    *(U32 *)(MRMAC_0_RESET_REG_0) = 0xFFFFFFFF; *(U32 *)(MRMAC_0_MODE_REG_0) = 0x40000A64;
    *(U32 *)(MRMAC_0_CONFIGURATION_RX_REG1_0) = 0x00000033; *(U32 *)(MRMAC_0_CONFIGURATION_TX_REG1_0) = 0x00000C03;
    *(U32 *)(MRMAC_0_FEC_CONFIGURATION_REG1_0) = 0x0000000A; *(U32 *)(MRMAC_0_RESET_REG_0) = 0x00000000;
}
static unsigned mac_rx_status(void) { *(U32 *)(MRMAC_0_STAT_RX_STATUS_REG1_0) = 0xFFFFFFFF; return (*(U32 *)(MRMAC_0_STAT_RX_STATUS_REG1_0)) & 0x7; }
static int wait_aligned(int p, int ms) { mrmac_base = PORT_BASE[p]; for (int t = 0; t < ms; t += 10) { if (mac_rx_status() == 0x7) return 1; usleep(10000); } return 0; }
static unsigned long long now_us(void) { XTime t; XTime_GetTime(&t); return (unsigned long long)t * 1000000ull / COUNTS_PER_SECOND; }
static uint64_t rd64(unsigned long lsb, unsigned long msb) { uint32_t l = *(volatile U32 *)lsb, h = *(volatile U32 *)msb; return ((uint64_t)h << 32) | l; }
static void mac_counts(int p, unsigned *tx, unsigned *rxg, unsigned *txb) {
    mrmac_base = PORT_BASE[p];
    *(U32 *)(MRMAC_0_TICK_REG_0) = 1;
    for (int t = 0; t < 100000; t++) if (((*(volatile U32 *)(MRMAC_0_STAT_STATISTICS_READY_0)) & 0x3) == 0x3) break;
    *tx  = (unsigned)rd64(MRMAC_0_STAT_TX_TOTAL_PACKETS_0_LSB, MRMAC_0_STAT_TX_TOTAL_PACKETS_0_MSB);
    *rxg = (unsigned)rd64(MRMAC_0_STAT_RX_TOTAL_GOOD_PACKETS_0_LSB, MRMAC_0_STAT_RX_TOTAL_GOOD_PACKETS_0_MSB);
    *txb = (unsigned)rd64(MRMAC_0_STAT_TX_TOTAL_BYTES_0_LSB, MRMAC_0_STAT_TX_TOTAL_BYTES_0_MSB);
}
static unsigned char txf[2048];
static unsigned make_frame(unsigned len) {                                   /* plain frame, LENGTH field = len - 14 */
    for (unsigned i = 0; i < len; i++) txf[i] = (unsigned char)(i * 7 + 3);
    txf[0] = 0x02; txf[6] = 0x02; txf[12] = (len - 14) >> 8; txf[13] = (len - 14) & 0xFF;
    return len;
}
static void ps_load(const unsigned char *f, unsigned len) {
    for (unsigned i = 0; i < len; i += 4) { U32 w = 0; for (unsigned k = 0; k < 4 && i + k < len; k++) w |= (U32)f[i + k] << (8 * k); PSR(PS_B, PS_TXBUF + i) = w; }
}

int main(void)
{
    Xil_ExceptionRegisterHandler(XIL_EXCEPTION_ID_SYNC_INT, sync_abort, NULL);
    xil_printf("\n\r*** A72 -> frame port AXI-Lite timing ***\n\r");
    unsigned long long t0, t1;
    const unsigned N = 10000;
    t0 = now_us(); for (unsigned i = 0; i < N; i++) PSR(PS_B, PS_TXBUF + ((i * 4) & 0xFFC)) = i; t1 = now_us();
    xil_printf("  %u 32-bit writes: %u us -> %u ns per write\n\r", N, (unsigned)(t1 - t0), (unsigned)((t1 - t0) * 1000 / N));
    volatile U32 sink = 0;
    t0 = now_us(); for (unsigned i = 0; i < N; i++) sink += PSR(PS_B, PS_TX_STAT); t1 = now_us();
    xil_printf("  %u 32-bit reads : %u us -> %u ns per read\n\r", N, (unsigned)(t1 - t0), (unsigned)((t1 - t0) * 1000 / N));
    unsigned n = make_frame(1518);
    t0 = now_us(); ps_load(txf, n); t1 = now_us();
    xil_printf("  full 1518-byte frame load: %u us\n\r", (unsigned)(t1 - t0));

    for (int p = 0; p < NPORT; p++) { mrmac_base = PORT_BASE[p]; mac_config(); }
    CTL_REG = 0x100;
    *(U32 *)(MRMAC_0_GT_LINERATE_RESET) = 0x4B000F02; *(U32 *)(MRMAC_0_GT_LINERATE_RESET) = 0x4B000002;
    sleep(2); wait_gt_rxresetdone(2); gt_rx_datapath_reset(2);
    int al0 = 0, al1 = 0;
    for (int a = 0; a < 6 && !(al0 && al1); a++) { al0 = wait_aligned(0, 2000); al1 = wait_aligned(1, 2000); if (!(al0 && al1)) gt_rx_datapath_reset(2); }
    xil_printf("  link: P0=%d P1=%d\n\r", al0, al1);
    CTL_REG = 0x103; usleep(20000);                                          /* pass-through, B -> cage 1 -> fibre -> cage 3 */
    static const unsigned sizes[] = { 1518, 512, 128 };
    for (unsigned s = 0; s < 3; s++) {
        unsigned len = make_frame(sizes[s]); ps_load(txf, len);
        unsigned tx0, rx0, tb0, tx1, rx1, tb1; mac_counts(0, &tx0, &rx0, &tb0); mac_counts(1, &tx1, &rx1, &tb1);
        const unsigned F = 2000;
        t0 = now_us();
        for (unsigned i = 0; i < F; i++) {
            for (unsigned o = 16; o <= 28; o += 4) PSR(PS_B, PS_TXBUF + o) = i;   /* patch the 4 header words (bucket + ID) */
            PSR(PS_B, PS_TX_LEN) = len; PSR(PS_B, PS_TX_COUNT) = 1; PSR(PS_B, PS_TX_CTL) = 1;
            while (PSR(PS_B, PS_TX_STAT) & 1) ;
        }
        t1 = now_us();
        usleep(50000);
        mac_counts(0, &tx0, &rx0, &tb0); mac_counts(1, &tx1, &rx1, &tb1);
        unsigned us = (unsigned)(t1 - t0);
        xil_printf("  PS-patch loop, %u frames of %u B: %u us -> %u us/frame, %u Mb/s on the wire; cage 1 TX %u frames %u B, cage 3 RX good %u\n\r",
                   F, len, us, us / F, (unsigned)((unsigned long long)F * (len + 4) * 8 / us), tx1, tb1, rx0);
        /* same frames, hardware repeat (TX_COUNT), for the injector's own ceiling */
        mac_counts(0, &tx0, &rx0, &tb0); mac_counts(1, &tx1, &rx1, &tb1);
        t0 = now_us(); PSR(PS_B, PS_TX_LEN) = len; PSR(PS_B, PS_TX_COUNT) = F; PSR(PS_B, PS_TX_CTL) = 1; while (PSR(PS_B, PS_TX_STAT) & 1) ; t1 = now_us();
        usleep(50000);
        mac_counts(0, &tx0, &rx0, &tb0); mac_counts(1, &tx1, &rx1, &tb1);
        us = (unsigned)(t1 - t0);
        xil_printf("  hardware repeat, %u frames of %u B: %u us -> %u Mb/s on the wire; cage 1 TX %u, cage 3 RX good %u, ilock drop %u\n\r",
                   F, len, us, (unsigned)((unsigned long long)F * (len + 4) * 8 / us), tx1, rx0, (unsigned)((*(volatile U32 *)(CTL_BASE + 4) >> 3) & 1));
    }
    xil_printf("done\n\r");
    return 0;
}
