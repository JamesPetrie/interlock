# Mi-V firmware build for the interlock gateware (both boards).
#
# Builds the Intel-HEX images that Libero bakes into the Mi-V TCM:
#   build/iog_cdr_mpf300.hex   from main.c          (MPF300-EVAL-KIT, VSC8575 + ZL30364)
#   build/iog_cdr_mpf200.hex   from main_mpf200.c   (MPF200-ETH-SENSOR-BRIDGE-R2, SFP via XCVR)
#
# Toolchain: Ubuntu packages (no SoftConsole needed)
#   apt-get install gcc-riscv64-unknown-elf picolibc-riscv64-unknown-elf
# Compiler flags mirror the AN4623 SoftConsole project (Debug/subdir.mk):
#   -march=rv32im -mabi=ilp32 -O0 -g3, linker script miv-rv32-ram.ld (TCM at 0x80000000).
#
# HAL, drivers and linker script are NOT vendored; they come from Microchip's
# AN4623 bundle (13 MB, no login), fetched into build/an4623 by `make fetch`
# (sha256-verified).  Set AN4623_DIR to an existing extraction to skip the fetch.
#
# Targets:
#   make            build both hex files into build/
#   make mpf200     build/iog_cdr_mpf200.hex (MODE=sgmii default, MODE=basex for optical SFPs)
#   make mpf300     build/iog_cdr_mpf300.hex
#   make install    copy build/iog_cdr_mpf200.hex -> ./iog_cdr_mpf200.hex (the file Libero uses)
#                   (the MPF300 image ./iog_cdr.hex is the silicon-validated SoftConsole build;
#                    replace it deliberately with `make install-mpf300`)
#   make test       host-side functional test of main_mpf200.c against a CoreTSE/PCS mock (test/, gcc)
#   make clean

CROSS      ?= riscv64-unknown-elf-
CC          = $(CROSS)gcc
OBJCOPY     = $(CROSS)objcopy
SIZE        = $(CROSS)size

MODE       ?= sgmii
BUILD      ?= build

AN4623_URL    = https://ww1.microchip.com/downloads/aemDocuments/documents/FPGA/SOCDesignFiles/mpf_an4623_v2022p3_df.zip
AN4623_SHA256 = 5aa66cab864dc3ba5960c361b13d11a7b9550ec56975626e27b64f73a32aa030
AN4623_DIR   ?= $(BUILD)/an4623
SC            = $(AN4623_DIR)/mpf_an4623_v2022p3_df/SoftConsole_Project/iog_cdr

ARCHFLAGS = -march=rv32im -mabi=ilp32 -msmall-data-limit=8 -mstrict-align -mno-save-restore
CFLAGS    = --specs=picolibc.specs $(ARCHFLAGS) -O0 -g3 -fmessage-length=0 -fsigned-char \
            -ffunction-sections -fdata-sections -DNDEBUG -std=gnu11 \
            -I$(SC)/hal -I$(SC)/miv_rv32_hal -I$(SC) -I$(SC)/drivers \
            -I$(SC)/drivers/CoreSPI -I$(SC)/drivers/CoreUARTapb
LDFLAGS   = --specs=picolibc.specs $(ARCHFLAGS) -O0 -g3 -T $(SC)/miv_rv32_hal/miv-rv32-ram.ld \
            -nostartfiles -Xlinker --gc-sections

ifeq ($(MODE),basex)
MPF200_DEFS = -DSFP_MODE_1000BASEX
else
MPF200_DEFS =
endif

# Support code shared by both images (from the AN4623 bundle + fw_stubs.c).
# miv_rv32_syscall.c (newlib syscalls) is replaced by fw_stubs.c under picolibc.
HAL_C   = $(SC)/miv_rv32_hal/miv_rv32_hal.c $(SC)/miv_rv32_hal/miv_rv32_init.c \
          $(SC)/miv_rv32_hal/miv_rv32_stubs.c $(SC)/hal/hal_irq.c \
          $(SC)/drivers/CoreUARTapb/core_uart_apb.c $(SC)/drivers/CoreSPI/core_spi.c
HAL_S   = $(SC)/miv_rv32_hal/miv_rv32_entry.S $(SC)/hal/hw_reg_access.S
HAL_OBJ = $(patsubst %.c,$(BUILD)/hal/%.o,$(notdir $(HAL_C))) \
          $(patsubst %.S,$(BUILD)/hal/%.o,$(notdir $(HAL_S))) \
          $(BUILD)/hal/fw_stubs.o

.PHONY: all mpf200 mpf300 fetch install install-mpf300 test clean
all: mpf200 mpf300
mpf200: $(BUILD)/iog_cdr_mpf200.hex
mpf300: $(BUILD)/iog_cdr_mpf300.hex

# ---- AN4623 bundle -----------------------------------------------------------
$(SC)/main.c:
	@echo "=== fetching AN4623 bundle into $(AN4623_DIR) ==="
	mkdir -p $(AN4623_DIR)
	curl -sSL -A "Mozilla/5.0" -e "https://www.microchip.com/en-us/application-notes/AN4623" \
	     -o $(AN4623_DIR)/mpf_an4623_v2022p3_df.zip "$(AN4623_URL)"
	echo "$(AN4623_SHA256)  $(AN4623_DIR)/mpf_an4623_v2022p3_df.zip" | sha256sum -c -
	unzip -q -o $(AN4623_DIR)/mpf_an4623_v2022p3_df.zip -d $(AN4623_DIR)
fetch: $(SC)/main.c

# ---- objects -----------------------------------------------------------------
$(BUILD)/hal/%.o: $(SC)/miv_rv32_hal/%.c | $(SC)/main.c
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -c -o $@ $<
$(BUILD)/hal/%.o: $(SC)/hal/%.c | $(SC)/main.c
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -c -o $@ $<
$(BUILD)/hal/%.o: $(SC)/drivers/CoreUARTapb/%.c | $(SC)/main.c
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -c -o $@ $<
$(BUILD)/hal/%.o: $(SC)/drivers/CoreSPI/%.c | $(SC)/main.c
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -c -o $@ $<
$(BUILD)/hal/%.o: $(SC)/miv_rv32_hal/%.S | $(SC)/main.c
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -x assembler-with-cpp -c -o $@ $<
$(BUILD)/hal/%.o: $(SC)/hal/%.S | $(SC)/main.c
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -x assembler-with-cpp -c -o $@ $<
$(BUILD)/hal/fw_stubs.o: fw_stubs.c | $(SC)/main.c
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -c -o $@ $<

$(BUILD)/main_mpf200.o: main_mpf200.c | $(SC)/main.c
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) $(MPF200_DEFS) -c -o $@ $<
$(BUILD)/main_mpf300.o: main.c | $(SC)/main.c
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -c -o $@ $<
$(BUILD)/zl30362_config.o: $(SC)/zl30362_config.c | $(SC)/main.c
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -c -o $@ $<

# ---- images ------------------------------------------------------------------
# Libero's RAM.cfg loads the hex into the TCM, so addresses are rebased from
# 0x80000000 to 0 (no type-04 extended-address records), matching iog_cdr.hex.
$(BUILD)/iog_cdr_mpf200.elf: $(BUILD)/main_mpf200.o $(HAL_OBJ)
	$(CC) $(LDFLAGS) -Wl,-Map,$(@:.elf=.map) -o $@ $^
	$(SIZE) $@
$(BUILD)/iog_cdr_mpf300.elf: $(BUILD)/main_mpf300.o $(BUILD)/zl30362_config.o $(HAL_OBJ)
	$(CC) $(LDFLAGS) -Wl,-Map,$(@:.elf=.map) -o $@ $^
	$(SIZE) $@
$(BUILD)/%.hex: $(BUILD)/%.elf
	$(OBJCOPY) -O ihex --change-addresses -0x80000000 $< $@

install: $(BUILD)/iog_cdr_mpf200.hex
	cp $< ./iog_cdr_mpf200.hex
install-mpf300: $(BUILD)/iog_cdr_mpf300.hex
	cp $< ./iog_cdr.hex

# ---- host test ---------------------------------------------------------------
# Compiles main_mpf200.c natively (-DHOST_TEST) against test/mock_tse.c and runs
# the SGMII and 1000BASE-X variants; needs only the host gcc, not the AN4623 bundle.
test:
	$(MAKE) -C test run

clean:
	rm -rf $(BUILD)/hal $(BUILD)/*.o $(BUILD)/*.elf $(BUILD)/*.map $(BUILD)/*.hex
	$(MAKE) -C test clean
