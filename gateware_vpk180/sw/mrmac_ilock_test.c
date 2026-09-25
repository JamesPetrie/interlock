/* Workload interlock (fabric_bridge) bring-up on the VPK180 PS-direct image, 1 ms buckets:
 *   frame port B (0xA4C00000) = prover frontend on ilock_pl port 0 (direct); requests go in here,
 *       request-side sync packets and certificates come back here
 *   frame port A (0xA4B00000) = compute side behind the cage 3 MRMAC, fibre to cage 1 = port 1;
 *       forwarded requests arrive here, responses go in here, response-side syncs come back here
 * Phase 1: core in -> sync cadence on both sides (1 ms, consecutive buckets), certificates every 1000 buckets.
 * Phase 2: requests stamped from the frontend sync (bucket + 1) forwarded to A; responses stamped from
 *          the compute sync forwarded to B; a stale-bucket request is dropped.
 * Wire format per docs/verification-protocol.md: 802.3 LENGTH = 64 + PLD_LEN, big-endian fields.
 * Injected frames carry 4 pad bytes after the DATA in place of an FCS; core frames carry the reframer's FCS. */
#include "mrmac_exdes_test_patched.inc"
#include "xil_exception.h"
#include "sleep.h"
#include "xtime_l.h"

#define NPORT 2
static const unsigned long PORT_BASE[2] = { 0xA4090000UL, 0xA4A00000UL };
#define CTL_BASE 0xA4D00000UL
#define CTL_REG  (*(volatile U32 *)(CTL_BASE + 0x0))
#define STAT_REG (*(volatile U32 *)(CTL_BASE + 0x4))
#define PS_A 0xA4B00000UL
#define PS_B 0xA4C00000UL
#define PS_TX_LEN 0x04
#define PS_TX_COUNT 0x08
#define PS_TX_CTL 0x0C
#define PS_TX_STAT 0x10
#define PS_RX_CTL 0x20
#define PS_RX_STAT 0x24
#define PS_RX_COUNT 0x28
#define PS_TXBUF 0x1000
#define PS_RXBUF 0x2000
#define PSR(b, o) (*(volatile U32 *)((b) + (o)))
#define ETH_HDR 14
#define CANON_HDR 64
#define CTL_PT   0x103u
#define CTL_CORE 0x303u
enum { K_SYNC, K_CERT, K_DATA, K_SHORT };

static void sync_abort(void *d) {
    unsigned long long esr, elr, far;
    asm volatile("mrs %0, esr_el3" : "=r"(esr)); asm volatile("mrs %0, elr_el3" : "=r"(elr)); asm volatile("mrs %0, far_el3" : "=r"(far));
    xil_printf("\n\rSYNC ABORT esr=%08x%08x elr=%08x%08x far=%08x%08x\n\r", (unsigned)(esr >> 32), (unsigned)esr, (unsigned)(elr >> 32), (unsigned)elr, (unsigned)(far >> 32), (unsigned)far);
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
static int wait_aligned(int p, int ms) { mrmac_base = PORT_BASE[p]; for (int t = 0; t < ms; t += 10) { if (mac_rx_status() == 0x7) return 1; usleep(10000); } return 0; }
static unsigned long long now_us(void) { XTime t; XTime_GetTime(&t); return (unsigned long long)t * 1000000ull / COUNTS_PER_SECOND; }   /* COUNTS_PER_SECOND = 99,999,001: no integer-truncated divisor */

/* ---- frame ports ---- */
static void ps_load(unsigned long b, const unsigned char *f, unsigned len) {
    for (unsigned i = 0; i < len; i += 4) { U32 w = 0; for (unsigned k = 0; k < 4 && i + k < len; k++) w |= (U32)f[i + k] << (8 * k); PSR(b, PS_TXBUF + i) = w; }
}
static void ps_start(unsigned long b, unsigned len) { PSR(b, PS_TX_LEN) = len; PSR(b, PS_TX_COUNT) = 1; PSR(b, PS_TX_CTL) = 1; }
static void ps_arm(unsigned long b) { PSR(b, PS_RX_CTL) = 0b011; }
static unsigned char rxf[4096], txf[2048];
/* wait up to `us` for the next queued frame; returns its length or 0 */
static unsigned ps_next(unsigned long b, unsigned us) {
    unsigned long long t0 = now_us();
    do {
        U32 st = PSR(b, PS_RX_STAT);
        if (st & 1) {
            unsigned len = (st >> 16) & 0x1FFF;
            for (unsigned i = 0; i < len; i += 4) { U32 w = PSR(b, PS_RXBUF + i); for (unsigned k = 0; k < 4 && i + k < len; k++) rxf[i + k] = (unsigned char)(w >> (8 * k)); }
            ps_arm(b);
            return len;
        }
    } while (now_us() - t0 < us);
    return 0;
}
static void ps_flush(unsigned long b) { ps_arm(b); while (ps_next(b, 150)) ; }   /* drain the queue until it stays empty for 150 us */
static unsigned be32(const unsigned char *p) { return ((unsigned)p[0] << 24) | ((unsigned)p[1] << 16) | ((unsigned)p[2] << 8) | p[3]; }
static void put32(unsigned char *p, unsigned v) { p[0] = v >> 24; p[1] = v >> 16; p[2] = v >> 8; p[3] = v; }
static int kind(unsigned len, unsigned *eth_len, unsigned *bucket, unsigned *idlo, unsigned *w0) {
    if (len < ETH_HDR + CANON_HDR) return K_SHORT;
    *eth_len = ((unsigned)rxf[12] << 8) | rxf[13];
    const unsigned char *d = rxf + ETH_HDR;
    *w0 = be32(d); *bucket = be32(d + 4); *idlo = be32(d + 12);
    unsigned idhi = be32(d + 8);
    if (idhi == 0 && *idlo == 1 && *eth_len == 64) return K_SYNC;
    if (idhi == 0 && *idlo == 0 && *eth_len == 224) return K_CERT;
    return K_DATA;
}
/* canonical frame: request (64-byte header with REFERENCE/KEY_COMMIT) or response (64-byte header, reserved tail) */
static unsigned build(unsigned char *f, int response, unsigned bucket, unsigned id, unsigned pld_len, unsigned seed) {
    static const unsigned char fe[6] = { 0x02, 0, 0, 0, 0, 0x01 }, cp[6] = { 0x02, 0, 0, 0, 0, 0x02 };
    unsigned n = CANON_HDR + pld_len;
    for (int i = 0; i < 6; i++) { f[i] = response ? fe[i] : cp[i]; f[6 + i] = response ? cp[i] : fe[i]; }
    f[12] = n >> 8; f[13] = n & 0xFF;
    unsigned char *h = f + ETH_HDR;
    for (int i = 0; i < CANON_HDR; i++) h[i] = 0;
    put32(h, pld_len); put32(h + 4, bucket); put32(h + 12, id);
    unsigned x = seed * 2654435761u + 7u;
    if (!response) for (int i = 32; i < 64; i++) { x = x * 1103515245u + 12345u; h[i] = (unsigned char)(x >> 16); }   /* KEY_COMMIT */
    for (unsigned i = 0; i < pld_len; i++) { x = x * 1103515245u + 12345u; h[CANON_HDR + i] = (unsigned char)(x >> 16); }
    for (int i = 0; i < 4; i++) f[ETH_HDR + n + i] = 0;
    return ETH_HDR + n + 4;
}
/* wait for a sync on port b (skipping anything else), return its bucket; 0xFFFFFFFF on timeout */
static unsigned next_sync(unsigned long b, unsigned us) {
    unsigned long long t0 = now_us();
    while (now_us() - t0 < us) {
        unsigned len = ps_next(b, 2000), el, bk, id, w0;
        if (len && kind(len, &el, &bk, &id, &w0) == K_SYNC) return bk;
    }
    return 0xFFFFFFFFu;
}
/* one canonical transfer: stamp from the sync on `from_sync`, send on `from`, expect on `to` */
static int transfer(const char *what, int response, unsigned long from, unsigned long to, unsigned id, unsigned pld, unsigned seed, int stale) {
    unsigned n = build(txf, response, 0, id, pld, seed);
    ps_load(from, txf, n);
    ps_flush(to);
    ps_flush(from);                                                     /* queued (stale) syncs would mis-stamp the bucket */
    unsigned k = next_sync(from, 20000);
    if (k == 0xFFFFFFFFu) { xil_printf("  %s: no sync on the sending side\n\r", what); return 0; }
    unsigned bucket = stale ? k : k + 1;
    put32(txf + ETH_HDR + 4, bucket);                                  /* frame offset 18: spans buffer words 4 and 5 */
    for (unsigned o = 16; o <= 20; o += 4)
        PSR(from, PS_TXBUF + o) = (U32)txf[o] | ((U32)txf[o + 1] << 8) | ((U32)txf[o + 2] << 16) | ((U32)txf[o + 3] << 24);
    unsigned long long ts = now_us();
    ps_start(from, n);
    unsigned want = ETH_HDR + CANON_HDR + pld, got = 0, el, bk, rid, w0;
    unsigned long long t0 = now_us();
    while (now_us() - t0 < 30000) {                                     /* forwarded at the next tick: within a few ms */
        unsigned len = ps_next(to, 3000);
        if (!len) continue;
        if (kind(len, &el, &bk, &rid, &w0) == K_DATA) { got = len; break; }
    }
    if (stale) {
        xil_printf("  %s stale bucket %u (current %u): %s\n\r", what, bucket, bucket + 1, got ? "FORWARDED (should have been dropped)" : "dropped as expected");
        return !got;
    }
    int same = got >= want;
    for (unsigned i = 0; same && i < want; i++) if (rxf[i] != txf[i]) same = 0;
    xil_printf("  %s bucket %u ID %u PLD_LEN %u: %s (%u B, %u us after send)\n\r", what, bucket, id, pld,
               !got ? "NOTHING forwarded" : same ? "forwarded verbatim" : "forwarded but DIFFERENT", got, (unsigned)(now_us() - ts));
    return same;
}

int main(void)
{
    Xil_ExceptionRegisterHandler(XIL_EXCEPTION_ID_SYNC_INT, sync_abort, NULL);
    xil_printf("\n\r*** VPK180 workload interlock (fabric_bridge) bring-up: 1 ms buckets ***\n\r");
    for (int p = 0; p < NPORT; p++) { mrmac_base = PORT_BASE[p]; mac_config(); }
    CTL_REG = 0x100;
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

    /* ---- phase 1: frontend side from the first tick ---- */
    CTL_REG = CTL_PT; usleep(20000);
    PSR(PS_A, PS_RX_CTL) = 0b110; PSR(PS_B, PS_RX_CTL) = 0b110;
    ps_arm(PS_B);
    unsigned long long t0 = now_us();
    CTL_REG = CTL_CORE;
    xil_printf("phase 1: core switched in at t=0; frontend port for 2.5 s (first 5 syncs and every certificate shown)\n\r");
    unsigned nsync = 0, ncert = 0, nother = 0, bad_seq = 0, last_bucket = 0, min_dt = 0xFFFFFFFF, max_dt = 0;
    unsigned long long last_t = 0, last_cert = 0;
    while (now_us() - t0 < 2500000ull) {
        unsigned len = ps_next(PS_B, 5000), el, bk, id, w0;
        if (!len) { xil_printf("  t=%u us: nothing for 5 ms\n\r", (unsigned)(now_us() - t0)); continue; }
        unsigned long long t = now_us() - t0;
        int k = kind(len, &el, &bk, &id, &w0);
        if (k == K_SYNC) {
            if (nsync) { unsigned dt = (unsigned)(t - last_t); if (dt < min_dt) min_dt = dt; if (dt > max_dt) max_dt = dt; if (bk != last_bucket + 1) bad_seq++; }
            if (nsync < 5) xil_printf("  t=%6u us: SYNC bucket %u first_arr 0x%08x (%u B)\n\r", (unsigned)t, bk, w0, len);
            last_bucket = bk; last_t = t; nsync++;
        } else if (k == K_CERT) {
            const unsigned char *c = rxf + ETH_HDR + 64;
            xil_printf("  t=%6u us (+%u us): CERT version %u device %u bkt_start %u bkt_num %u nonce %08x%08x%08x%08x (%u B, after sync bucket %u)\n\r",
                       (unsigned)t, (unsigned)(t - last_cert), be32(c), be32(c + 4), be32(c + 8), be32(c + 12), be32(c + 16), be32(c + 20), be32(c + 24), be32(c + 28), len, last_bucket);
            last_cert = t; ncert++;
        } else { nother++; if (nother <= 3) xil_printf("  t=%6u us: other frame %u B LENGTH %u ID %u\n\r", (unsigned)t, len, el, id); }
    }
    xil_printf("phase 1 (frontend): %u syncs, buckets %u..%u, %u out of sequence, interval %u..%u us; %u certs; %u other; drops flag %u\n\r",
               nsync, last_bucket + 1 - nsync, last_bucket, bad_seq, min_dt, max_dt, ncert, nother, (unsigned)((PSR(PS_B, PS_RX_STAT) >> 2) & 1));
    /* compute side: response-direction syncs over the fibre */
    ps_flush(PS_A); PSR(PS_A, PS_RX_CTL) = 0b010; ps_arm(PS_A);
    unsigned na = 0, bad_a = 0, lb = 0, na_other = 0; unsigned long long ta = now_us();
    while (now_us() - ta < 300000ull) {
        unsigned len = ps_next(PS_A, 5000), el, bk, id, w0;
        if (!len) continue;
        if (kind(len, &el, &bk, &id, &w0) == K_SYNC) { if (na && bk != lb + 1) bad_a++; lb = bk; na++; } else na_other++;
    }
    xil_printf("phase 1 (compute side, 300 ms): %u syncs, %u out of sequence, last bucket %u, %u other\n\r", na, bad_a, lb, na_other);
    int ok1 = nsync >= 2000 && bad_seq == 0 && ncert >= 2 && na >= 250 && bad_a == 0;

    /* ---- phase 2: requests B -> A, responses A -> B, stale request dropped ---- */
    xil_printf("phase 2: canonical traffic\n\r");
    static const unsigned plds[] = { 100, 300, 1000, 1436, 64 };
    int ok2 = 1; unsigned id = 2;
    for (unsigned i = 0; i < 5; i++, id += 2) ok2 &= transfer("request  B->A", 0, PS_B, PS_A, id, plds[i], 500 + i, 0);
    for (unsigned i = 0; i < 5; i++, id += 2) ok2 &= transfer("response A->B", 1, PS_A, PS_B, id, plds[i], 600 + i, 0);
    ok2 &= transfer("request  B->A", 0, PS_B, PS_A, id, 100, 700, 1);
    xil_printf("drops: A %u B %u, ilock drop flag %u\n\r", (unsigned)((PSR(PS_A, PS_RX_STAT) >> 2) & 1), (unsigned)((PSR(PS_B, PS_RX_STAT) >> 2) & 1), (unsigned)((STAT_REG >> 3) & 1));
    xil_printf("RESULT: phase 1 (sync/cert cadence) %s, phase 2 (data across) %s\n\r", ok1 ? "PASS" : "FAIL", ok2 ? "PASS" : "FAIL");
    CTL_REG = CTL_PT;
    return 0;
}
