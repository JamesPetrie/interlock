/* MRMAC FCS handling for the interlock core, on the PS-direct reference image (PG314 register map v1.4:
 * TX_REG1 0x000C bit1 ctl_tx_fcs_ins_enable, bit2 ctl_tx_ignore_fcs; RX_REG1 0x0010 bit1 ctl_rx_delete_fcs,
 * bit2 ctl_rx_ignore_fcs). The core's egress on cage 1 TX (sync packets: 14 + 64 + reframer FCS = 82 B)
 * is received by cage 3 and captured by frame port A. Per setting: cage 1 TX count, cage 3 RX total/good,
 * cage 3 RX bad-FCS, captured length. Then a canonical request B -> core -> cage 1 -> cage 3 -> A and a
 * response A -> cage 3 -> cage 1 -> core -> B with the chosen settings. */
#include "mrmac_exdes_test_patched.inc"
#include "xil_exception.h"
#include "sleep.h"

#define NPORT 2
static const unsigned long PORT_BASE[2] = { 0xA4090000UL, 0xA4A00000UL };
#define CTL_BASE 0xA4D00000UL
#define CTL_REG  (*(volatile U32 *)(CTL_BASE + 0x0))
#define PS_A 0xA4B00000UL
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
#define STAT_RX_BAD_FCS_LSB 0x0EE8   /* PG314 v1.4 STAT_RX_BAD_FCS_0_LSB */
#define ETH_HDR 14
#define CANON_HDR 64

static void sync_abort(void *d) {
    unsigned long long esr, elr, far;
    asm volatile("mrs %0, esr_el3" : "=r"(esr)); asm volatile("mrs %0, elr_el3" : "=r"(elr)); asm volatile("mrs %0, far_el3" : "=r"(far));
    xil_printf("\n\rSYNC ABORT esr=%08x%08x elr=%08x%08x far=%08x%08x\n\r", (unsigned)(esr >> 32), (unsigned)esr, (unsigned)(elr >> 32), (unsigned)elr, (unsigned)(far >> 32), (unsigned)far);
    while (1) ;
}
static void mac_config(void) {
    *(U32 *)(MRMAC_0_RESET_REG_0) = 0xFFFFFFFF; *(U32 *)(MRMAC_0_MODE_REG_0) = 0x40000A64;
    *(U32 *)(MRMAC_0_CONFIGURATION_RX_REG1_0) = 0x00000033; *(U32 *)(MRMAC_0_CONFIGURATION_TX_REG1_0) = 0x00000C03;
    *(U32 *)(MRMAC_0_FEC_CONFIGURATION_REG1_0) = 0x0000000A; *(U32 *)(MRMAC_0_RESET_REG_0) = 0x00000000;
}
static unsigned mac_rx_status(void) { *(U32 *)(MRMAC_0_STAT_RX_STATUS_REG1_0) = 0xFFFFFFFF; return (*(U32 *)(MRMAC_0_STAT_RX_STATUS_REG1_0)) & 0x7; }
static int wait_aligned(int p, int ms) { mrmac_base = PORT_BASE[p]; for (int t = 0; t < ms; t += 10) { if (mac_rx_status() == 0x7) return 1; usleep(10000); } return 0; }
static uint64_t rd64(unsigned long lsb, unsigned long msb) { uint32_t l = *(volatile U32 *)lsb, h = *(volatile U32 *)msb; return ((uint64_t)h << 32) | l; }
typedef struct { unsigned tx, rxt, rxg; } cnt_t;
static cnt_t mac_counts(int p) {
    cnt_t c; mrmac_base = PORT_BASE[p];
    *(U32 *)(MRMAC_0_TICK_REG_0) = 1;
    for (int t = 0; t < 100000; t++) if (((*(volatile U32 *)(MRMAC_0_STAT_STATISTICS_READY_0)) & 0x3) == 0x3) break;
    c.tx  = (unsigned)rd64(MRMAC_0_STAT_TX_TOTAL_PACKETS_0_LSB, MRMAC_0_STAT_TX_TOTAL_PACKETS_0_MSB);
    c.rxg = (unsigned)rd64(MRMAC_0_STAT_RX_TOTAL_GOOD_PACKETS_0_LSB, MRMAC_0_STAT_RX_TOTAL_GOOD_PACKETS_0_MSB);
    c.rxt = (unsigned)rd64(MRMAC_0_STAT_RX_TOTAL_PACKETS_0_LSB, MRMAC_0_STAT_RX_TOTAL_PACKETS_0_MSB);
    return c;
}
static unsigned char rxf[4096], txf[2048];
static unsigned drain_last(unsigned long b, unsigned *frames) {           /* drain the queue; last frame in rxf */
    unsigned len = 0; *frames = 0;
    PSR(b, PS_RX_CTL) = 0b011;
    for (int i = 0; i < 3000; i++) {
        U32 st = PSR(b, PS_RX_STAT);
        if (st & 1) { len = (st >> 16) & 0x1FFF; (*frames)++; for (unsigned k = 0; k < len; k += 4) { U32 w = PSR(b, PS_RXBUF + k); for (unsigned j = 0; j < 4 && k + j < len; j++) rxf[k + j] = (unsigned char)(w >> (8 * j)); } PSR(b, PS_RX_CTL) = 0b011; }
        usleep(10);
    }
    return len;
}
static int realign(void) { int a = 0; for (int i = 0; i < 6 && !a; i++) { a = wait_aligned(0, 1000) && wait_aligned(1, 1000); if (!a) gt_rx_datapath_reset(2); } return a; }
static void set_regs(unsigned tx1, unsigned rx0, int with_reset) {
    if (with_reset) {                                                        /* configuration under MAC reset, as the example does */
        mrmac_base = PORT_BASE[1]; *(U32 *)(MRMAC_0_RESET_REG_0) = 0xFFFFFFFF; *(U32 *)(MRMAC_0_CONFIGURATION_TX_REG1_0) = tx1; *(U32 *)(MRMAC_0_RESET_REG_0) = 0;
        mrmac_base = PORT_BASE[0]; *(U32 *)(MRMAC_0_RESET_REG_0) = 0xFFFFFFFF; *(U32 *)(MRMAC_0_CONFIGURATION_RX_REG1_0) = rx0; *(U32 *)(MRMAC_0_RESET_REG_0) = 0;
        usleep(50000);
        int a = realign(); xil_printf("    (after MAC reset: aligned=%d)\n\r", a);
    } else {
        mrmac_base = PORT_BASE[1]; *(U32 *)(MRMAC_0_CONFIGURATION_TX_REG1_0) = tx1;
        mrmac_base = PORT_BASE[0]; *(U32 *)(MRMAC_0_CONFIGURATION_RX_REG1_0) = rx0;
    }
}
static void trial2(const char *what, unsigned tx1, unsigned rx0, int with_reset);
static void trial(const char *what, unsigned tx1, unsigned rx0) { trial2(what, tx1, rx0, 0); }
static void trial2(const char *what, unsigned tx1, unsigned rx0, int with_reset) {
    set_regs(tx1, rx0, with_reset);
    usleep(20000);
    mac_counts(0); mac_counts(1);
    usleep(200000);
    cnt_t c0 = mac_counts(0), c1 = mac_counts(1);
    unsigned frames, len = drain_last(PS_A, &frames);
    if (len >= 4)
        xil_printf("  %-38s TX1=0x%03x RX0=0x%02x : cage1 TX %3u | cage3 RX total %3u good %3u | A: %u frames, last %u B, tail %02x%02x%02x%02x\n\r",
                   what, tx1, rx0, c1.tx, c0.rxt, c0.rxg, frames, len, rxf[len-4], rxf[len-3], rxf[len-2], rxf[len-1]);
    else
        xil_printf("  %-38s TX1=0x%03x RX0=0x%02x : cage1 TX %3u | cage3 RX total %3u good %3u | A: no frame captured (errored frames are dropped before the capture)\n\r",
                   what, tx1, rx0, c1.tx, c0.rxt, c0.rxg);
}
static void put32(unsigned char *p, unsigned v) { p[0] = v >> 24; p[1] = v >> 16; p[2] = v >> 8; p[3] = v; }
static unsigned be32(const unsigned char *p) { return ((unsigned)p[0] << 24) | ((unsigned)p[1] << 16) | ((unsigned)p[2] << 8) | p[3]; }
static void ps_load(unsigned long b, const unsigned char *f, unsigned len) { for (unsigned i = 0; i < len; i += 4) { U32 w = 0; for (unsigned k = 0; k < 4 && i + k < len; k++) w |= (U32)f[i + k] << (8 * k); PSR(b, PS_TXBUF + i) = w; } }
static unsigned cur_bucket(unsigned long b) {                              /* wait for a fresh sync on port b */
    PSR(b, PS_RX_CTL) = 0b011; for (int i = 0; i < 400; i++) { if (PSR(b, PS_RX_STAT) & 1) PSR(b, PS_RX_CTL) = 0b011; usleep(10); }
    for (int i = 0; i < 3000; i++) {
        U32 st = PSR(b, PS_RX_STAT);
        if (st & 1) {
            unsigned len = (st >> 16) & 0x1FFF; for (unsigned k = 0; k < 32; k += 4) { U32 w = PSR(b, PS_RXBUF + k); for (unsigned j = 0; j < 4; j++) rxf[k + j] = (unsigned char)(w >> (8 * j)); }
            PSR(b, PS_RX_CTL) = 0b011;
            if (len >= 78 && rxf[13] == 64 && be32(rxf + ETH_HDR + 12) == 1) return be32(rxf + ETH_HDR + 4) + 1;
        }
        usleep(2);
    }
    return 0;
}
static void xfer(const char *what, int response, unsigned long from, unsigned long to, unsigned id, unsigned pld) {
    unsigned n = CANON_HDR + pld;
    for (int i = 0; i < 14; i++) txf[i] = 0;
    txf[0] = 0x02; txf[5] = response ? 0x01 : 0x02; txf[6] = 0x02; txf[11] = response ? 0x02 : 0x01; txf[12] = n >> 8; txf[13] = n & 0xFF;
    unsigned char *h = txf + ETH_HDR; for (int i = 0; i < 64; i++) h[i] = 0;
    put32(h, pld); put32(h + 12, id);
    unsigned x = id * 7919u; for (unsigned i = (response ? 64 : 32); i < 64 + pld; i++) { x = x * 1103515245u + 12345u; h[i] = (unsigned char)(x >> 16); }
    for (int i = 0; i < 4; i++) txf[ETH_HDR + n + i] = 0;                  /* pad in place of an FCS (B is direct; A's MAC inserts one) */
    unsigned total = ETH_HDR + n + 4;
    ps_load(from, txf, total);
    PSR(to, PS_RX_CTL) = 0b011; for (int i = 0; i < 400; i++) { if (PSR(to, PS_RX_STAT) & 1) PSR(to, PS_RX_CTL) = 0b011; usleep(10); }
    unsigned bk = cur_bucket(from);
    put32(txf + ETH_HDR + 4, bk);
    for (unsigned o = 16; o <= 20; o += 4) PSR(from, PS_TXBUF + o) = (U32)txf[o] | ((U32)txf[o+1] << 8) | ((U32)txf[o+2] << 16) | ((U32)txf[o+3] << 24);
    PSR(from, PS_TX_LEN) = total; PSR(from, PS_TX_COUNT) = 1; PSR(from, PS_TX_CTL) = 1;
    unsigned got = 0, want = ETH_HDR + n;
    for (int i = 0; i < 30000 && !got; i++) {
        U32 st = PSR(to, PS_RX_STAT);
        if (st & 1) {
            unsigned len = (st >> 16) & 0x1FFF; for (unsigned k = 0; k < len; k += 4) { U32 w = PSR(to, PS_RXBUF + k); for (unsigned j = 0; j < 4 && k + j < len; j++) rxf[k + j] = (unsigned char)(w >> (8 * j)); }
            PSR(to, PS_RX_CTL) = 0b011;
            if (!(rxf[13] == 64 && be32(rxf + ETH_HDR + 12) == 1) && !(rxf[12] == 0 && rxf[13] == 224)) got = len;   /* not a sync / cert */
        } else usleep(1);
    }
    int same = got >= want; for (unsigned i = 0; same && i < want; i++) if (rxf[i] != txf[i]) same = 0;
    xil_printf("  %s bucket %u ID %u PLD %u: %s (%u B; frame + %d trailing bytes)\n\r", what, bk, id, pld, !got ? "NOTHING" : same ? "forwarded verbatim" : "DIFFERENT", got, (int)got - (int)want);
}

#define MR(off) (*(volatile U32 *)(MRMAC_0_BASEADDR + (off)))
static void tick(int p) { mrmac_base = PORT_BASE[p]; *(U32 *)(MRMAC_0_TICK_REG_0) = 1; for (int t = 0; t < 100000; t++) if (((*(volatile U32 *)(MRMAC_0_STAT_STATISTICS_READY_0)) & 0x3) == 0x3) break; }
static unsigned st(int p, unsigned off) { mrmac_base = PORT_BASE[p]; return MR(off); }
/* window statistics: tick both, wait, tick both, read (PG314 counters are captured and cleared on the tick) */
static void stats_window(const char *what, int txp, int rxp, unsigned wait_ms) {
    tick(0); tick(1); usleep(wait_ms * 1000); tick(0); tick(1);
    xil_printf("  %s\n\r    cage%s TX: total %u good %u bytes %u  frame_err %u bad_fcs %u small %u large %u  65-127 %u\n\r", what, txp ? "1" : "3",
        st(txp, 0x818), st(txp, 0x820), st(txp, 0x828), st(txp, 0x808), st(txp, 0x8D0), st(txp, 0x8A0), st(txp, 0x898), st(txp, 0x840));
    xil_printf("    cage%s RX: total %u good %u bytes %u  bad_fcs %u pkt_bad_fcs %u stomped %u inrange_err %u trunc %u  framing_err %u bad_code %u undersize %u fragment %u toolong %u\n\r", rxp ? "1" : "3",
        st(rxp, 0xE30), st(rxp, 0xE38), st(rxp, 0xE40), st(rxp, 0xEE8), st(rxp, 0xEF0), st(rxp, 0xEF8), st(rxp, 0xF30), st(rxp, 0xF38),
        st(rxp, 0xCA8), st(rxp, 0xD58), st(rxp, 0xEC0), st(rxp, 0xEC8), st(rxp, 0xED8));
}
static void set_tx(int p, unsigned v) { mrmac_base = PORT_BASE[p]; *(U32 *)(MRMAC_0_RESET_REG_0) = 0xFFFFFFFF; *(U32 *)(MRMAC_0_CONFIGURATION_TX_REG1_0) = v; *(U32 *)(MRMAC_0_RESET_REG_0) = 0; usleep(50000); realign(); }
static void set_rx(int p, unsigned v) { mrmac_base = PORT_BASE[p]; *(U32 *)(MRMAC_0_RESET_REG_0) = 0xFFFFFFFF; *(U32 *)(MRMAC_0_CONFIGURATION_RX_REG1_0) = v; *(U32 *)(MRMAC_0_RESET_REG_0) = 0; usleep(50000); realign(); }
static unsigned crc32_eth(const unsigned char *p, unsigned n) { unsigned c = 0xFFFFFFFFu; for (unsigned i = 0; i < n; i++) { c ^= p[i]; for (int k = 0; k < 8; k++) c = (c >> 1) ^ (0xEDB88320u & (0u - (c & 1))); } return ~c; }
static unsigned le32(const unsigned char *p) { return (unsigned)p[0] | ((unsigned)p[1] << 8) | ((unsigned)p[2] << 16) | ((unsigned)p[3] << 24); }
static void fcs_check(void) {                                             /* is the reframer's FCS valid? capture with cage 3 delete_fcs off */
    set_rx(0, 0x31);
    unsigned frames, len = drain_last(PS_A, &frames);
    if (len < 12) { xil_printf("  fcs-check: no frame captured (len %u)\n\r", len); }
    else {
        unsigned inner = crc32_eth(rxf, len - 8), outer = crc32_eth(rxf, len - 4);
        xil_printf("  fcs-check on a %u B core frame (LENGTH field %u): inner FCS %08x computed %08x %s | outer (MAC) FCS %08x computed %08x %s\n\r",
                   len, ((unsigned)rxf[12] << 8) | rxf[13], le32(rxf + len - 8), inner, le32(rxf + len - 8) == inner ? "OK" : "MISMATCH",
                   le32(rxf + len - 4), outer, le32(rxf + len - 4) == outer ? "OK" : "MISMATCH");
    }
    set_rx(0, 0x33);
}
static void inject_a(unsigned pld, int good_fcs, unsigned count) {        /* raw frame from A with a software FCS */
    unsigned n = ETH_HDR + pld;
    for (int i = 0; i < 14; i++) txf[i] = 0;
    txf[0] = 0x02; txf[5] = 0x01; txf[6] = 0x02; txf[11] = 0x02; txf[12] = pld >> 8; txf[13] = pld & 0xFF;
    for (unsigned i = 0; i < pld; i++) txf[ETH_HDR + i] = (unsigned char)(i * 13 + 7);
    unsigned c = crc32_eth(txf, n) ^ (good_fcs ? 0 : 0x00010000u);
    txf[n] = c; txf[n + 1] = c >> 8; txf[n + 2] = c >> 16; txf[n + 3] = c >> 24;
    ps_load(PS_A, txf, n + 4);
    PSR(PS_A, PS_TX_LEN) = n + 4; PSR(PS_A, PS_TX_COUNT) = count; PSR(PS_A, PS_TX_CTL) = 1;
    for (int i = 0; i < 100000 && (PSR(PS_A, PS_TX_STAT) & 1); i++) usleep(1);
}
static void a_side(const char *what, unsigned tx0, int good_fcs) {
    set_tx(0, tx0);
    tick(0); tick(1); inject_a(100, good_fcs, 5); usleep(50000); tick(0); tick(1);
    xil_printf("  A-side %-34s cage3 TX0=0x%03x: cage3 TX total %u good %u bad_fcs %u frame_err %u | cage1 RX total %u good %u bad_fcs %u inrange_err %u\n\r",
               what, tx0, st(0, 0x818), st(0, 0x820), st(0, 0x8D0), st(0, 0x808), st(1, 0xE30), st(1, 0xE38), st(1, 0xEE8), st(1, 0xF30));
}

int main(void)
{
    Xil_ExceptionRegisterHandler(XIL_EXCEPTION_ID_SYNC_INT, sync_abort, NULL);
    xil_printf("\n\r*** MRMAC FCS handling for the core (core sync packets: 82 B on the wire incl. the reframer FCS) ***\n\r");
    for (int p = 0; p < NPORT; p++) { mrmac_base = PORT_BASE[p]; mac_config(); }
    CTL_REG = 0x100;
    *(U32 *)(MRMAC_0_GT_LINERATE_RESET) = 0x4B000F02; *(U32 *)(MRMAC_0_GT_LINERATE_RESET) = 0x4B000002;
    sleep(2); wait_gt_rxresetdone(2); gt_rx_datapath_reset(2);
    int al0 = 0, al1 = 0;
    for (int a = 0; a < 6 && !(al0 && al1); a++) { al0 = wait_aligned(0, 2000); al1 = wait_aligned(1, 2000); if (!(al0 && al1)) gt_rx_datapath_reset(2); }
    xil_printf("link: P0=%d P1=%d\n\r", al0, al1);
    CTL_REG = 0x303; usleep(50000);
    trial("baseline: insert on / delete on",          0xC03, 0x33);
    fcs_check();
    stats_window("baseline 0xC03 (200 ms of core syncs)", 1, 0, 200);
    set_tx(1, 0xC06); stats_window("cage 1 TX 0xC06: insertion off, ignore", 1, 0, 200);
    set_tx(1, 0xC02); stats_window("cage 1 TX 0xC02: insertion off, check", 1, 0, 200);
    set_tx(1, 0xC03); stats_window("cage 1 TX back to 0xC03", 1, 0, 200);
    /* independent of the core: frames from A (behind cage 3) with a software FCS, cage 3 MAC insertion off */
    a_side("0xC03 insert on, sw FCS appended too", 0xC03, 1);
    a_side("0xC06 insert off, good sw FCS",        0xC06, 1);
    a_side("0xC02 insert off, check, good sw FCS", 0xC02, 1);
    a_side("0xC02 insert off, check, BAD sw FCS",  0xC02, 0);
    a_side("0xC06 insert off, ignore, BAD sw FCS", 0xC06, 0);
    a_side("back to 0xC03, good sw FCS",           0xC03, 1);
    xil_printf("done\n\r");
    CTL_REG = 0x103;
    return 0;
}
