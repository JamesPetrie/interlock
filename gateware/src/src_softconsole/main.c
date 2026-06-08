/*******************************************************************************
 * Interlock firmware — iteration 2: port-1 bring-up, mirrored from port-0.
 *
 * Adapted from Microsemi/Microchip AN4623 (DG0799) iog_cdr/main.c.
 *
 * Strategy for risk reduction: the port-0 init functions are LEFT UNCHANGED
 * (verbatim from AN4623 — we know they work). Port-1 init functions are
 * near-verbatim copies of the port-0 functions, with two constants changed:
 *   - All `0x1C..` MDIO encoded addresses (PHY 28 = copper-side of port 0)
 *     become `0x1D..` (PHY 29 = copper-side of port 1).
 *   - All `0x12..` MDIO encoded addresses (PHY 18 = SGMII-side of port 0)
 *     become `0x13..` (PHY 19 = SGMII-side of port 1).
 *   - MAC register writes for CoreTSE_1 go to TSE1_BASEADDR (0x60003000)
 *     instead of TSE_BASEADDR (0x60000000).
 * MDIO transactions still go through CoreTSE_0's MDIO master (TSE_BASEADDR
 * for the MDIO command/status registers) because that's the only MDIO master
 * physically wired to the VSC8575. The encoded PHY address routes within the
 * chip.
 *
 * Port-1 PHY addresses (29 and 19) are best-guess by +1 analogy with port 0.
 * The MDIO scan at boot will confirm. If addresses differ, edit the four
 * `PHY1_*` defines below and reflash.
 *
 * Defensive change in port-1 functions only: the AN4623 polling loops are
 * unbounded `while(1)`; in port-1 versions they have a bounded timeout so
 * a missing/unresponsive PHY logs an error instead of hanging the CPU.
 *
 * Original copyright header retained per Microsemi licence.
 */
/*******************************************************************************
 * (c) Copyright 2016-2017 Microsemi Corporation. All rights reserved.
 *
 *  Simple IOD CDR 1G loop back example program.
 */

#include "drivers/CoreUARTapb/core_uart_apb.h"
#include "miv_rv32_hal/miv_rv32_hal.h"
#include "sample_hw_platform.h"

extern void configure_zl30364(void);

/*
 * Port-1 mapping. CoreTSE_1 sits at CoreAPB3 slot 3.
 * Slot stride is 0x1000 starting at 0x60000000 (slot 0 = CoreTSE_0,
 * slot 1 = UART, slot 2 = SPI, slot 3 = CoreTSE_1).
 */
#define TSE1_BASEADDR       0x60003000UL

/* MDIO addresses for the two halves of each port's PHY block on the VSC8575.
 * Port 0 values verified in the AN4623 firmware. Port 1 values are +1
 * by analogy; MDIO scan will confirm. */
#define PHY0_COPPER_ADDR    28      /* encodes as 0x1C in MDIO command */
#define PHY0_SGMII_ADDR     18      /* encodes as 0x12 */
#define PHY1_COPPER_ADDR    29      /* encodes as 0x1D — GUESSED */
#define PHY1_SGMII_ADDR     19      /* encodes as 0x13 — GUESSED */

#define MDIO_POLL_TIMEOUT_LOOPS   2000000u   /* ~few seconds at 80 MHz */

UART_instance_t g_uart;
extern void delay(uint32_t div);

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

static void uart_print_hex32(uint32_t v)
{
    uart_print_hex16((uint16_t)(v >> 16));
    uart_print_hex16((uint16_t)v);
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

/*--------------------------- MAC register diagnostics -----------------------*/
/* Raw APB read of a CoreTSE MAC register. base = TSE_BASEADDR / TSE1_BASEADDR */
static uint32_t mac_rd(uint32_t base, uint32_t off)
{
    return *(volatile uint32_t *)(base + off);
}

/* Config readback — verify the MAC is set up as expected.
 * CFG1(0x00): b0 TX_EN, b1 SYNC_TX, b2 RX_EN, b3 SYNC_RX, b8 LOOPBACK, b31 SOFT_RST
 * CFG2(0x04): b0 FDX, b1 CRC_EN(append FCS), b2 PAD/CRC, b8:9 iface mode */
static void dump_mac_cfg(uint32_t base, const char *label)
{
    uart_print("[maccfg] "); uart_print(label);
    uart_print(" CFG1="); uart_print_hex32(mac_rd(base, 0x00));
    uart_print(" CFG2="); uart_print_hex32(mac_rd(base, 0x04));
    uart_print(" IPG="); uart_print_hex32(mac_rd(base, 0x08));
    uart_print(" MAXFR="); uart_print_hex32(mac_rd(base, 0x10));
    uart_print(" FIFO0(48)="); uart_print_hex32(mac_rd(base, 0x48));
    uart_print(" FIFO2(50)="); uart_print_hex32(mac_rd(base, 0x50));
    uart_print(" FIFO5(5C)="); uart_print_hex32(mac_rd(base, 0x5C));
    uart_print("\r\n");
}

/* RMON statistics dump (0x80..0x17C). Prints only NONZERO regs as off=val so
 * we can spot which counters grow while streaming (RX pkt count on the ingress
 * MAC, TX pkt count on the egress MAC, plus error counters: FCS/align/overflow/
 * undersize/fragment/drop on RX, and underrun/drop on TX). Counters that move
 * when frames flow localize exactly where the bridge stalls. */
static void dump_mac_stats(uint32_t base, const char *label)
{
    uart_print("[macstat] "); uart_print(label); uart_print(":");
    for (uint32_t off = 0x80; off <= 0x17C; off += 4) {
        uint32_t v = mac_rd(base, off);
        if (v != 0u) {
            uart_print(" "); uart_print_hex16((uint16_t)off);
            uart_print("="); uart_print_hex32(v);
        }
    }
    uart_print("\r\n");
}

/*--------------------------- MDIO helpers -----------------------------------*/

static inline uint16_t mdio_addr(uint8_t phy, uint8_t reg)
{
    return (uint16_t)(((uint16_t)phy << 8) | (reg & 0x1F));
}

static uint16_t mdio_read(uint32_t tse_base, uint8_t phy, uint8_t reg)
{
    *(volatile unsigned int *)(tse_base + 0x028) = mdio_addr(phy, reg);
    *(volatile unsigned int *)(tse_base + 0x024) = 0x1;
    while ((*(volatile unsigned int *)(tse_base + 0x034)) != 0) { }
    uint16_t v = (uint16_t) *(volatile unsigned int *)(tse_base + 0x030);
    *(volatile unsigned int *)(tse_base + 0x024) = 0x0;
    return v;
}

static void mdio_write(uint32_t tse_base, uint8_t phy, uint8_t reg, uint16_t val)
{
    *(volatile unsigned int *)(tse_base + 0x028) = mdio_addr(phy, reg);
    *(volatile unsigned int *)(tse_base + 0x02C) = val;
    while ((*(volatile unsigned int *)(tse_base + 0x034)) != 0) { }
}

/* Verbose MDIO read: bounded timeout + indicator-bit capture.
 * Existing mdio_read() blocks forever if indicator never clears.  This
 * version exits after ~100k poll cycles and snapshots the BUSY/SCANNING/
 * NOT_VALID bits of CSR 0x034 at exit so we can distinguish "clean read
 * that returned 0xFFFF" (bus pull-up, no slave responding) from "master
 * timed out internally" (NOT_VALID set).  See CoreTSE doc §3.1 reg 0x034. */
static uint16_t mdio_read_v(uint32_t tse_base, uint8_t phy, uint8_t reg,
                             uint32_t *ind_out, uint32_t *cycles_out)
{
    *(volatile unsigned int *)(tse_base + 0x028) = mdio_addr(phy, reg);
    *(volatile unsigned int *)(tse_base + 0x024) = 0x1;

    uint32_t cycles = 0;
    uint32_t ind;
    do {
        ind = *(volatile unsigned int *)(tse_base + 0x034) & 0x7;
        if (ind == 0) break;
        cycles++;
    } while (cycles < 100000);

    *ind_out = ind;
    *cycles_out = cycles;

    uint16_t v = (uint16_t) *(volatile unsigned int *)(tse_base + 0x030);
    *(volatile unsigned int *)(tse_base + 0x024) = 0x0;
    return v;
}

/* Full PCS register dump for a single MDIO address through a specific
 * CoreTSE master.  In iter-4 we have TWO independent MDIO buses, so the
 * tse_base parameter selects which master's bus the dump reads from. */
static void dump_pcs(uint32_t tse_base, const char *label, uint8_t phy)
{
    uint32_t ind, cyc;
    uint16_t r;

    uart_print("[pcs] ");
    uart_print(label);
    uart_print(" addr=");
    uart_print_dec(phy);

    r = mdio_read_v(tse_base, phy, 0x00, &ind, &cyc);
    uart_print(" Ctl=");   uart_print_hex16(r);
    uart_print("/i=");     uart_print_hex16((uint16_t)ind);
    uart_print("/c=");     uart_print_hex16((uint16_t)cyc);

    r = mdio_read_v(tse_base, phy, 0x01, &ind, &cyc);
    uart_print(" Sts=");   uart_print_hex16(r);
    uart_print("/i=");     uart_print_hex16((uint16_t)ind);

    r = mdio_read_v(tse_base, phy, 0x0F, &ind, &cyc);
    uart_print(" ExtSts="); uart_print_hex16(r);
    uart_print("/i=");      uart_print_hex16((uint16_t)ind);

    r = mdio_read_v(tse_base, phy, 0x11, &ind, &cyc);
    uart_print(" TBICtl="); uart_print_hex16(r);
    uart_print("/i=");      uart_print_hex16((uint16_t)ind);

    uart_print("\r\n");
}

/* VSC8575 package-level SGMII MAC interface configuration.
 *
 * Per VMDS-10457 §3.1.1.1 / §3.2 / §4.7.7, the four-port SGMII MAC
 * interface needs explicit configuration via:
 *   - Register 19G (bits 15:14) = 00  → SGMII MAC mode (global)
 *   - Register 18G = 0x80F0           → microprocessor command to enable
 *                                       four-port SGMII (chip's internal
 *                                       8051 executes; up to 25 ms)
 *   - Per-port register 23 bit 12 = 0 → SGMII MAC type (super-sticky)
 *   - Per-port register 0  bit 15 = 1 → software reset to commit
 *
 * The "G" suffix maps to the GPIO register page selected by writing
 * 0x0010 to register 31 (VMDS-10457 §4.2.28 Table 68).
 *
 * Without this sequence, ports 1-3 may be in a default state where their
 * SGMII outputs aren't fully active, even though the copper PHYs link
 * up.  The single-port AN4623 reference design skips this because it
 * doesn't use ports 1-3 — we have to do it ourselves for multi-port.
 *
 * After the 18G write, we poll bit 15 (self-clearing on completion) and
 * check bit 14: 1 = error (squelch patch not loaded — would indicate we
 * need to load the chip's firmware patch, which we don't currently do). */
static void vsc8575_init(void)
{
    uart_print("[vsc] VSC8575 4-port SGMII config starting\r\n");

    /* Switch to GPIO page (= "G" suffix) — Table 68 says write 0x0010. */
    mdio_write(TSE_BASEADDR, 28, 31, 0x0010);

    /* 19G bits 15:14 = 00 (SGMII MAC mode).  19G = MAC Mode and Fast Link
     * Configuration register, Table 119.  Default should already be 00 but
     * explicit write is good hygiene. */
    uint16_t reg19g = mdio_read(TSE_BASEADDR, 28, 19);
    uart_print("[vsc] 19G before=0x");
    uart_print_hex16(reg19g);
    reg19g &= ~0xC000u;
    mdio_write(TSE_BASEADDR, 28, 19, reg19g);
    uart_print(" after=0x");
    uart_print_hex16(mdio_read(TSE_BASEADDR, 28, 19));
    uart_print("\r\n");

    /* 18G = 0x80F0 (Enable four MAC SGMII ports).  This is a command
     * processed by the chip's internal 8051.  Per §4.7.7 we should poll
     * bit 15 (self-clears) and check bit 14 (error flag). */
    mdio_write(TSE_BASEADDR, 28, 18, 0x80F0);

    /* Spec says up to 25 ms.  PCLK is 50 MHz → 25 ms ≈ 1.25M cycles.
     * Use 5M as safety margin. */
    uint32_t cycles = 0;
    uint16_t reg18g;
    do {
        reg18g = mdio_read(TSE_BASEADDR, 28, 18);
        cycles++;
    } while ((reg18g & 0x8000u) && cycles < 5000000);

    uart_print("[vsc] 18G post-cmd=0x");
    uart_print_hex16(reg18g);
    uart_print(" cycles=0x");
    uart_print_hex32(cycles);
    if (reg18g & 0x8000u) uart_print(" TIMEOUT");
    if (reg18g & 0x4000u) uart_print(" ERROR(squelch-patch-missing)");
    uart_print("\r\n");

    /* Back to standard page. */
    mdio_write(TSE_BASEADDR, 28, 31, 0x0000);

    /* Per-port: register 23 bit 12 = 0 → SGMII MAC type (vs 1000BASE-X).
     * Per §4.2.21 Table 61, this is a super-sticky bit that needs a SW
     * reset to commit.  Then software reset (reg 0 bit 15) per port. */
    for (uint8_t phy = 28; phy <= 31; phy++) {
        uint16_t reg23 = mdio_read(TSE_BASEADDR, phy, 23);
        reg23 &= ~(1u << 12);
        mdio_write(TSE_BASEADDR, phy, 23, reg23);

        uint16_t reg0 = mdio_read(TSE_BASEADDR, phy, 0);
        mdio_write(TSE_BASEADDR, phy, 0, reg0 | 0x8000u);

        /* §4.2.1: wait at least 1 µs after software reset before next
         * SMI access.  PCLK = 50 MHz → 1 µs = 50 cycles.  Pad to 1000. */
        for (volatile uint32_t i = 0; i < 1000; i++) { }
    }

    uart_print("[vsc] VSC8575 init done\r\n");
}

/*--------------------------- MDIO bus scan ----------------------------------*/

static void mdio_scan(uint32_t tse_base)
{
    uart_print("[scan] sweeping MDIO 0..31 via TSE @ ");
    uart_print_hex32(tse_base);
    uart_print("\r\n");
    for (uint8_t addr = 0; addr < 32; addr++) {
        uint16_t id1 = mdio_read(tse_base, addr, 2);
        uint16_t id2 = mdio_read(tse_base, addr, 3);
        if (id1 != 0xFFFF && (id1 != 0x0000 || id2 != 0x0000)) {
            uart_print("[scan]   addr=");
            uart_print_dec(addr);
            uart_print("  ID1=");
            uart_print_hex16(id1);
            uart_print("  ID2=");
            uart_print_hex16(id2);
            uart_print("\r\n");
        }
    }
    uart_print("[scan] done\r\n");
}

/*--------------------------- Status dump helper -----------------------------*/

static void dump_phy_status(uint32_t tse_base, uint8_t phy_addr, const char *label)
{
    uint16_t st = mdio_read(tse_base, phy_addr, 1);
    uart_print("[state] ");
    uart_print(label);
    uart_print(" PHY ");
    uart_print_dec(phy_addr);
    uart_print(" status=");
    uart_print_hex16(st);
    uart_print(" link=");
    uart_print_dec((st >> 2) & 1);
    uart_print(" aneg_done=");
    uart_print_dec((st >> 5) & 1);
    uart_print("\r\n");
}

/*======================================================================*/
/* PORT 0 INIT — unchanged from AN4623 / iteration 1.                   */
/* These functions are KNOWN to work. They drive PHY 28 (copper) and    */
/* PHY 18 (SGMII), with MAC at TSE_BASEADDR.                            */
/*======================================================================*/

void Phy_advertise(void)
{
  uint32_t phy_reg = 0xFFFF;

  *(volatile unsigned int *) (TSE_BASEADDR + 0x028) = 0x1C04;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x1;
  while ((*(volatile unsigned int *) (TSE_BASEADDR + 0x034)) != 0);

  phy_reg = *(volatile unsigned int *) (TSE_BASEADDR + 0x030);
  *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x0;

  phy_reg &= ~(0x1E);

  *(volatile unsigned int *) (TSE_BASEADDR + 0x028) = 0x1C04;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x02C) = phy_reg;
  while ((*(volatile unsigned int *) (TSE_BASEADDR + 0x034)) != 0);

  *(volatile unsigned int *) (TSE_BASEADDR + 0x028) = 0x1C09;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x1;
  while ((*(volatile unsigned int *) (TSE_BASEADDR + 0x034)) != 0);

  phy_reg = *(volatile unsigned int *) (TSE_BASEADDR + 0x030);
  *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x0;

  phy_reg |= 0x200;

  *(volatile unsigned int *) (TSE_BASEADDR + 0x028) = 0x1C09;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x02C) = phy_reg;
  while ((*(volatile unsigned int *) (TSE_BASEADDR + 0x034)) != 0);
}

void phy_autonegotiation(void)
{
  uint32_t phy_reg = 0xFFFF;
  uint16_t autoneg_complete;
  volatile uint32_t copper_aneg_timeout = 1000000u;
  volatile uint32_t sgmii_aneg_timeout = 100000u;
  uint8_t copper_link_up;

  *(volatile unsigned int *) (TSE_BASEADDR + 0x028) = 0x1C1F;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x02C) = 0x0;
  while ((*(volatile unsigned int *) (TSE_BASEADDR + 0x034)) != 0);

  *(volatile unsigned int *) (TSE_BASEADDR + 0x028) = 0x1C00;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x1;
  while ((*(volatile unsigned int *) (TSE_BASEADDR + 0x034)) != 0);

  phy_reg = *(volatile unsigned int *) (TSE_BASEADDR + 0x030);
  *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x0;

  phy_reg |= 0x1200;

  *(volatile unsigned int *) (TSE_BASEADDR + 0x028) = 0x1C00;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x02C) = phy_reg;
  while ((*(volatile unsigned int *) (TSE_BASEADDR + 0x034)) != 0);

  do {
    *(volatile unsigned int *) (TSE_BASEADDR + 0x028) = 0x1C01;
    *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x1;
    while ((*(volatile unsigned int *) (TSE_BASEADDR + 0x034)) != 0);

    phy_reg = *(volatile unsigned int *) (TSE_BASEADDR + 0x030);
    *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x0;

    autoneg_complete = phy_reg & 0x0020u;
    --copper_aneg_timeout;
  } while(!autoneg_complete && (copper_aneg_timeout != 0u));

  for (volatile uint32_t i = 0; i < 100000; i++);

  *(volatile unsigned int *) (TSE_BASEADDR + 0x028) = 0x1C01;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x1;
  while ((*(volatile unsigned int *) (TSE_BASEADDR + 0x034)) != 0);

  phy_reg = *(volatile unsigned int *) (TSE_BASEADDR + 0x030);
  *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x0;

  copper_link_up = phy_reg & 0x0004;

  if(copper_link_up != 0u)
  {
    *(volatile unsigned int *) (TSE_BASEADDR + 0x028) = 0x1200;
    *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x1;
    while ((*(volatile unsigned int *) (TSE_BASEADDR + 0x034)) != 0);

    phy_reg = *(volatile unsigned int *) (TSE_BASEADDR + 0x030);
    *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x0;

    phy_reg |= 0x1000;

    *(volatile unsigned int *) (TSE_BASEADDR + 0x028) = 0x1200;
    *(volatile unsigned int *) (TSE_BASEADDR + 0x02C) = phy_reg;
    while ((*(volatile unsigned int *) (TSE_BASEADDR + 0x034)) != 0);

    *(volatile unsigned int *) (TSE_BASEADDR + 0x028) = 0x1200;
    *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x1;
    while ((*(volatile unsigned int *) (TSE_BASEADDR + 0x034)) != 0);

    phy_reg = *(volatile unsigned int *) (TSE_BASEADDR + 0x030);
    *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x0;

    phy_reg |= 0x0200;

    *(volatile unsigned int *) (TSE_BASEADDR + 0x028) = 0x1200;
    *(volatile unsigned int *) (TSE_BASEADDR + 0x02C) = phy_reg;
    while ((*(volatile unsigned int *) (TSE_BASEADDR + 0x034)) != 0);

    do {
      *(volatile unsigned int *) (TSE_BASEADDR + 0x028) = 0x1201;
      *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x1;
      while ((*(volatile unsigned int *) (TSE_BASEADDR + 0x034)) != 0);

      phy_reg = *(volatile unsigned int *) (TSE_BASEADDR + 0x030);
      *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x0;

      autoneg_complete = phy_reg & 0x0020;
      --sgmii_aneg_timeout;
    } while((!autoneg_complete) && (sgmii_aneg_timeout != 0u));
  }
}

void phy_init (void)
{
  volatile uint16_t phy_reg_0;
  volatile uint16_t temp;
  volatile uint16_t  id1=0,id2=0,phy_mac_reg = 0;

  *(volatile unsigned int *) (TSE_BASEADDR + 0x028) = 0x1C02;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x1;
  while ((*(volatile unsigned int *) (TSE_BASEADDR + 0x034)) != 0);

  id1 = *(volatile unsigned int *) (TSE_BASEADDR + 0x030);
  *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x0;

  *(volatile unsigned int *) (TSE_BASEADDR + 0x028) = 0x1C03;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x1;
  while ((*(volatile unsigned int *) (TSE_BASEADDR + 0x034)) != 0);

  id2 = *(volatile unsigned int *) (TSE_BASEADDR + 0x030);
  *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x0;

  *(volatile unsigned int *) (TSE_BASEADDR + 0x028) = 0x1C1F;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x02C) = 0x0003;
  while ((*(volatile unsigned int *) (TSE_BASEADDR + 0x034)) != 0);

  *(volatile unsigned int *) (TSE_BASEADDR + 0x028) = 0x1C10;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x1;
  while ((*(volatile unsigned int *) (TSE_BASEADDR + 0x034)) != 0);

  phy_mac_reg = *(volatile unsigned int *) (TSE_BASEADDR + 0x030);
  *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x0;

  phy_mac_reg |= 0x80;

  *(volatile unsigned int *) (TSE_BASEADDR + 0x028) = 0x1C10;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x02C) = phy_mac_reg;
  while ((*(volatile unsigned int *) (TSE_BASEADDR + 0x034)) != 0);

  *(volatile unsigned int *) (TSE_BASEADDR + 0x028) = 0x1C1F;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x02C) = 0x0010;
  while ((*(volatile unsigned int *) (TSE_BASEADDR + 0x034)) != 0);

  phy_mac_reg = 0x80F0;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x028) = 0x1C12;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x02C) = phy_mac_reg;
  while ((*(volatile unsigned int *) (TSE_BASEADDR + 0x034)) != 0);

  while(1)
  {
    *(volatile unsigned int *) (TSE_BASEADDR + 0x028) = 0x1C12;
    *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x1;
    while ((*(volatile unsigned int *) (TSE_BASEADDR + 0x034)) != 0);

    temp = *(volatile unsigned int *) (TSE_BASEADDR + 0x030);
    *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x0;

    if((temp & 0x8000) == 0)
    {
      break;
    }
  }

  *(volatile unsigned int *) (TSE_BASEADDR + 0x028) = 0x1C1F;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x02C) = 0x0;
  while ((*(volatile unsigned int *) (TSE_BASEADDR + 0x034)) != 0);

  *(volatile unsigned int *) (TSE_BASEADDR + 0x028) = 0x1C00;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x1;
  while ((*(volatile unsigned int *) (TSE_BASEADDR + 0x034)) != 0);

  phy_reg_0 = *(volatile unsigned int *) (TSE_BASEADDR + 0x030);
  *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x0;

  phy_reg_0 = phy_reg_0 | 0x8000;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x028) = 0x1C00;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x02C) = phy_reg_0;
  while ((*(volatile unsigned int *) (TSE_BASEADDR + 0x034)) != 0);

  while(1)
  {
    *(volatile unsigned int *) (TSE_BASEADDR + 0x028) = 0x1C00;
    *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x1;
    while ((*(volatile unsigned int *) (TSE_BASEADDR + 0x034)) != 0);

    temp = *(volatile unsigned int *) (TSE_BASEADDR + 0x030);
    *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x0;
    if((temp & 0x8000) == 0)
    {
      break;
    }
  }

  *(volatile unsigned int *) (TSE_BASEADDR + 0x028) = 0x1C00;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x1;
  while ((*(volatile unsigned int *) (TSE_BASEADDR + 0x034)) != 0);

  temp = *(volatile unsigned int *) (TSE_BASEADDR + 0x030);
  *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x0;

  (void)id1; (void)id2; (void)temp;
}

void tse_init (void)
{
  uint32_t tse_reg = 0xFFFF;

  *(volatile unsigned int *) (TSE_BASEADDR + 0x000) = 0x00000005;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x004) = 0x00007201;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x040) = 0x6060603C;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x044) = 0xB1C00000;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x048) = 0x0000FF00;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x04C) = 0x0FFF0000;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x050) = 0x04000180;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x054) = 0x0680FFFF;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x058) = 0x00000000;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x05C) = 0x0007FFFF;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x1C0) = 0x0000000F;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x020) = 0x0007;

  tse_reg = *(volatile unsigned int *) (TSE_BASEADDR + 0x20);
  (void)tse_reg;

  phy_init();
}

/*======================================================================*/
/* PORT 1 INIT — mirror of port 0 with PHY/base address constants       */
/* changed. ALL MDIO transactions still issued via TSE_BASEADDR because */
/* CoreTSE_0 owns the (only) MDIO master. PHY targets are 29 (copper)   */
/* and 19 (SGMII). MAC writes go to TSE1_BASEADDR.                      */
/*======================================================================*/

void Phy1_advertise(void)
{
  uint32_t phy_reg = 0xFFFF;

  *(volatile unsigned int *) (TSE_BASEADDR + 0x028) = 0x1D04;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x1;
  while ((*(volatile unsigned int *) (TSE_BASEADDR + 0x034)) != 0);

  phy_reg = *(volatile unsigned int *) (TSE_BASEADDR + 0x030);
  *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x0;

  phy_reg &= ~(0x1E);

  *(volatile unsigned int *) (TSE_BASEADDR + 0x028) = 0x1D04;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x02C) = phy_reg;
  while ((*(volatile unsigned int *) (TSE_BASEADDR + 0x034)) != 0);

  *(volatile unsigned int *) (TSE_BASEADDR + 0x028) = 0x1D09;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x1;
  while ((*(volatile unsigned int *) (TSE_BASEADDR + 0x034)) != 0);

  phy_reg = *(volatile unsigned int *) (TSE_BASEADDR + 0x030);
  *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x0;

  phy_reg |= 0x200;

  *(volatile unsigned int *) (TSE_BASEADDR + 0x028) = 0x1D09;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x02C) = phy_reg;
  while ((*(volatile unsigned int *) (TSE_BASEADDR + 0x034)) != 0);
}

void phy1_autonegotiation(void)
{
  uint32_t phy_reg = 0xFFFF;
  uint16_t autoneg_complete;
  volatile uint32_t copper_aneg_timeout = 1000000u;
  volatile uint32_t sgmii_aneg_timeout = 100000u;
  uint8_t copper_link_up;

  *(volatile unsigned int *) (TSE_BASEADDR + 0x028) = 0x1D1F;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x02C) = 0x0;
  while ((*(volatile unsigned int *) (TSE_BASEADDR + 0x034)) != 0);

  *(volatile unsigned int *) (TSE_BASEADDR + 0x028) = 0x1D00;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x1;
  while ((*(volatile unsigned int *) (TSE_BASEADDR + 0x034)) != 0);

  phy_reg = *(volatile unsigned int *) (TSE_BASEADDR + 0x030);
  *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x0;

  phy_reg |= 0x1200;

  *(volatile unsigned int *) (TSE_BASEADDR + 0x028) = 0x1D00;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x02C) = phy_reg;
  while ((*(volatile unsigned int *) (TSE_BASEADDR + 0x034)) != 0);

  do {
    *(volatile unsigned int *) (TSE_BASEADDR + 0x028) = 0x1D01;
    *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x1;
    while ((*(volatile unsigned int *) (TSE_BASEADDR + 0x034)) != 0);

    phy_reg = *(volatile unsigned int *) (TSE_BASEADDR + 0x030);
    *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x0;

    autoneg_complete = phy_reg & 0x0020u;
    --copper_aneg_timeout;
  } while(!autoneg_complete && (copper_aneg_timeout != 0u));

  for (volatile uint32_t i = 0; i < 100000; i++);

  *(volatile unsigned int *) (TSE_BASEADDR + 0x028) = 0x1D01;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x1;
  while ((*(volatile unsigned int *) (TSE_BASEADDR + 0x034)) != 0);

  phy_reg = *(volatile unsigned int *) (TSE_BASEADDR + 0x030);
  *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x0;

  copper_link_up = phy_reg & 0x0004;

  if(copper_link_up != 0u)
  {
    /* iter-4: PHY 19 (CORETSE_1's internal SGMII slave) is now on the
     * INTERNAL MDIO bus, reachable only via TSE1_BASEADDR.  All PHY-19
     * accesses below use TSE1_BASEADDR (CORETSE_1's own master), which
     * also makes CORETSE_1's internal MDC toggle — the precondition the
     * iter-3 diagnostics narrowed in on.  PHY 29 (VSC8575 port 1 copper)
     * stays on the EXTERNAL bus and is accessed via TSE_BASEADDR above. */
    *(volatile unsigned int *) (TSE1_BASEADDR + 0x028) = 0x1300;
    *(volatile unsigned int *) (TSE1_BASEADDR + 0x024) = 0x1;
    while ((*(volatile unsigned int *) (TSE1_BASEADDR + 0x034)) != 0);

    phy_reg = *(volatile unsigned int *) (TSE1_BASEADDR + 0x030);
    *(volatile unsigned int *) (TSE1_BASEADDR + 0x024) = 0x0;

    phy_reg |= 0x1000;

    *(volatile unsigned int *) (TSE1_BASEADDR + 0x028) = 0x1300;
    *(volatile unsigned int *) (TSE1_BASEADDR + 0x02C) = phy_reg;
    while ((*(volatile unsigned int *) (TSE1_BASEADDR + 0x034)) != 0);

    *(volatile unsigned int *) (TSE1_BASEADDR + 0x028) = 0x1300;
    *(volatile unsigned int *) (TSE1_BASEADDR + 0x024) = 0x1;
    while ((*(volatile unsigned int *) (TSE1_BASEADDR + 0x034)) != 0);

    phy_reg = *(volatile unsigned int *) (TSE1_BASEADDR + 0x030);
    *(volatile unsigned int *) (TSE1_BASEADDR + 0x024) = 0x0;

    phy_reg |= 0x0200;

    *(volatile unsigned int *) (TSE1_BASEADDR + 0x028) = 0x1300;
    *(volatile unsigned int *) (TSE1_BASEADDR + 0x02C) = phy_reg;
    while ((*(volatile unsigned int *) (TSE1_BASEADDR + 0x034)) != 0);

    do {
      *(volatile unsigned int *) (TSE1_BASEADDR + 0x028) = 0x1301;
      *(volatile unsigned int *) (TSE1_BASEADDR + 0x024) = 0x1;
      while ((*(volatile unsigned int *) (TSE1_BASEADDR + 0x034)) != 0);

      phy_reg = *(volatile unsigned int *) (TSE1_BASEADDR + 0x030);
      *(volatile unsigned int *) (TSE1_BASEADDR + 0x024) = 0x0;

      autoneg_complete = phy_reg & 0x0020;
      --sgmii_aneg_timeout;
    } while((!autoneg_complete) && (sgmii_aneg_timeout != 0u));
  }
}

void phy1_init (void)
{
  volatile uint16_t phy_reg_0;
  volatile uint16_t temp;
  volatile uint16_t id1=0, id2=0, phy_mac_reg = 0;
  uint32_t timeout;

  *(volatile unsigned int *) (TSE_BASEADDR + 0x028) = 0x1D02;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x1;
  while ((*(volatile unsigned int *) (TSE_BASEADDR + 0x034)) != 0);

  id1 = *(volatile unsigned int *) (TSE_BASEADDR + 0x030);
  *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x0;

  *(volatile unsigned int *) (TSE_BASEADDR + 0x028) = 0x1D03;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x1;
  while ((*(volatile unsigned int *) (TSE_BASEADDR + 0x034)) != 0);

  id2 = *(volatile unsigned int *) (TSE_BASEADDR + 0x030);
  *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x0;

  /* Quick sanity check: if PHY 29 doesn't respond (ID register all-ones
   * or all-zeros), skip the rest of this function — we'd just hang in
   * the polling loops below. */
  if (id1 == 0xFFFF || (id1 == 0x0000 && id2 == 0x0000)) {
    uart_print("[phy1] WARNING: PHY 29 ID register reads as ");
    uart_print_hex16(id1);
    uart_print(" / ");
    uart_print_hex16(id2);
    uart_print(" — PHY likely absent, skipping phy1_init\r\n");
    return;
  }

  *(volatile unsigned int *) (TSE_BASEADDR + 0x028) = 0x1D1F;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x02C) = 0x0003;
  while ((*(volatile unsigned int *) (TSE_BASEADDR + 0x034)) != 0);

  *(volatile unsigned int *) (TSE_BASEADDR + 0x028) = 0x1D10;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x1;
  while ((*(volatile unsigned int *) (TSE_BASEADDR + 0x034)) != 0);

  phy_mac_reg = *(volatile unsigned int *) (TSE_BASEADDR + 0x030);
  *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x0;

  phy_mac_reg |= 0x80;

  *(volatile unsigned int *) (TSE_BASEADDR + 0x028) = 0x1D10;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x02C) = phy_mac_reg;
  while ((*(volatile unsigned int *) (TSE_BASEADDR + 0x034)) != 0);

  *(volatile unsigned int *) (TSE_BASEADDR + 0x028) = 0x1D1F;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x02C) = 0x0010;
  while ((*(volatile unsigned int *) (TSE_BASEADDR + 0x034)) != 0);

  phy_mac_reg = 0x80F0;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x028) = 0x1D12;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x02C) = phy_mac_reg;
  while ((*(volatile unsigned int *) (TSE_BASEADDR + 0x034)) != 0);

  /* Poll for command-complete with bounded timeout. AN4623's port-0
   * version uses while(1); we add a counter so an unresponsive PHY
   * doesn't hang the CPU. */
  timeout = MDIO_POLL_TIMEOUT_LOOPS;
  while(timeout-- > 0)
  {
    *(volatile unsigned int *) (TSE_BASEADDR + 0x028) = 0x1D12;
    *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x1;
    while ((*(volatile unsigned int *) (TSE_BASEADDR + 0x034)) != 0);

    temp = *(volatile unsigned int *) (TSE_BASEADDR + 0x030);
    *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x0;

    if((temp & 0x8000) == 0)
    {
      break;
    }
  }
  if (timeout == 0) {
    uart_print("[phy1] WARNING: timeout waiting for PHY 29 reg 0x12 bit 15 clear\r\n");
  }

  *(volatile unsigned int *) (TSE_BASEADDR + 0x028) = 0x1D1F;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x02C) = 0x0;
  while ((*(volatile unsigned int *) (TSE_BASEADDR + 0x034)) != 0);

  *(volatile unsigned int *) (TSE_BASEADDR + 0x028) = 0x1D00;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x1;
  while ((*(volatile unsigned int *) (TSE_BASEADDR + 0x034)) != 0);

  phy_reg_0 = *(volatile unsigned int *) (TSE_BASEADDR + 0x030);
  *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x0;

  phy_reg_0 = phy_reg_0 | 0x8000;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x028) = 0x1D00;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x02C) = phy_reg_0;
  while ((*(volatile unsigned int *) (TSE_BASEADDR + 0x034)) != 0);

  /* Bounded version of the PHY-reset wait. */
  timeout = MDIO_POLL_TIMEOUT_LOOPS;
  while(timeout-- > 0)
  {
    *(volatile unsigned int *) (TSE_BASEADDR + 0x028) = 0x1D00;
    *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x1;
    while ((*(volatile unsigned int *) (TSE_BASEADDR + 0x034)) != 0);

    temp = *(volatile unsigned int *) (TSE_BASEADDR + 0x030);
    *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x0;
    if((temp & 0x8000) == 0)
    {
      break;
    }
  }
  if (timeout == 0) {
    uart_print("[phy1] WARNING: timeout waiting for PHY 29 reset complete\r\n");
  }

  *(volatile unsigned int *) (TSE_BASEADDR + 0x028) = 0x1D00;
  *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x1;
  while ((*(volatile unsigned int *) (TSE_BASEADDR + 0x034)) != 0);

  temp = *(volatile unsigned int *) (TSE_BASEADDR + 0x030);
  *(volatile unsigned int *) (TSE_BASEADDR + 0x024) = 0x0;

  (void)id1; (void)id2; (void)temp;
}

void tse1_init (void)
{
  uint32_t tse_reg = 0xFFFF;

  /* Same MAC register sequence as tse_init(), targeting TSE1_BASEADDR.
   * Values copied verbatim — same operating mode, same address filter
   * defaults, same MDIO clock prescaler. The MDIO clock register write
   * here configures CoreTSE_1's MDIO master, but its MDIO pins are not
   * wired in the SmartDesign (marked unused in top.tcl), so the master
   * is harmless dead weight. */
  *(volatile unsigned int *) (TSE1_BASEADDR + 0x000) = 0x00000005;
  *(volatile unsigned int *) (TSE1_BASEADDR + 0x004) = 0x00007201;
  *(volatile unsigned int *) (TSE1_BASEADDR + 0x040) = 0x6060603C;
  *(volatile unsigned int *) (TSE1_BASEADDR + 0x044) = 0xB1C00000;
  *(volatile unsigned int *) (TSE1_BASEADDR + 0x048) = 0x0000FF00;
  *(volatile unsigned int *) (TSE1_BASEADDR + 0x04C) = 0x0FFF0000;
  *(volatile unsigned int *) (TSE1_BASEADDR + 0x050) = 0x04000180;
  *(volatile unsigned int *) (TSE1_BASEADDR + 0x054) = 0x0680FFFF;
  *(volatile unsigned int *) (TSE1_BASEADDR + 0x058) = 0x00000000;
  *(volatile unsigned int *) (TSE1_BASEADDR + 0x05C) = 0x0007FFFF;
  *(volatile unsigned int *) (TSE1_BASEADDR + 0x1C0) = 0x0000000F;
  *(volatile unsigned int *) (TSE1_BASEADDR + 0x020) = 0x0007;

  tse_reg = *(volatile unsigned int *) (TSE1_BASEADDR + 0x20);
  (void)tse_reg;

  phy1_init();
}

/*-------------------------- main ------------------------------------------*/

int main(void)
{
    UART_init(&g_uart, COREUARTAPB0_BASE_ADDR,
              BAUD_VALUE_115200, (DATA_8_BITS | NO_PARITY));
    uart_print("\r\n\r\n[boot] interlock firmware iter-4 starting\r\n");

    uart_print("[boot] configure_zl30364 (clock generator)\r\n");
    configure_zl30364();
    uart_print("[boot] configure_zl30364 done\r\n");

    uart_print("[boot] tse_init (MAC 0 @ ");
    uart_print_hex32(TSE_BASEADDR);
    uart_print(", PHY 28 copper / 18 SGMII)\r\n");
    tse_init();
    uart_print("[boot] tse_init done\r\n");

    /* Scan now that CoreTSE_0's MDIO master is configured but port-1's PHYs
     * haven't been touched yet — gives us a clean picture of what's on the bus. */
    mdio_scan(TSE_BASEADDR);

    uart_print("[boot] tse1_init (MAC 1 @ ");
    uart_print_hex32(TSE1_BASEADDR);
    uart_print(", PHY 29 copper / 19 SGMII)\r\n");
    tse1_init();
    uart_print("[boot] tse1_init done\r\n");

    /* iter-3: VSC8575 package-level SGMII MAC config per VMDS-10457 §3.2.
     * Configures all four chip ports for SGMII MAC mode via 18G/19G
     * (GPIO page).  Must come before per-port advertise/autoneg so that
     * VSC8575 ports 1-3 are properly in SGMII mode. */
    vsc8575_init();

    uart_print("[boot] Phy_advertise (port 0, PHY 28)\r\n");
    Phy_advertise();
    uart_print("[boot] Phy_advertise done\r\n");

    uart_print("[boot] Phy1_advertise (port 1, PHY 29)\r\n");
    Phy1_advertise();
    uart_print("[boot] Phy1_advertise done\r\n");

    uart_print("[boot] phy_autonegotiation (port 0)\r\n");
    phy_autonegotiation();
    uart_print("[boot] phy_autonegotiation done\r\n");

    uart_print("[boot] phy1_autonegotiation (port 1)\r\n");
    phy1_autonegotiation();
    uart_print("[boot] phy1_autonegotiation done\r\n");

    /* Final state dump. */
    dump_phy_status(TSE_BASEADDR,  PHY0_COPPER_ADDR, "port0 copper");
    dump_phy_status(TSE_BASEADDR,  PHY0_SGMII_ADDR,  "port0 SGMII");
    dump_phy_status(TSE_BASEADDR,  PHY1_COPPER_ADDR, "port1 copper");
    /* iter-4: PHY 19 is on the INTERNAL bus → read via TSE1_BASEADDR. */
    dump_phy_status(TSE1_BASEADDR, PHY1_SGMII_ADDR,  "port1 SGMII");

    /* CoreTSE_1 sanity probe (already initialized, this just confirms the
     * address is readable and what MAC_CONFIG_1 reads back as). */
    uart_print("[probe] CoreTSE_1 MAC_CONFIG_1 reads ");
    uint32_t tse1_mc1 = *(volatile unsigned int *)(TSE1_BASEADDR + 0x000);
    uart_print_hex32(tse1_mc1);
    uart_print("\r\n");

    /* iter-4 dual-bus verbose probes.  After iter-3 confirmed CORETSE_1's
     * slave was electrically identical to PHY 17 (unused address), iter-4
     * gives CORETSE_1 its own internal MDIO bus (tse1_loopback.sv in
     * gateware) and routes PHY 19 transactions through TSE1_BASEADDR
     * (CORETSE_1's own master).  These probes verify the new topology
     * by comparing what each master sees.
     *
     * Expected (if hypothesis is correct):
     *   Via CORETSE_0 (TSE_BASEADDR, external bus):
     *     - PHY 18 responds  (CORETSE_0's own slave)
     *     - PHY 28 responds  (VSC8575 port 0 copper)
     *     - PHY 19 silent    (no longer on this bus — it's on internal bus)
     *     - PHY 17 silent    (nothing there)
     *   Via CORETSE_1 (TSE1_BASEADDR, internal bus):
     *     - PHY 19 responds  (CORETSE_1's own slave, now reachable!)
     *     - PHY 18 silent    (different bus)
     *     - PHY 28 silent    (different bus)
     *     - PHY 17 silent    (nothing there)
     *
     * If we see this pattern, the hypothesis holds and the two-bus
     * architecture works.  Any deviation tells us something new. */

    uart_print("[diag] iter-4 PCS dumps via CORETSE_0 master (TSE_BASEADDR, external bus)\r\n");
    dump_pcs(TSE_BASEADDR, "PHY18 (CORETSE_0 slave, expect OK)",     18);
    dump_pcs(TSE_BASEADDR, "PHY28 (VSC8575 p0,    expect OK)",       28);
    dump_pcs(TSE_BASEADDR, "PHY19 (CORETSE_1, expect SILENT on ext)",19);
    dump_pcs(TSE_BASEADDR, "PHY17 (unused, baseline)",               17);

    uart_print("[diag] iter-4 PCS dumps via CORETSE_1 master (TSE1_BASEADDR, internal bus)\r\n");
    dump_pcs(TSE1_BASEADDR, "PHY19 (CORETSE_1 slave, expect OK NOW)",19);
    dump_pcs(TSE1_BASEADDR, "PHY18 (CORETSE_0 slave, expect SILENT)",18);
    dump_pcs(TSE1_BASEADDR, "PHY28 (VSC8575 p0,    expect SILENT)",  28);
    dump_pcs(TSE1_BASEADDR, "PHY17 (unused, baseline)",              17);

    uart_print("[boot] init complete — polling both ports\r\n");

    /* One-time MAC config readback for both ports (verify TX/RX enable, CRC, FIFO). */
    dump_mac_cfg(TSE_BASEADDR,  "CORETSE_0 (port0)");
    dump_mac_cfg(TSE1_BASEADDR, "CORETSE_1 (port1)");

    uint32_t tick = 0;
    while (1) {
        for (volatile uint32_t i = 0; i < 8000000; i++) { }
        uint16_t s0c = mdio_read(TSE_BASEADDR,  PHY0_COPPER_ADDR, 1);
        uint16_t s0s = mdio_read(TSE_BASEADDR,  PHY0_SGMII_ADDR,  1);
        uint16_t s1c = mdio_read(TSE_BASEADDR,  PHY1_COPPER_ADDR, 1);
        /* iter-4: PHY 19 on internal bus. */
        uint16_t s1s = mdio_read(TSE1_BASEADDR, PHY1_SGMII_ADDR,  1);
        uart_print("[poll t=");
        uart_print_dec(tick++);
        uart_print("] p0c=");
        uart_print_hex16(s0c);
        uart_print(" p0s=");
        uart_print_hex16(s0s);
        uart_print(" p1c=");
        uart_print_hex16(s1c);
        uart_print(" p1s=");
        uart_print_hex16(s1s);
        uart_print(" (link bits: ");
        uart_print_dec((s0c >> 2) & 1);
        uart_print_dec((s0s >> 2) & 1);
        uart_print_dec((s1c >> 2) & 1);
        uart_print_dec((s1s >> 2) & 1);
        uart_print(")\r\n");

        /* MAC-level frame counters + errors (the reliable signal). For the
         * bridge request path (port0 RX -> port1 TX): watch CORETSE_0 RX pkt
         * count rise (frames reach ingress MAC) and CORETSE_1 TX pkt count
         * (does the bridge feed the egress MAC?). Re-print CFG1/CFG2 each poll
         * in case anything changes them. */
        uart_print("[cfg] p0 CFG1=");  uart_print_hex32(mac_rd(TSE_BASEADDR,0x00));
        uart_print(" CFG2=");          uart_print_hex32(mac_rd(TSE_BASEADDR,0x04));
        uart_print(" | p1 CFG1=");     uart_print_hex32(mac_rd(TSE1_BASEADDR,0x00));
        uart_print(" CFG2=");          uart_print_hex32(mac_rd(TSE1_BASEADDR,0x04));
        uart_print("\r\n");
        dump_mac_stats(TSE_BASEADDR,  "P0");
        dump_mac_stats(TSE1_BASEADDR, "P1");
    }
}
