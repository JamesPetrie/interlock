/* Host-test stand-in for the AN4623 sample_hw_platform.h (same values). */
#ifndef SAMPLE_HW_PLATFORM_H
#define SAMPLE_HW_PLATFORM_H
#define SYS_CLK_FREQ                    80000000UL
#define TSE_BASEADDR                    0x60000000
#define COREUARTAPB0_BASE_ADDR          0x60001000UL
#define CORESPI_BASE_ADDR               0x60002000UL
#define BAUD_VALUE_115200               (SYS_CLK_FREQ / (16 * 115200)) - 1
#endif
