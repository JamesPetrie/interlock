#!/usr/bin/env python3
"""Add a synchronous-abort handler that prints ESR/ELR/FAR (EL3) to a Versal
standalone A72 C program, so a silent Xil_SyncErrorHandler hang becomes a
readable console line. Usage: instrument_abort.py <file.c>  (idempotent)"""
import sys

p = sys.argv[1]
s = open(p).read()
if "ilock_sync_handler" in s:
    print("already instrumented"); sys.exit(0)
handler = """
#include "xil_exception.h"
static void ilock_sync_handler(void *d) {
    unsigned long long esr, elr, far;
    asm volatile("mrs %0, esr_el3" : "=r"(esr));
    asm volatile("mrs %0, elr_el3" : "=r"(elr));
    asm volatile("mrs %0, far_el3" : "=r"(far));
    xil_printf("\\n\\rSYNC ABORT esr=%08x%08x elr=%08x%08x far=%08x%08x\\n\\r",
        (unsigned)(esr >> 32), (unsigned)esr, (unsigned)(elr >> 32), (unsigned)elr,
        (unsigned)(far >> 32), (unsigned)far);
    while (1) ;
}
"""
i = s.index("int wait (uint32_t delay)")
s = s[:i] + handler + s[i:]
main_sig = "int main()\n{\n"
assert main_sig in s, "main() signature not found"
s = s.replace(main_sig, main_sig + "    Xil_ExceptionRegisterHandler(XIL_EXCEPTION_ID_SYNC_INT, ilock_sync_handler, NULL);\n", 1)
open(p, "w").write(s)
print("instrumented")
