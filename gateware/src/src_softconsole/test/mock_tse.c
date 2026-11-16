#include "mock_tse.h"
#include "drivers/CoreUARTapb/core_uart_apb.h"
#include <string.h>
#include <stdio.h>

mock_tse_t g_tse[2];
uint32_t   g_bad_base_accesses;
uint32_t   g_spin_calls;
int        g_run_iterations;
char       g_uart_buf[1 << 16];
size_t     g_uart_len;
int        g_uart_inited;

/* CoreTSE MAC CSR offsets (handbook §3.1) */
#define MIIMCFG  0x020u
#define MIIMCOM  0x024u
#define MIIMADD  0x028u
#define MIIMCON  0x02Cu
#define MIIMSTAT 0x030u
#define MIIMIND  0x034u

/* PCS (handbook §3.5) */
#define PCS_CTRL      0x00u
#define PCS_STATUS    0x01u
#define PCS_ADV       0x04u
#define PCS_LPA       0x05u
#define PCS_EXT_STAT  0x0Fu
#define PCS_TBI_CTRL  0x11u
#define CTRL_RESET    0x8000u
#define CTRL_AN_EN    0x1000u
#define CTRL_AN_RST   0x0200u
#define STAT_FIXED    0x0149u   /* bits 8, 6, 3, 0 read as 1 */
#define STAT_AN_DONE  0x0020u
#define STAT_LINK     0x0004u

void mock_reset(void)
{
    memset(g_tse, 0, sizeof g_tse);
    g_tse[0].base = 0x60000000u; g_tse[0].pcs_addr = 18u;
    g_tse[1].base = 0x60003000u; g_tse[1].pcs_addr = 19u;
    for (int i = 0; i < 2; i++) {
        g_tse[i].module_present   = 1;
        g_tse[i].an_polls_to_done = 3;
        g_tse[i].lpa_value        = 0xD801u;    /* SGMII: link up, FD, 1000 Mb/s, ack */
        g_tse[i].busy_polls       = 2;
        g_tse[i].pcs[PCS_EXT_STAT] = 0xA000u;   /* 1000BASE-X FD + 1000BASE-T FD capable */
    }
    g_bad_base_accesses = 0;
    g_spin_calls = 0;
    g_run_iterations = 1;
    g_uart_len = 0; g_uart_buf[0] = '\0';
    g_uart_inited = 0;
}

static mock_tse_t *find(uint32_t base)
{
    for (int i = 0; i < 2; i++) if (g_tse[i].base == base) return &g_tse[i];
    g_bad_base_accesses++;
    return NULL;
}

static uint16_t pcs_read(mock_tse_t *t, uint8_t reg)
{
    if (reg == PCS_STATUS) {
        t->status_reads++;
        if (t->an_in_progress && t->module_present) {
            if (t->an_countdown > 0) t->an_countdown--;
            if (t->an_countdown == 0) {
                t->an_in_progress = 0; t->an_done = 1; t->link = 1;
                t->pcs[PCS_LPA] = t->lpa_value;
            }
        }
        return (uint16_t)(STAT_FIXED | (t->an_done ? STAT_AN_DONE : 0u) | (t->link ? STAT_LINK : 0u));
    }
    return t->pcs[reg & 0x1Fu];
}

static void pcs_write(mock_tse_t *t, uint8_t reg, uint16_t val)
{
    switch (reg) {
    case PCS_CTRL:
        if (val & CTRL_RESET) {                  /* self-clearing reset */
            t->pcs_resets++;
            t->an_done = 0; t->link = 0; t->an_in_progress = 0;
            val &= (uint16_t)~CTRL_RESET;
        }
        if (val & CTRL_AN_RST) {                 /* self-clearing restart, needs AN enabled */
            if (val & CTRL_AN_EN) {
                t->an_restarts++;
                t->an_done = 0; t->link = 0;
                t->an_in_progress = 1; t->an_countdown = t->an_polls_to_done;
            } else {
                t->restarts_without_enable++;
            }
            val &= (uint16_t)~CTRL_AN_RST;
        }
        t->pcs[PCS_CTRL] = val;
        break;
    case PCS_ADV:
        t->adv_writes++; t->last_adv = val; t->pcs[PCS_ADV] = val;
        break;
    default:
        t->pcs[reg & 0x1Fu] = val;
    }
}

void reg_write(uint32_t base, uint32_t off, uint32_t val)
{
    mock_tse_t *t = find(base);
    if (!t) return;
    t->writes++;
    uint32_t idx = (off & 0x1FFu) / 4u;
    uint8_t phy = (uint8_t)((t->csr[MIIMADD / 4] >> 8) & 0x1Fu);
    uint8_t reg = (uint8_t)(t->csr[MIIMADD / 4] & 0x1Fu);
    switch (off) {
    case MIIMADD:
        t->csr[idx] = val; break;
    case MIIMCOM:
        if ((val & 1u) && !(t->csr[idx] & 1u)) {           /* 0->1: one read cycle */
            t->mdio_reads++;
            if (phy != t->pcs_addr) { t->foreign_phy_accesses++; t->mdio_result = 0xFFFFu; }
            else if (t->pcs_silent)  { t->mdio_result = 0xFFFFu; }
            else                     { t->mdio_result = pcs_read(t, reg); }
            t->busy_left = t->busy_polls; t->read_cycle = 1;
        }
        t->csr[idx] = val; break;
    case MIIMCON:                                           /* write cycle */
        t->mdio_writes++;
        if (phy != t->pcs_addr) t->foreign_phy_accesses++;
        else pcs_write(t, reg, (uint16_t)val);              /* a silent slave still "hears" writes; the test checks none are issued */
        t->busy_left = t->busy_polls; t->read_cycle = 0;
        t->csr[idx] = val; break;
    default:
        if (t->mac_log_n < MOCK_MAC_LOG_MAX) {
            t->mac_log_off[t->mac_log_n] = off; t->mac_log_val[t->mac_log_n] = val; t->mac_log_n++;
        }
        t->csr[idx] = val;
    }
}

uint32_t reg_read(uint32_t base, uint32_t off)
{
    mock_tse_t *t = find(base);
    if (!t) return 0xFFFFFFFFu;
    t->reads++;
    switch (off) {
    case MIIMIND:
        if (t->busy_forever) return 0x5u;                   /* BUSY | NOT_VALID */
        if (t->busy_left > 0) { t->busy_left--; return t->read_cycle ? 0x5u : 0x1u; }
        return 0u;
    case MIIMSTAT:
        return t->mdio_result;
    default:
        return t->csr[(off & 0x1FFu) / 4u];
    }
}

void spin_delay(uint32_t loops) { (void)loops; g_spin_calls++; }

int host_keep_running(void) { return g_run_iterations-- > 0; }

void UART_init(UART_instance_t *u, addr_t base, uint16_t baud, uint8_t cfg)
{
    u->base_address = base; u->status = 0;
    g_uart_inited = (base == 0x60001000u && baud == 42u && cfg == 0x01u);  /* 80 MHz/(16*115200)-1 = 42 */
}

void UART_polled_tx_string(UART_instance_t *u, const uint8_t *s)
{
    (void)u;
    size_t n = strlen((const char *)s);
    if (g_uart_len + n + 1 < sizeof g_uart_buf) {
        memcpy(g_uart_buf + g_uart_len, s, n); g_uart_len += n; g_uart_buf[g_uart_len] = '\0';
    }
}
