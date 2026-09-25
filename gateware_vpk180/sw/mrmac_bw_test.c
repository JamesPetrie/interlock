/* Throughput of the workload interlock (fabric_bridge, 1 ms buckets), request direction:
 *   frame port B (frontend, direct on ilock_pl port 0) sends canonical requests, the PS patches ID and
 *   BUCKET per frame; forwarded frames leave on cage 1 (MRMAC TX statistics) and arrive at cage 3 (RX).
 * The bucket comes from the sync packets B receives (queued capture, read in the same loop). With
 * "guard" on, no frame is started within GUARD_US of the predicted next tick, as a real frontend would. */
#include "mrmac_exdes_test_patched.inc"
#include "xil_exception.h"
#include "sleep.h"
#include "xtime_l.h"

#define NPORT 2
static const unsigned long PORT_BASE[2] = { 0xA4090000UL, 0xA4A00000UL };
#define CTL_BASE 0xA4D00000UL
#define CTL_REG  (*(volatile U32 *)(CTL_BASE + 0x0))
#define STAT_REG (*(volatile U32 *)(CTL_BASE + 0x4))
#define PS_B 0xA4C00000UL
#define PS_TX_LEN 0x04
#define PS_TX_COUNT 0x08
#define PS_TX_CTL 0x0C
#define PS_TX_STAT 0x10
#define PS_RX_CTL 0x20
#define PS_RX_STAT 0x24
#define PS_TXBUF 0x1000
#define PS_RXBUF 0x2000
#define PSR(b, o) (*(volatile U32 *)((b) + (o)))
#define ETH_HDR 14
#define CANON_HDR 64

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
static void mac_counts(int p, unsigned *tx, unsigned *rxg, unsigned *rxt) {
    mrmac_base = PORT_BASE[p];
    *(U32 *)(MRMAC_0_TICK_REG_0) = 1;
    for (int t = 0; t < 100000; t++) if (((*(volatile U32 *)(MRMAC_0_STAT_STATISTICS_READY_0)) & 0x3) == 0x3) break;
    *tx  = (unsigned)rd64(MRMAC_0_STAT_TX_TOTAL_PACKETS_0_LSB, MRMAC_0_STAT_TX_TOTAL_PACKETS_0_MSB);
    *rxg = (unsigned)rd64(MRMAC_0_STAT_RX_TOTAL_GOOD_PACKETS_0_LSB, MRMAC_0_STAT_RX_TOTAL_GOOD_PACKETS_0_MSB);
    *rxt = (unsigned)rd64(MRMAC_0_STAT_RX_TOTAL_PACKETS_0_LSB, MRMAC_0_STAT_RX_TOTAL_PACKETS_0_MSB);
}
static unsigned be32(const unsigned char *p) { return ((unsigned)p[0] << 24) | ((unsigned)p[1] << 16) | ((unsigned)p[2] << 8) | p[3]; }
static void put32(unsigned char *p, unsigned v) { p[0] = v >> 24; p[1] = v >> 16; p[2] = v >> 8; p[3] = v; }
static unsigned char txf[2048], rxf[4096];
static unsigned build_request(unsigned pld_len) {                        /* header stamped later; 4 pad bytes after the DATA */
    unsigned n = CANON_HDR + pld_len;
    for (int i = 0; i < 6; i++) { txf[i] = 0; txf[6 + i] = 0; }
    txf[0] = 0x02; txf[5] = 0x02; txf[6] = 0x02; txf[11] = 0x01;
    txf[12] = n >> 8; txf[13] = n & 0xFF;
    unsigned char *h = txf + ETH_HDR;
    for (int i = 0; i < CANON_HDR; i++) h[i] = 0;
    put32(h, pld_len);
    unsigned x = 12345;
    for (int i = 32; i < 64; i++) { x = x * 1103515245u + 12345u; h[i] = (unsigned char)(x >> 16); }
    for (unsigned i = 0; i < pld_len; i++) { x = x * 1103515245u + 12345u; h[CANON_HDR + i] = (unsigned char)(x >> 16); }
    for (int i = 0; i < 4; i++) txf[ETH_HDR + n + i] = 0;
    return ETH_HDR + n + 4;
}
static void ps_load(const unsigned char *f, unsigned len) {
    for (unsigned i = 0; i < len; i += 4) { U32 w = 0; for (unsigned k = 0; k < 4 && i + k < len; k++) w |= (U32)f[i + k] << (8 * k); PSR(PS_B, PS_TXBUF + i) = w; }
}
/* poll B's capture: if a sync is there, update the bucket and the tick time estimate; returns 1 on a sync */
static unsigned cur_bucket = 0; static unsigned long long last_tick_us = 0; static unsigned nsync = 0;
static int poll_sync(void) {
    U32 st = PSR(PS_B, PS_RX_STAT);
    if (!(st & 1)) return 0;
    unsigned len = (st >> 16) & 0x1FFF, w[8];
    for (unsigned i = 0; i < 8 && i * 4 < len; i++) w[i] = PSR(PS_B, PS_RXBUF + 4 * i);
    PSR(PS_B, PS_RX_CTL) = 0b011;                                            /* clear + re-arm: next queued frame */
    unsigned char h[32]; for (unsigned i = 0; i < 8; i++) { h[4*i] = w[i]; h[4*i+1] = w[i] >> 8; h[4*i+2] = w[i] >> 16; h[4*i+3] = w[i] >> 24; }
    unsigned eth_len = ((unsigned)h[12] << 8) | h[13];
    if (eth_len == 64 && be32(h + ETH_HDR + 8) == 0 && be32(h + ETH_HDR + 12) == 1) {
        cur_bucket = be32(h + ETH_HDR + 4) + 1; last_tick_us = now_us(); nsync++;
        return 1;
    }
    return 0;
}
static void drain(void) { PSR(PS_B, PS_RX_CTL) = 0b011; for (int i = 0; i < 400; i++) { poll_sync(); usleep(20); } }

/* one run: F frames of `len` bytes, minimum `period_us` per frame (0 = as fast as possible), guard before the predicted tick */
static void run(const char *tag, unsigned F, unsigned len, unsigned period_us, unsigned guard_us, unsigned *id) {
    drain();
    unsigned tx0, rx0, rt0, tx1, rx1, rt1;
    mac_counts(0, &tx0, &rx0, &rt0); mac_counts(1, &tx1, &rx1, &rt1);
    poll_sync();
    unsigned bclr = cur_bucket;                                              /* bucket when the counters were cleared */
    unsigned long long t0 = now_us(), tnext = t0, guarded = 0;
    unsigned b0 = cur_bucket;
    for (unsigned i = 0; i < F; i++) {
        poll_sync();
        while (now_us() < tnext) poll_sync();                               /* pacing first ... */
        tnext += period_us;
        if (guard_us) {                                                     /* ... then hold off while a tick is imminent */
            while (now_us() + guard_us >= last_tick_us + 1000) { if (poll_sync()) break; guarded++; }
        }
        put32(txf + ETH_HDR + 4, cur_bucket); put32(txf + ETH_HDR + 12, *id); *id += 2;
        for (unsigned o = 16; o <= 28; o += 4) PSR(PS_B, PS_TXBUF + o) = (U32)txf[o] | ((U32)txf[o+1] << 8) | ((U32)txf[o+2] << 16) | ((U32)txf[o+3] << 24);
        PSR(PS_B, PS_TX_LEN) = len; PSR(PS_B, PS_TX_COUNT) = 1; PSR(PS_B, PS_TX_CTL) = 1;
        while (PSR(PS_B, PS_TX_STAT) & 1) ;
    }
    unsigned long long t1 = now_us();
    usleep(20000);                                                          /* the last bucket drains at the next tick */
    for (int i = 0; i < 50; i++) { poll_sync(); usleep(20); }
    mac_counts(0, &tx0, &rx0, &rt0); mac_counts(1, &tx1, &rx1, &rt1);
    poll_sync();
    unsigned syncs = cur_bucket - bclr;                                      /* one compute-side sync per bucket also leaves on cage 1 */
    int data_fwd = (int)tx1 - (int)syncs, lost = (int)F - data_fwd;
    unsigned us = (unsigned)(t1 - t0), wire = len + 4;
    xil_printf("  %s %4u B x %u: %6u us, sent %u Mb/s | cage 1 TX %u (- %u syncs = %d data), cage 3 RX total %u good %u | lost ~%d (%d.%02d%%) | %u buckets, %u guard waits\n\r",
               tag, len, F, us, (unsigned)((unsigned long long)F * wire * 8 / us), tx1, syncs, data_fwd, rt0, rx0,
               lost, lost * 100 / (int)F, (lost * 10000 / (int)F) % 100, cur_bucket - b0, (unsigned)guarded);
}

int main(void)
{
    Xil_ExceptionRegisterHandler(XIL_EXCEPTION_ID_SYNC_INT, sync_abort, NULL);
    xil_printf("\n\r*** Workload interlock throughput, requests (frontend -> core -> cage 1 -> fibre -> cage 3) ***\n\r");
    for (int p = 0; p < NPORT; p++) { mrmac_base = PORT_BASE[p]; mac_config(); }
    CTL_REG = 0x100;
    *(U32 *)(MRMAC_0_GT_LINERATE_RESET) = 0x4B000F02; *(U32 *)(MRMAC_0_GT_LINERATE_RESET) = 0x4B000002;
    sleep(2); wait_gt_rxresetdone(2); gt_rx_datapath_reset(2);
    int al0 = 0, al1 = 0;
    for (int a = 0; a < 6 && !(al0 && al1); a++) { al0 = wait_aligned(0, 2000); al1 = wait_aligned(1, 2000); if (!(al0 && al1)) gt_rx_datapath_reset(2); }
    xil_printf("link: P0=%d P1=%d\n\r", al0, al1);
    if (!(al0 && al1)) return 0;
    CTL_REG = 0x303; usleep(50000);                                         /* core in, crossover, A and ilock own the MAC TXs */
    unsigned id = 2;
    static const unsigned sizes[] = { 1514, 512, 128 };                   /* 1514 = 14 + 64 + 1436: the largest canonical frame */
    for (unsigned s = 0; s < 3; s++) {
        unsigned n = build_request(sizes[s] - ETH_HDR - CANON_HDR); ps_load(txf, n);   /* n = size + 4 pad */
        unsigned wire = n + 4;                                               /* + MAC FCS */
        unsigned p1000 = (unsigned)((unsigned long long)wire * 8 / 1000);    /* period for 1.0 Gb/s on the wire */
        run("max rate, no guard ", 20000, n, 0, 0, &id);
        run("max rate, guard 25us", 20000, n, 0, 25, &id);
        run("1.0 Gb/s, guard 25us", 20000, n, p1000, 25, &id);
    }
    xil_printf("ilock drop flag %u, syncs read %u\n\rdone\n\r", (unsigned)((STAT_REG >> 3) & 1), nsync);
    CTL_REG = 0x103;
    return 0;
}
