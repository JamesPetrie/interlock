/* Mock of the two CoreTSE MACs' MDIO-management blocks and the TBI/PCS slave
 * behind each one, for running main_mpf200.c natively (-DHOST_TEST).
 *
 * Register behaviour follows the CoreTSE v3.2 handbook: MAC CSRs §3.1
 * (MDIO Mgmt 0x020-0x034), PCS registers §3.5 (control 0x00, status 0x01,
 * AN advertisement 0x04, link-partner ability 0x05, extended status 0x0F,
 * TBI control 0x11). */
#ifndef MOCK_TSE_H
#define MOCK_TSE_H
#include <stdint.h>
#include <stddef.h>

#define MOCK_MAC_LOG_MAX 64

typedef struct {
    /* identity */
    uint32_t base;
    uint8_t  pcs_addr;              /* MDIO_PHYID of this MAC's internal PCS */

    /* knobs (set before running the firmware) */
    int      module_present;        /* 1: AN completes after an_polls_to_done status reads */
    uint32_t an_polls_to_done;
    uint16_t lpa_value;             /* reported in reg 5 once AN completes */
    int      pcs_silent;            /* 1: every MDIO read returns 0xFFFF (slave not responding) */
    uint32_t busy_polls;            /* MIIMIND reports BUSY this many polls after each cycle */
    int      busy_forever;          /* 1: MIIMIND never goes idle */

    /* state */
    uint32_t csr[0x200 / 4];
    uint16_t pcs[32];
    uint32_t busy_left;
    int      read_cycle;
    uint16_t mdio_result;
    int      an_done, link, an_in_progress;
    uint32_t an_countdown;

    /* observations */
    uint32_t reads, writes;
    uint32_t mdio_reads, mdio_writes;
    uint32_t status_reads;
    uint32_t an_restarts;           /* control writes with AN_ENABLE|AN_RESTART */
    uint32_t restarts_without_enable;
    uint32_t adv_writes;
    uint16_t last_adv;
    uint32_t pcs_resets;
    uint32_t foreign_phy_accesses;  /* MDIO cycles addressed to a PHY other than pcs_addr */
    uint32_t mac_log_off[MOCK_MAC_LOG_MAX];
    uint32_t mac_log_val[MOCK_MAC_LOG_MAX];
    int      mac_log_n;             /* non-MDIO CSR writes, in order */
} mock_tse_t;

extern mock_tse_t g_tse[2];           /* [0] base 0x60000000 / PCS 18, [1] base 0x60003000 / PCS 19 */
extern uint32_t   g_bad_base_accesses;/* accesses to any other base */
extern uint32_t   g_spin_calls;
extern int        g_run_iterations;   /* main-loop iterations host_keep_running() allows */
extern char       g_uart_buf[1 << 16];
extern size_t     g_uart_len;
extern int        g_uart_inited;

void mock_reset(void);                /* defaults: both modules present, AN done after 3 polls */

#endif
