/* Minimal libc hooks for the Mi-V firmware when built with the Ubuntu
 * gcc-riscv64-unknown-elf + picolibc toolchain (see Makefile).  The AN4623
 * SoftConsole project supplied these through miv_rv32_syscall.c, which targets
 * newlib and does not compile against picolibc (thread-local errno). Nothing in
 * the firmware uses stdio or the heap, so only _exit (referenced by the HAL's
 * trap handler) is required. */
void _exit(int code)
{
    (void)code;
    for (;;) { }
}
