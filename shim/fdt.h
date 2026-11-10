#pragma once
#include <stdint.h>

/* Read a u32/u64 (cell-size agnostic) property of /chosen. Returns 0 if absent. */
int fdt_chosen_u64(uintptr_t fdt, const char *prop, uint64_t *out);

/* Read a NUL-terminated string property of /chosen. Returns length or 0 if absent. */
uint32_t fdt_chosen_str(uintptr_t fdt, const char *prop, char *out, uint32_t max_len);
