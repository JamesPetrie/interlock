/* Interlock core bring-up on the VPK180 PS-direct image (INLINE_TOPO=psdirect, same bitstream):
 *   frame port B (0xA4C00000) = the prover frontend on ilock_pl port 0 (direct, no MAC)
 *   frame port A (0xA4B00000) = the enclosure side, behind the cage 3 MRMAC, over the fibre to cage 1 = port 1
 * Phase 1: switch the recomp core in and watch the frontend port: one sync packet per bucket (100 ms,
 *          BUCKET = the bucket just closed, FIRST_ARR all-ones while idle) and a certificate every
 *          10 buckets, from the very first tick.
 * Phase 2: stamp canonical requests with the current bucket (last sync + 1) and increasing IDs and
 *          check they are forwarded verbatim to the enclosure side; a stale-bucket packet must be dropped.
 * Wire format (verification-protocol.md): 802.3 frame, LENGTH = 64 + PLD_LEN, DATA = request header
 * (PLD_LEN, BUCKET, ID, REFERENCE, RESERVED, KEY_COMMIT; big-endian) + payload. Frames injected here
 * carry 4 pad bytes after the DATA in place of an FCS (the deframer forwards LENGTH octets and drops
 * the rest); frames from the core carry the reframer's FCS, ignored on capture. */
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
#define CTL_PT   0x103u   /* ext_sel A + ilock, crossover, pass-through */
#define CTL_CORE 0x303u   /* ... + recomp core in */

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
static unsigned now_ms(void) { XTime t; XTime_GetTime(&t); return (unsigned)(t / (COUNTS_PER_SECOND / 1000)); }

/* ---- frame ports ---- */
static void ps_load(unsigned long b, const unsigned char *f, unsigned len) {
    for (unsigned i = 0; i < len; i += 4) {
        U32 w = 0;
        for (unsigned k = 0; k < 4 && i + k < len; k++) w |= (U32)f[i + k] << (8 * k);
        PSR(b, PS_TXBUF + i) = w;
    }
}
static int ps_send(unsigned long b, unsigned len) {
    PSR(b, PS_TX_LEN) = len; PSR(b, PS_TX_COUNT) = 1; PSR(b, PS_TX_CTL) = 1;
    for (int t = 0; t < 200000; t++) if (!(PSR(b, PS_TX_STAT) & 1)) return 1;
    return 0;
}
static void ps_arm(unsigned long b) { PSR(b, PS_RX_CTL) = 0b011; }
static int ps_captured(unsigned long b, unsigned char *f, unsigned *len, int ms) {   /* 1 captured, 0 timeout */
    for (int t = 0; t < ms; t += 1) {
        U32 st = PSR(b, PS_RX_STAT);
        if (st & 1) {
            *len = (st >> 16) & 0x1FFF;
            for (unsigned i = 0; i < *len; i += 4) { U32 w = PSR(b, PS_RXBUF + i); for (unsigned k = 0; k < 4 && i + k < *len; k++) f[i + k] = (unsigned char)(w >> (8 * k)); }
            return 1;
        }
        usleep(1000);
    }
    return 0;
}
static unsigned be32(const unsigned char *p) { return ((unsigned)p[0] << 24) | ((unsigned)p[1] << 16) | ((unsigned)p[2] << 8) | p[3]; }
static void put32(unsigned char *p, unsigned v) { p[0] = v >> 24; p[1] = v >> 16; p[2] = v >> 8; p[3] = v; }

/* ---- packets ---- */
static unsigned char txf[2048], rxf[4096];
typedef struct { unsigned len, eth_len, id_hi, id_lo, bucket, first; } pkt_t;
static int parse(const unsigned char *f, unsigned len, pkt_t *p) {          /* frontend-bound frame from the core */
    if (len < ETH_HDR + CANON_HDR) return 0;
    p->len = len; p->eth_len = ((unsigned)f[12] << 8) | f[13];
    const unsigned char *d = f + ETH_HDR;
    p->first = be32(d); p->bucket = be32(d + 4); p->id_hi = be32(d + 8); p->id_lo = be32(d + 12);
    return 1;
}
static unsigned build_request(unsigned char *f, unsigned bucket, unsigned id, unsigned pld_len, unsigned seed) {
    static const unsigned char dst[6] = { 0x02, 0, 0, 0, 0, 0x02 }, src[6] = { 0x02, 0, 0, 0, 0, 0x01 };
    unsigned n = CANON_HDR + pld_len;
    for (int i = 0; i < 6; i++) { f[i] = dst[i]; f[6 + i] = src[i]; }
    f[12] = n >> 8; f[13] = n & 0xFF;                                  /* 802.3 LENGTH */
    unsigned char *h = f + ETH_HDR;
    for (int i = 0; i < CANON_HDR; i++) h[i] = 0;
    put32(h + 0, pld_len); put32(h + 4, bucket); put32(h + 12, id);     /* ID: 64-bit, low word */
    /* REFERENCE (16..23) = 0, RESERVED (24..31) = 0 */
    unsigned x = seed * 2654435761u + 7u;
    for (int i = 32; i < 64; i++) { x = x * 1103515245u + 12345u; h[i] = (unsigned char)(x >> 16); }   /* KEY_COMMIT */
    for (unsigned i = 0; i < pld_len; i++) { x = x * 1103515245u + 12345u; h[CANON_HDR + i] = (unsigned char)(x >> 16); }
    unsigned total = ETH_HDR + n;
    for (int i = 0; i < 4; i++) f[total + i] = 0;                       /* pad in place of an FCS */
    return total + 4;
}

int main(void)
{
    Xil_ExceptionRegisterHandler(XIL_EXCEPTION_ID_SYNC_INT, sync_abort, NULL);
    xil_printf("\n\r*** VPK180 recomp interlock bring-up: sync/cert cadence, then canonical data across ***\n\r");
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

    /* ---- phase 1: core in, watch the frontend port from the first tick ---- */
    CTL_REG = CTL_PT; usleep(20000);
    PSR(PS_A, PS_RX_CTL) = 0b110; PSR(PS_B, PS_RX_CTL) = 0b110;     /* clear + zero counters */
    ps_arm(PS_B);
    unsigned t0 = now_ms();
    CTL_REG = CTL_CORE;                                                /* core released from reset now */
    xil_printf("phase 1: core switched in at t=0, listening on the frontend port (B) for 2.5 s\n\r");
    unsigned nsync = 0, ncert = 0, nother = 0, last_bucket = 0, last_t = 0, last_cert_t = 0, bad_seq = 0;
    pkt_t p;
    while (now_ms() - t0 < 2500) {
        unsigned len;
        if (!ps_captured(PS_B, rxf, &len, 300)) { xil_printf("  t=%u ms: nothing for 300 ms\n\r", now_ms() - t0); ps_arm(PS_B); continue; }
        unsigned t = now_ms() - t0;
        ps_arm(PS_B);
        if (!parse(rxf, len, &p)) { nother++; xil_printf("  t=%u ms: short frame %u B\n\r", t, len); continue; }
        if (p.id_hi == 0 && p.id_lo == 1 && p.eth_len == 64) {
            xil_printf("  t=%4u ms (+%3u): SYNC  bucket %u  first_arr 0x%08x  frame %u B\n\r", t, t - last_t, p.bucket, p.first, len);
            if (nsync && p.bucket != last_bucket + 1) bad_seq++;
            last_bucket = p.bucket; last_t = t; nsync++;
        } else if (p.id_hi == 0 && p.id_lo == 0 && p.eth_len == 224) {
            const unsigned char *c = rxf + ETH_HDR + 64;               /* certificate body: VERSION DEVICE BKT_START BKT_NUM NONCE ... */
            xil_printf("  t=%4u ms (+%3u): CERT  frame %u B  version %u device %u bkt_start %u bkt_num %u nonce %08x%08x%08x%08x\n\r",
                       t, t - last_cert_t, len, be32(c), be32(c + 4), be32(c + 8), be32(c + 12), be32(c + 16), be32(c + 20), be32(c + 24), be32(c + 28));
            last_cert_t = t; ncert++;
        } else {
            nother++; xil_printf("  t=%4u ms: other frame %u B, LENGTH %u, ID %08x%08x\n\r", t, len, p.eth_len, p.id_hi, p.id_lo);
        }
    }
    xil_printf("phase 1: %u syncs (%u out of sequence), %u certs, %u other; B captured %u; A RX_COUNT %u (expect 0)\n\r",
               nsync, bad_seq, ncert, nother, (unsigned)PSR(PS_B, PS_RX_COUNT), (unsigned)PSR(PS_A, PS_RX_COUNT));
    int ok1 = nsync >= 20 && bad_seq == 0 && ncert >= 2 && PSR(PS_A, PS_RX_COUNT) == 0;

    /* ---- phase 2: canonical requests stamped with the current bucket, forwarded to the enclosure ---- */
    xil_printf("phase 2: data across (B -> core -> cage 1 -> fibre -> cage 3 -> A)\n\r");
    static const unsigned plds[] = { 100, 300, 1000, 1436, 64 };
    unsigned id = 2, ok2 = 1;
    for (unsigned i = 0; i < sizeof plds / sizeof plds[0] + 1; i++) {
        int stale = (i == sizeof plds / sizeof plds[0]);
        unsigned pld = stale ? 100 : plds[i];
        unsigned len;
        ps_arm(PS_B);
        if (!ps_captured(PS_B, rxf, &len, 400) || !parse(rxf, len, &p) || p.id_lo != 1) { xil_printf("  no sync to stamp from\n\r"); ok2 = 0; break; }
        unsigned bucket = stale ? p.bucket : p.bucket + 1;              /* sync reports the bucket just closed */
        unsigned n = build_request(txf, bucket, id, pld, 500 + i);
        ps_arm(PS_A);
        ps_load(PS_B, txf, n);
        unsigned ts = now_ms();
        ps_send(PS_B, n);
        unsigned rlen = 0;
        int got = ps_captured(PS_A, rxf, &rlen, 400);
        unsigned want = ETH_HDR + CANON_HDR + pld;
        if (stale) {
            xil_printf("  stale bucket %u (current %u), ID %u: %s\n\r", bucket, bucket + 1, id, got ? "FORWARDED (should have been dropped)" : "dropped as expected");
            ok2 = ok2 && !got;
        } else {
            int same = got && rlen >= want;
            for (unsigned k = 0; same && k < want; k++) if (rxf[k] != txf[k]) same = 0;
            xil_printf("  bucket %u, ID %u, PLD_LEN %u: %s (captured %u B, %u ms after send)\n\r", bucket, id, pld,
                       !got ? "NOTHING at the enclosure side" : same ? "forwarded verbatim" : "forwarded but DIFFERENT", rlen, now_ms() - ts);
            ok2 = ok2 && same;
        }
        id += 2;
    }
    /* the next sync must report a real first-arrival for a bucket that carried our packet */
    ps_arm(PS_B);
    { unsigned len; if (ps_captured(PS_B, rxf, &len, 400) && parse(rxf, len, &p)) xil_printf("  next sync: bucket %u first_arr 0x%08x\n\r", p.bucket, p.first); }
    xil_printf("A RX_COUNT %u, drop flag %u\n\r", (unsigned)PSR(PS_A, PS_RX_COUNT), (unsigned)((STAT_REG >> 3) & 1));
    xil_printf("RESULT: phase 1 (sync/cert cadence) %s, phase 2 (data across) %s\n\r", ok1 ? "PASS" : "FAIL", ok2 ? "PASS" : "FAIL");
    CTL_REG = CTL_PT;
    return 0;
}
