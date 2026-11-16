/* Host-test stand-in for the CoreUARTapb driver header: same types and
 * prototypes as the AN4623 copy; the functions live in ../../../mock_tse.c. */
#ifndef CORE_UART_APB_H
#define CORE_UART_APB_H
#include <stdint.h>
typedef unsigned int addr_t;
#define DATA_8_BITS     0x01u
#define NO_PARITY       0x00u
typedef struct {
    addr_t  base_address;
    uint8_t status;
} UART_instance_t;
void UART_init(UART_instance_t *this_uart, addr_t base_addr, uint16_t baud_value, uint8_t line_config);
void UART_polled_tx_string(UART_instance_t *this_uart, const uint8_t *p_sz_string);
#endif
