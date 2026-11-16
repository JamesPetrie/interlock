/*******************************************************************************
 * Interlock Mi-V firmware — MPF200-ETH-SENSOR-BRIDGE-R2 variant.
 *
 * On this board the two Ethernet ports are SFP+ cages driven straight from the
 * PolarFire transceiver (PF_XCVR_ERM in PMA mode, CoreTSE in TBI mode).  There
 * is no VSC8575 copper PHY and no ZL30364 clock generator, so compared with the
 * MPF300-EVAL-KIT firmware (main.c) this build:
 *
 *   - skips configure_zl30364() and every VSC8575 register sequence,
 *   - initialises both CoreTSE MACs (same CSR values as AN4623's tse_init),
 *   - configures each MAC's internal PCS (MDIO address 18 for CORETSE_0,
 *     19 for CORETSE_1 — each on its own looped-back MDIO bus, so PCS n is
 *     reached through MAC n's MDIO master) for either SGMII or 1000BASE-X
 *     auto-negotiation and restarts AN,
 *   - prints PCS status over the UART once a second.
 *
 * PCS mode (CoreTSE v3.2 handbook §3.5): the TBI block is dual-mode and the
 * mode follows the AN Advertisement register (0x04) contents.
 *   SGMII (default)   reg 4 = 0x0001  -> use with 1000BASE-T copper SFP modules
 *                     (their internal PHY talks SGMII to the host; the copper
 *                     side auto-negotiates with the far NIC).
 *   1000BASE-X        reg 4 = 0x0020  -> full duplex, no pause; use with optical
 *                     SFP modules (clause 37 AN with the far end).
 * Build with -DSFP_MODE_1000BASEX for the optical variant (Makefile: MODE=basex).
 *
 * Every MDIO poll is bounded so a missing module can never hang the CPU.
 *
 * Host-side tests: `make test` (test/) compiles this file natively against a
 * mock of the two CoreTSE MDIO masters and their PCS slaves (-DHOST_TEST).
 *
 * Address map (unchanged from the kit): CoreAPB3 slots at 0x60000000 + n*0x1000:
 *   0 CORETSE_0, 1 CoreUARTapb, 2 CoreSPI (pins tied off on this board), 3 CORETSE_1.
 *
 * Derived from Microsemi/Microchip AN4623 (DG0799) iog_cdr/main.c; original
 * copyright header retained per the Microsemi licence:
 * (c) Copyright 2016-2017 Microsemi Corporation. All rights reserved.
 ******************************************************************************/

#include "drivers/CoreUARTapb/core_uart_apb.h"
#include "miv_rv32_hal/miv_rv32_hal.h"
#include "sample_hw_platform.h"

#define TSE0_BASEADDR       TSE_BASEADDR        /* 0x60000000, CoreAPB3 slot 0 */
#define TSE1_BASEADDR       0x60003000UL        /* CoreAPB3 slot 3 */

#define PCS0_MDIO_ADDR      18u                 /* CORETSE_0 MDIO_PHYID */
#define PCS1_MDIO_ADDR      19u                 /* CORETSE_1 MDIO_PHYID */

/* CoreTSE MAC CSRs (handbook §3.1) */
#define TSE_MAC_CFG1        0x000u
#define TSE_MAC_CFG2        0x004u
#define TSE_MDIO_CFG        0x020u
#define TSE_MDIO_CMD        0x024u
#define TSE_MDIO_ADDR       0x028u
#define TSE_MDIO_CTRL       0x02Cu
#define TSE_MDIO_STATUS     0x030u
#define TSE_MDIO_IND        0x034u

/* CoreTSE PCS (TBI) MDIO registers (handbook §3.5) */
#define PCS_REG_CONTROL     0x00u
#define PCS_REG_STATUS      0x01u
#define PCS_REG_AN_ADV      0x04u
#define PCS_REG_AN_LPA      0x05u
#define PCS_REG_EXT_STATUS  0x0Fu
#define PCS_REG_TBI_CTRL    0x11u

#define PCS_CTRL_RESET      0x8000u
#define PCS_CTRL_AN_ENABLE  0x1000u
#define PCS_CTRL_AN_RESTART 0x0200u
#define PCS_STAT_AN_DONE    0x0020u
#define PCS_STAT_LINK       0x0004u

#ifdef SFP_MODE_1000BASEX
#define PCS_AN_ADV_VALUE    0x0020u             /* 1000BASE-X: full duplex */
#define PCS_MODE_NAME       "1000BASE-X"
#else
#define PCS_AN_ADV_VALUE    0x0001u             /* SGMII: TBI config word */
#define PCS_MODE_NAME       "SGMII"
#endif

#define MDIO_BUSY_TIMEOUT   200000u             /* ~ms at 80 MHz; one MDIO frame is ~25 us */
#define AN_WAIT_POLLS       400u                /* x ~10 ms = ~4 s per port at boot */

UART_instance_t g_uart;

/*--------------------------- UART print helpers -----------------------------*/

static void uart_print(const char *s)
{
    UART_polled_tx_string(&g_uart, (const uint8_t *)s);
}

static void uart_print_hex16(uint16_t v)
{
    static const char hex[] = "0123456789ABCDEF";
    char buf[7];
    buf[0] = '0';
    buf[1] = 'x';
    buf[2] = hex[(v >> 12) & 0xF];
    buf[3] = hex[(v >>  8) & 0xF];
    buf[4] = hex[(v >>  4) & 0xF];
    buf[5] = hex[(v >>  0) & 0xF];
    buf[6] = '\0';
    uart_print(buf);
}

static void uart_print_dec(uint32_t v)
{
    char buf[11];
    int i = 10;
    buf[i--] = '\0';
    if (v == 0) {
        buf[i--] = '0';
    } else {
        while (v > 0) { buf[i--] = (char)('0' + (v % 10)); v /= 10; }
    }
    uart_print(&buf[i + 1]);
}

#ifdef HOST_TEST
/* test/ compiles this file natively against a CoreTSE/PCS mock (test/mock_tse.c):
 * register accesses and the busy-wait are supplied by the mock and the main
 * loop ends when the harness says so.  Nothing here changes the target build. */
void     reg_write(uint32_t base, uint32_t off, uint32_t val);
uint32_t reg_read(uint32_t base, uint32_t off);
void     spin_delay(uint32_t loops);
int      host_keep_running(void);
#define  FW_KEEP_RUNNING()  host_keep_running()
#else
#define  FW_KEEP_RUNNING()  1

static void spin_delay(uint32_t loops)
{
    for (volatile uint32_t i = 0; i < loops; i++) { }
}

static inline void reg_write(uint32_t base, uint32_t off, uint32_t val)
{
    *(volatile uint32_t *)(base + off) = val;
}

static inline uint32_t reg_read(uint32_t base, uint32_t off)
{
    return *(volatile uint32_t *)(base + off);
}
#endif /* HOST_TEST */

/*--------------------------- MDIO helpers (bounded) -------------------------*/

static int mdio_wait_idle(uint32_t tse_base)
{
    uint32_t n = MDIO_BUSY_TIMEOUT;
    while ((reg_read(tse_base, TSE_MDIO_IND) & 0x7u) != 0u) {
        if (--n == 0u) { return -1; }
    }
    return 0;
}

static uint16_t mdio_read(uint32_t tse_base, uint8_t phy, uint8_t reg)
{
    reg_write(tse_base, TSE_MDIO_ADDR, ((uint32_t)phy << 8) | (reg & 0x1Fu));
    reg_write(tse_base, TSE_MDIO_CMD, 0x1u);
    (void)mdio_wait_idle(tse_base);
    uint16_t v = (uint16_t)reg_read(tse_base, TSE_MDIO_STATUS);
    reg_write(tse_base, TSE_MDIO_CMD, 0x0u);
    return v;
}

static void mdio_write(uint32_t tse_base, uint8_t phy, uint8_t reg, uint16_t val)
{
    reg_write(tse_base, TSE_MDIO_ADDR, ((uint32_t)phy << 8) | (reg & 0x1Fu));
    reg_write(tse_base, TSE_MDIO_CTRL, val);
    (void)mdio_wait_idle(tse_base);
}

/*--------------------------- MAC init ---------------------------------------*/

/* Same register sequence as AN4623's tse_init(): 1000 Mb/s full duplex,
 * SGMII/TBI interface mode, address filtering defaults, MDIO clock divider. */
static void tse_mac_init(uint32_t base)
{
    reg_write(base, TSE_MAC_CFG1, 0x00000005u);
    reg_write(base, TSE_MAC_CFG2, 0x00007201u);
    reg_write(base, 0x040u, 0x6060603Cu);
    reg_write(base, 0x044u, 0xB1C00000u);
    reg_write(base, 0x048u, 0x0000FF00u);
    reg_write(base, 0x04Cu, 0x0FFF0000u);
    reg_write(base, 0x050u, 0x04000180u);
    reg_write(base, 0x054u, 0x0680FFFFu);
    reg_write(base, 0x058u, 0x00000000u);
    reg_write(base, 0x05Cu, 0x0007FFFFu);
    reg_write(base, 0x1C0u, 0x0000000Fu);
    reg_write(base, TSE_MDIO_CFG, 0x0007u);
    (void)reg_read(base, TSE_MDIO_CFG);
}

/*--------------------------- PCS (TBI) init ---------------------------------*/

static void pcs_init(uint32_t tse_base, uint8_t pcs_addr, const char *label)
{
    uint16_t r;

    uart_print("[pcs] ");
    uart_print(label);
    uart_print(" addr=");
    uart_print_dec(pcs_addr);

    /* Sanity: the internal slave must answer (status reg has fixed 1s in
     * bits 8, 6, 3, 0 and bit 15..9 reserved-0, so 0xFFFF means "silent"). */
    r = mdio_read(tse_base, pcs_addr, PCS_REG_STATUS);
    uart_print(" status=");
    uart_print_hex16(r);
    if (r == 0xFFFFu) {
        uart_print(" NO RESPONSE (MDIO loopback broken?)\r\n");
        return;
    }

    /* Mode select + advertisement (handbook §3.5, reg 0x04). */
    mdio_write(tse_base, pcs_addr, PCS_REG_AN_ADV, PCS_AN_ADV_VALUE);

    /* Enable and restart auto-negotiation (reg 0 bits 12 and 9). */
    r = mdio_read(tse_base, pcs_addr, PCS_REG_CONTROL);
    r |= PCS_CTRL_AN_ENABLE;
    mdio_write(tse_base, pcs_addr, PCS_REG_CONTROL, r);
    mdio_write(tse_base, pcs_addr, PCS_REG_CONTROL, (uint16_t)(r | PCS_CTRL_AN_RESTART));

    uart_print(" mode=" PCS_MODE_NAME " adv=");
    uart_print_hex16(mdio_read(tse_base, pcs_addr, PCS_REG_AN_ADV));
    uart_print(" tbi_ctrl=");
    uart_print_hex16(mdio_read(tse_base, pcs_addr, PCS_REG_TBI_CTRL));
    uart_print("\r\n");
}

static void pcs_wait_an(uint32_t tse_base, uint8_t pcs_addr, const char *label)
{
    uint16_t st = 0;
    uint32_t polls = AN_WAIT_POLLS;
    do {
        st = mdio_read(tse_base, pcs_addr, PCS_REG_STATUS);
        if (st & PCS_STAT_AN_DONE) { break; }
        spin_delay(100000u);                     /* ~10 ms */
    } while (--polls != 0u);

    uart_print("[an]  ");
    uart_print(label);
    uart_print(" status=");
    uart_print_hex16(st);
    uart_print(" link=");
    uart_print_dec((st >> 2) & 1u);
    uart_print(" an_done=");
    uart_print_dec((st >> 5) & 1u);
    uart_print(" lpa=");
    uart_print_hex16(mdio_read(tse_base, pcs_addr, PCS_REG_AN_LPA));
    uart_print(polls ? "\r\n" : " (timeout)\r\n");
}

/*-------------------------- main ------------------------------------------*/

int main(void)
{
    UART_init(&g_uart, COREUARTAPB0_BASE_ADDR,
              BAUD_VALUE_115200, (DATA_8_BITS | NO_PARITY));
    uart_print("\r\n\r\n[boot] interlock firmware mpf200-esb (SFP via XCVR, PCS " PCS_MODE_NAME ") starting\r\n");

    uart_print("[boot] tse_mac_init MAC0 @0x60000000, MAC1 @0x60003000\r\n");
    tse_mac_init(TSE0_BASEADDR);
    tse_mac_init(TSE1_BASEADDR);

    /* Give the transceiver lanes a moment after reset release before AN. */
    spin_delay(1000000u);

    pcs_init(TSE0_BASEADDR, PCS0_MDIO_ADDR, "port0/SFP1");
    pcs_init(TSE1_BASEADDR, PCS1_MDIO_ADDR, "port1/SFP2");

    pcs_wait_an(TSE0_BASEADDR, PCS0_MDIO_ADDR, "port0/SFP1");
    pcs_wait_an(TSE1_BASEADDR, PCS1_MDIO_ADDR, "port1/SFP2");

    uart_print("[boot] init complete — polling both ports\r\n");
    uint32_t tick = 0;
    while (FW_KEEP_RUNNING()) {
        spin_delay(8000000u);                    /* ~1 s */
        uint16_t s0 = mdio_read(TSE0_BASEADDR, PCS0_MDIO_ADDR, PCS_REG_STATUS);
        uint16_t s1 = mdio_read(TSE1_BASEADDR, PCS1_MDIO_ADDR, PCS_REG_STATUS);
        uart_print("[poll t=");
        uart_print_dec(tick++);
        uart_print("] p0s=");
        uart_print_hex16(s0);
        uart_print(" p1s=");
        uart_print_hex16(s1);
        uart_print(" (link bits: ");
        uart_print_dec((s0 >> 2) & 1u);
        uart_print_dec((s1 >> 2) & 1u);
        uart_print(" an_done: ");
        uart_print_dec((s0 >> 5) & 1u);
        uart_print_dec((s1 >> 5) & 1u);
        uart_print(")\r\n");

        /* A port whose AN never completed keeps retrying: kick a restart so a
         * module hot-plugged after boot still links up. */
        if (!(s0 & PCS_STAT_AN_DONE)) {
            mdio_write(TSE0_BASEADDR, PCS0_MDIO_ADDR, PCS_REG_CONTROL,
                       (uint16_t)(PCS_CTRL_AN_ENABLE | PCS_CTRL_AN_RESTART));
        }
        if (!(s1 & PCS_STAT_AN_DONE)) {
            mdio_write(TSE1_BASEADDR, PCS1_MDIO_ADDR, PCS_REG_CONTROL,
                       (uint16_t)(PCS_CTRL_AN_ENABLE | PCS_CTRL_AN_RESTART));
        }
    }
    return 0;                                    /* not reached on the target */
}
