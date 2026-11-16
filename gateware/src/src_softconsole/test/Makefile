# Host-side test of main_mpf200.c (see mock_tse.h).  `make -C .. test` or `make run` here.
CC     ?= gcc
FW      = ..
CFLAGS  = -std=gnu11 -O2 -g -Wall -Wextra -Werror -DHOST_TEST -I mock -I $(FW)

.PHONY: run build clean
run: build
	./test_sgmii
	./test_basex

build: test_sgmii test_basex

# main_mpf200.c's main() becomes fw_main(); the harness supplies the real main().
fw_sgmii.o: $(FW)/main_mpf200.c mock/sample_hw_platform.h mock/drivers/CoreUARTapb/core_uart_apb.h mock/miv_rv32_hal/miv_rv32_hal.h
	$(CC) $(CFLAGS) -Dmain=fw_main -c -o $@ $<
fw_basex.o: $(FW)/main_mpf200.c mock/sample_hw_platform.h mock/drivers/CoreUARTapb/core_uart_apb.h mock/miv_rv32_hal/miv_rv32_hal.h
	$(CC) $(CFLAGS) -DSFP_MODE_1000BASEX -Dmain=fw_main -c -o $@ $<
mock_tse.o: mock_tse.c mock_tse.h
	$(CC) $(CFLAGS) -c -o $@ $<
test_sgmii.o: test_main.c mock_tse.h
	$(CC) $(CFLAGS) -c -o $@ $<
test_basex.o: test_main.c mock_tse.h
	$(CC) $(CFLAGS) -DSFP_MODE_1000BASEX -c -o $@ $<
test_sgmii: test_sgmii.o mock_tse.o fw_sgmii.o
	$(CC) $(CFLAGS) -o $@ $^
test_basex: test_basex.o mock_tse.o fw_basex.o
	$(CC) $(CFLAGS) -o $@ $^

clean:
	rm -f *.o test_sgmii test_basex
