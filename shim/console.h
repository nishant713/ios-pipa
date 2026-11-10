#pragma once
#include <stdint.h>

#define FB_BASE		0x9c000000	/* reserved-memory cont_splash_region on pipa */
#define FB_W		1800		/* Xiaomi Pad 6 (pipa) physical panel portrait scanout */
#define FB_H		2880
#define FB_STRIDE	(FB_W * 4)	/* a8r8g8b8 (7200 bytes per scanline) */

#define RED	0xffff0000U
#define BLUE	0xff0000a0U
#define WHITE	0xffffffffU
#define YELLOW	0xffffff00U
#define GREEN	0xff00ff00U
#define CYAN	0xff00ffffU
#define MAGENTA	0xffff00ffU
#define GRAY	0xff888888U

extern volatile uint32_t *con_fb;

void con_clear(uint32_t bg);
void con_skip(void);
void con_str(const char *label, const char *s);
void con_hex(const char *label, uint64_t v);
/* label, then "a=xxxxxxxx b=xxxxxxxx" style pairs of 32-bit values */
void con_hex2(const char *label, const char *a, uint32_t va, const char *b, uint32_t vb);

void con_set_scale(int s);
int con_get_row(void);
void con_set_row(int r);
void con_puts(const char *s, uint32_t fg);
void con_delay_ms(unsigned ms);

