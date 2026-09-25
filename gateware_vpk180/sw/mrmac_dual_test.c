/* Two-MRMAC 100GAUI-1 link test on the VPK180.
 *   port 0 = QSFPDD3 lane 3 (MRMAC_X0Y2, quad X0Y9 ch2/3, AXI 0xA4090000)
 *   port 1 = QSFPDD1 lane 3 (MRMAC_X1Y1, quad X1Y7 ch0/1, AXI 0xA4A00000)
 * One fibre between the two cages. Both MACs are brought up, then the example's PRBS
 * generator on each side sends a burst (shared trigger) and the other side's MRMAC
 * statistics must report the same packet and byte counts.
 * Built on the example program: mrmac_exdes_test_patched.inc = the sample C with
 * MRMAC_0_BASEADDR made a variable (mrmac_base) and main() renamed. */
#include "mrmac_exdes_test_patched.inc"
#include "xil_exception.h"
#include "sleep.h"

#define NPORT 2
static const unsigned long PORT_BASE[NPORT] = { 0xA4090000UL, 0xA4A00000UL };
static const char *PORT_NAME[NPORT] = { "port0 QSFPDD3.3", "port1 QSFPDD1.3" };

static void sync_abort(void *d) {
    unsigned long long esr, elr, far;
    asm volatile("mrs %0, esr_el3" : "=r"(esr));
    asm volatile("mrs %0, elr_el3" : "=r"(elr));
    asm volatile("mrs %0, far_el3" : "=r"(far));
    xil_printf("\n\rSYNC ABORT esr=%08x%08x elr=%08x%08x far=%08x%08x\n\r",
        (unsigned)(esr >> 32), (unsigned)esr, (unsigned)(elr >> 32), (unsigned)elr,
        (unsigned)(far >> 32), (unsigned)far);
    while (1) ;
}

static void mac_config(void) {                       /* the example's 1x100GE port-0 setup */
    *(U32 *)(MRMAC_0_RESET_REG_0) = 0xFFFFFFFF;
    *(U32 *)(MRMAC_0_MODE_REG_0) = 0x40000A64;
    *(U32 *)(MRMAC_0_CONFIGURATION_RX_REG1_0) = 0x00000033;
    *(U32 *)(MRMAC_0_CONFIGURATION_TX_REG1_0) = 0x00000C03;
    *(U32 *)(MRMAC_0_FEC_CONFIGURATION_REG1_0) = 0x0000000A;
    *(U32 *)(MRMAC_0_RESET_REG_0) = 0x00000000;
}

static unsigned mac_rx_status(void) {                /* write-to-clear, then read bits [2:0]; 0x7 = aligned */
    *(U32 *)(MRMAC_0_STAT_RX_STATUS_REG1_0) = 0xFFFFFFFF;
    return (*(U32 *)(MRMAC_0_STAT_RX_STATUS_REG1_0)) & 0x7;
}

static int wait_aligned(int p, int ms) {
    mrmac_base = PORT_BASE[p];
    for (int t = 0; t < ms; t += 10) {
        if (mac_rx_status() == 0x7) return 1;
        usleep(10000);
    }
    return 0;
}

typedef struct { uint64_t tx_pkts, tx_good_pkts, tx_bytes, tx_good_bytes, rx_pkts, rx_good_pkts, rx_bytes, rx_good_bytes; } stats_t;

static uint64_t rd64(unsigned long lsb, unsigned long msb) {
    uint32_t l = *(volatile U32 *)lsb, h = *(volatile U32 *)msb;
    return ((uint64_t)h << 32) | l;
}

static int mac_stats(stats_t *s) {                   /* tick = latch + clear the counters, then read */
    *(U32 *)(MRMAC_0_TICK_REG_0) = 0x00000001;
    int t;
    for (t = 0; t < 100000; t++)
        if (((*(volatile U32 *)(MRMAC_0_STAT_STATISTICS_READY_0)) & 0x3) == 0x3) break;
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

static void print_stats(int p, const stats_t *s) {
    xil_printf("  %s: TX pkts %u (good %u) bytes %u | RX pkts %u (good %u) bytes %u (good %u)\n\r", PORT_NAME[p],
        (unsigned)s->tx_pkts, (unsigned)s->tx_good_pkts, (unsigned)s->tx_bytes,
        (unsigned)s->rx_pkts, (unsigned)s->rx_good_pkts, (unsigned)s->rx_bytes, (unsigned)s->rx_good_bytes);
}

int main(void)
{
    Xil_ExceptionRegisterHandler(XIL_EXCEPTION_ID_SYNC_INT, sync_abort, NULL);
    xil_printf("\n\r*** VPK180 two-MRMAC 100GAUI-1 link test: QSFPDD3 lane 3 <-> QSFPDD1 lane 3 ***\n\r");
    for (int p = 0; p < NPORT; p++) {
        mrmac_base = PORT_BASE[p];
        xil_printf("%s @ 0x%08x: MRMAC revision 0x%08x\n\r", PORT_NAME[p], (unsigned)PORT_BASE[p],
                   (unsigned)*(volatile U32 *)(MRMAC_0_CONFIGURATION_REVISION_REG));
    }
    for (int p = 0; p < NPORT; p++) { mrmac_base = PORT_BASE[p]; mac_config(); }

    xil_printf("GT reset, external mode (no loopback), both quads\n\r");
    *(U32 *)(MRMAC_0_GT_LINERATE_RESET) = 0x4B000F02;
    *(U32 *)(MRMAC_0_GT_LINERATE_RESET) = 0x4B000002;
    sleep(2);
    wait_gt_rxresetdone(2);
    gt_rx_datapath_reset(2);

    int aligned[NPORT] = { 0, 0 };
    for (int attempt = 0; attempt < 8; attempt++) {
        for (int p = 0; p < NPORT; p++) aligned[p] = wait_aligned(p, 3000);
        xil_printf("attempt %d: RX aligned port0=%d port1=%d\n\r", attempt, aligned[0], aligned[1]);
        if (aligned[0] && aligned[1]) break;
        gt_rx_datapath_reset(2);
    }
    if (!(aligned[0] && aligned[1]))
        xil_printf("LINK: RX ALIGNMENT FAILED (port0=%d port1=%d), continuing to show statistics\n\r", aligned[0], aligned[1]);
    else
        xil_printf("LINK: both ports RX aligned\n\r");

    int pass = 1;
    for (int round = 0; round < 3; round++) {
        stats_t s[NPORT];
        for (int p = 0; p < NPORT; p++) { mrmac_base = PORT_BASE[p]; mac_stats(&s[p]); }   /* clear */
        *(U32 *)(MRMAC_0_GENMON_CTL_BASEADDR_LSB) = 0x00004501;   /* trigger both generators */
        *(U32 *)(MRMAC_0_GENMON_CTL_BASEADDR_LSB) = 0x00004500;
        sleep(1);
        xil_printf("round %d:\n\r", round);
        for (int p = 0; p < NPORT; p++) {
            mrmac_base = PORT_BASE[p];
            if (!mac_stats(&s[p])) xil_printf("  %s: statistics not ready\n\r", PORT_NAME[p]);
            print_stats(p, &s[p]);
        }
        int ok01 = s[0].tx_pkts != 0 && s[0].tx_pkts == s[1].rx_good_pkts && s[0].tx_bytes == s[1].rx_good_bytes;
        int ok10 = s[1].tx_pkts != 0 && s[1].tx_pkts == s[0].rx_good_pkts && s[1].tx_bytes == s[0].rx_good_bytes;
        xil_printf("  port0->port1: %s   port1->port0: %s\n\r", ok01 ? "PASS" : "FAIL", ok10 ? "PASS" : "FAIL");
        pass = pass && ok01 && ok10;
    }
    xil_printf(pass ? "RESULT: LINK TEST PASS\n\r" : "RESULT: LINK TEST FAIL\n\r");
    return 0;
}
