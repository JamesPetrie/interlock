/* Host-side functional test of main_mpf200.c against the CoreTSE/PCS mock.
 * Built twice by the Makefile: SGMII (default) and -DSFP_MODE_1000BASEX.
 * fw_main() is main_mpf200.c's main() renamed with -Dmain=fw_main. */
#include "mock_tse.h"
#include <stdio.h>
#include <string.h>

int fw_main(void);

#ifdef SFP_MODE_1000BASEX
#define EXPECT_ADV   0x0020u
#define EXPECT_MODE  "mode=1000BASE-X"
#define EXPECT_ADV_S "adv=0x0020"
#else
#define EXPECT_ADV   0x0001u
#define EXPECT_MODE  "mode=SGMII"
#define EXPECT_ADV_S "adv=0x0001"
#endif

static int g_fail, g_checks;
#define CHECK(cond, ...) do { g_checks++; if (!(cond)) { g_fail++; printf("    FAIL: " __VA_ARGS__); printf("  [%s:%d]\n", __FILE__, __LINE__); } } while (0)
#define UART_HAS(s) (strstr(g_uart_buf, (s)) != NULL)

/* MAC init sequence of AN4623 / the silicon-validated kit firmware (main.c tse_init). */
static const uint32_t kit_mac_init[][2] = {
    {0x000u, 0x00000005u}, {0x004u, 0x00007201u}, {0x040u, 0x6060603Cu}, {0x044u, 0xB1C00000u},
    {0x048u, 0x0000FF00u}, {0x04Cu, 0x0FFF0000u}, {0x050u, 0x04000180u}, {0x054u, 0x0680FFFFu},
    {0x058u, 0x00000000u}, {0x05Cu, 0x0007FFFFu}, {0x1C0u, 0x0000000Fu}, {0x020u, 0x00000007u},
};
#define KIT_N (sizeof kit_mac_init / sizeof kit_mac_init[0])

static void check_common_invariants(void)
{
    CHECK(g_bad_base_accesses == 0, "%u accesses to a base other than 0x60000000/0x60003000\n", g_bad_base_accesses);
    CHECK(g_uart_inited, "UART_init not called with base 0x60001000, 115200, 8N1\n");
    for (int i = 0; i < 2; i++) {
        mock_tse_t *t = &g_tse[i];
        CHECK(t->foreign_phy_accesses == 0, "MAC%d issued %u MDIO cycles to a PHY address other than %u\n", i, t->foreign_phy_accesses, t->pcs_addr);
        CHECK(t->restarts_without_enable == 0, "MAC%d PCS: %u AN restarts without AN enable\n", i, t->restarts_without_enable);
        CHECK(t->pcs_resets == 0, "MAC%d PCS was reset %u times (firmware is not expected to reset the PCS)\n", i, t->pcs_resets);
        CHECK(t->mac_log_n == (int)KIT_N, "MAC%d: %d non-MDIO CSR writes, expected %zu\n", i, t->mac_log_n, KIT_N);
        for (size_t k = 0; k < KIT_N && (int)k < t->mac_log_n; k++) {
            CHECK(t->mac_log_off[k] == kit_mac_init[k][0] && t->mac_log_val[k] == kit_mac_init[k][1],
                  "MAC%d init write %zu: 0x%03X <= 0x%08X, kit firmware writes 0x%03X <= 0x%08X\n", i, k,
                  t->mac_log_off[k], t->mac_log_val[k], kit_mac_init[k][0], kit_mac_init[k][1]);
        }
    }
}

static void show_uart(const char *title)
{
    printf("  --- UART (%s) ---\n", title);
    /* print at most the first 1200 chars, indented */
    size_t n = g_uart_len < 1200 ? g_uart_len : 1200;
    fputs("    ", stdout);
    for (size_t i = 0; i < n; i++) { putchar(g_uart_buf[i]); if (g_uart_buf[i] == '\n' && i + 1 < n) fputs("    ", stdout); }
    if (n < g_uart_len) printf("    ... (%zu more bytes)\n", g_uart_len - n);
}

static void t_nominal(void)
{
    printf("[a/b] nominal: both modules present, PCS mode %s\n", EXPECT_MODE);
    mock_reset();
    g_run_iterations = 2;
    fw_main();
    for (int i = 0; i < 2; i++) {
        mock_tse_t *t = &g_tse[i];
        CHECK(t->adv_writes == 1 && t->last_adv == EXPECT_ADV, "PCS%u adv writes=%u last=0x%04X (expected one write of 0x%04X)\n", t->pcs_addr, t->adv_writes, t->last_adv, EXPECT_ADV);
        CHECK(t->pcs[0] & 0x1000u, "PCS%u control AN enable not set (ctrl=0x%04X)\n", t->pcs_addr, t->pcs[0]);
        CHECK(t->an_restarts == 1, "PCS%u AN restarts=%u, expected exactly 1 (init only; no kicks once AN is done)\n", t->pcs_addr, t->an_restarts);
        CHECK(t->an_done && t->link, "PCS%u AN not complete in the mock (an_done=%d link=%d)\n", t->pcs_addr, t->an_done, t->link);
    }
    CHECK(UART_HAS(EXPECT_MODE), "UART lacks '%s'\n", EXPECT_MODE);
    CHECK(UART_HAS("[pcs] port0/SFP1 addr=18 status=0x0149"), "UART lacks PCS0 probe line\n");
    CHECK(UART_HAS("[pcs] port1/SFP2 addr=19 status=0x0149"), "UART lacks PCS1 probe line\n");
    CHECK(UART_HAS(EXPECT_MODE " " EXPECT_ADV_S " tbi_ctrl=0x0000"), "UART lacks the adv/tbi_ctrl readback '%s'\n", EXPECT_ADV_S);
    CHECK(UART_HAS("[an]  port0/SFP1 status=0x016D link=1 an_done=1 lpa=0xD801\r\n"), "UART lacks port0 AN-complete line\n");
    CHECK(UART_HAS("[an]  port1/SFP2 status=0x016D link=1 an_done=1 lpa=0xD801\r\n"), "UART lacks port1 AN-complete line\n");
    CHECK(!UART_HAS("(timeout)"), "unexpected AN timeout\n");
    CHECK(UART_HAS("[boot] init complete"), "init did not complete\n");
    CHECK(UART_HAS("[poll t=0] p0s=0x016D p1s=0x016D (link bits: 11 an_done: 11)"), "poll line 0 wrong\n");
    CHECK(UART_HAS("[poll t=1]"), "second poll iteration missing\n");
    check_common_invariants();
    show_uart("nominal");
}

static void t_no_module_port1(void)
{
    printf("[c] no module on port 1: bounded AN wait, init completes, poll loop re-kicks AN on port 1 only\n");
    mock_reset();
    g_tse[1].module_present = 0;
    g_run_iterations = 3;
    uint32_t before = g_tse[1].status_reads;
    fw_main();
    CHECK(UART_HAS("[an]  port0/SFP1 status=0x016D link=1 an_done=1 lpa=0xD801\r\n"), "port0 should link\n");
    CHECK(UART_HAS("[an]  port1/SFP2 status=0x0149 link=0 an_done=0 lpa=0x0000 (timeout)\r\n"), "port1 should report AN timeout\n");
    CHECK(UART_HAS("[boot] init complete"), "init must complete with a port missing\n");
    /* pcs_wait_an polls status AN_WAIT_POLLS (400) times on the absent port: 1 probe + 400 waits + 3 poll-loop reads */
    CHECK(g_tse[1].status_reads - before == 1 + 400 + 3, "PCS19 status reads=%u, expected 404 (1 probe + 400 bounded AN polls + 3 loop polls)\n", g_tse[1].status_reads - before);
    CHECK(g_tse[0].an_restarts == 1, "PCS18 restarts=%u, expected 1 (linked port must not be kicked)\n", g_tse[0].an_restarts);
    CHECK(g_tse[1].an_restarts == 1 + 3, "PCS19 restarts=%u, expected 4 (init + one per poll iteration)\n", g_tse[1].an_restarts);
    CHECK(UART_HAS("[poll t=2] p0s=0x016D p1s=0x0149 (link bits: 10 an_done: 10)"), "poll line wrong\n");
    check_common_invariants();
}

static void t_hotplug_kick(void)
{
    printf("[c2] no module at boot: the poll loop kicks AN on the dead port so a module plugged later can link\n");
    mock_reset();
    g_tse[1].module_present = 0;
    g_run_iterations = 1;
    fw_main();
    CHECK(!g_tse[1].link && !g_tse[1].an_done, "port1 linked without a module?\n");
    CHECK(g_tse[1].an_restarts == 2, "PCS19 restarts=%u, expected 2 (init + one kick from poll iteration 0)\n", g_tse[1].an_restarts);
    CHECK(g_tse[1].an_in_progress, "the kick did not leave AN running on PCS19\n");
    CHECK(g_tse[1].pcs[0] == 0x1000u, "PCS19 control after kick = 0x%04X, expected 0x1000 (AN enable, restart self-cleared)\n", g_tse[1].pcs[0]);
    CHECK(g_tse[0].an_restarts == 1, "PCS18 restarts=%u, expected 1\n", g_tse[0].an_restarts);
    check_common_invariants();
}

static void t_busy_forever(void)
{
    printf("[d] MDIO management block never idle: every wait is bounded, firmware reaches the main loop\n");
    mock_reset();
    g_tse[0].busy_forever = 1; g_tse[1].busy_forever = 1;
    g_run_iterations = 1;
    fw_main();
    CHECK(UART_HAS("[boot] init complete"), "init must complete even if MDIO never reports idle\n");
    CHECK(UART_HAS("[poll t=0]"), "main loop not reached\n");
    uint32_t total = g_tse[0].reads + g_tse[1].reads;
    CHECK(total > 1000000u && total < 400000000u, "unexpected register read count %u\n", total);
    printf("    (register reads with MDIO stuck busy: %u)\n", total);
    check_common_invariants();
}

static void t_pcs_silent_port0(void)
{
    printf("[e] PCS slave on port 0 silent (0xFFFF): NO RESPONSE path, port 1 still configured\n");
    mock_reset();
    g_tse[0].pcs_silent = 1;
    g_run_iterations = 1;
    fw_main();
    CHECK(UART_HAS("[pcs] port0/SFP1 addr=18 status=0xFFFF NO RESPONSE (MDIO loopback broken?)\r\n"), "missing NO RESPONSE line\n");
    CHECK(g_tse[0].adv_writes == 0 && g_tse[0].mdio_writes == 0, "firmware wrote to a silent PCS (adv_writes=%u mdio_writes=%u)\n", g_tse[0].adv_writes, g_tse[0].mdio_writes);
    CHECK(g_tse[1].adv_writes == 1 && g_tse[1].last_adv == EXPECT_ADV && g_tse[1].link, "port1 not configured normally\n");
    CHECK(UART_HAS("[an]  port1/SFP2 status=0x016D link=1 an_done=1"), "port1 should still link\n");
    CHECK(UART_HAS("[boot] init complete"), "init must complete\n");
    show_uart("silent PCS0");
    check_common_invariants();
}

static void t_slow_mdio(void)
{
    printf("[f] slow MDIO (busy 50 polls per cycle) and immediate AN: same result, no extra AN kicks\n");
    mock_reset();
    g_tse[0].busy_polls = 50; g_tse[1].busy_polls = 50;
    g_tse[0].an_polls_to_done = 1; g_tse[1].an_polls_to_done = 1;
    g_run_iterations = 1;
    fw_main();
    CHECK(g_tse[0].link && g_tse[1].link, "ports did not link\n");
    CHECK(g_tse[0].an_restarts == 1 && g_tse[1].an_restarts == 1, "unexpected AN kicks (%u, %u)\n", g_tse[0].an_restarts, g_tse[1].an_restarts);
    CHECK(UART_HAS("[poll t=0] p0s=0x016D p1s=0x016D (link bits: 11 an_done: 11)"), "poll line wrong\n");
    check_common_invariants();
}

int main(void)
{
    printf("=== main_mpf200.c host test (%s) ===\n", EXPECT_MODE);
    t_nominal();
    t_no_module_port1();
    t_hotplug_kick();
    t_busy_forever();
    t_pcs_silent_port0();
    t_slow_mdio();
    printf("=== %d checks, %d failed ===\n", g_checks, g_fail);
    return g_fail ? 1 : 0;
}
