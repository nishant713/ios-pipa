#include <stddef.h>

/*

 * Milestone 5 probe: can we talk to the internal UFS that ABL booted from?

 *

 * Runs with the MMU off (all memory Device, so DMA is coherent with no cache

 * maintenance). Dumps the UFSHCI registers ABL left behind and, if the host

 * is enabled and the link is up, points the transfer request list at our own

 * buffers and issues READ-ONLY SCSI commands on LUN 0. Nothing is ever

 * written to the device: there is no SCSI WRITE in this file.

 *

 * Register layout and descriptor formats: JEDEC UFSHCI (JESD223).

 */

#include <stdint.h>

#include "console.h"

#include "ufs.h"

#include "fdt.h"



#define UFS_BASE	0x01d84000UL	/* ufshc@1d84000, sweet live FDT */



#define REG_CAP		0x00

#define REG_VER		0x08

#define REG_IS		0x20

#define REG_HCS		0x30

#define REG_HCE		0x34

#define REG_UTRLBA	0x50

#define REG_UTRLBAU	0x54

#define REG_UTRLDBR	0x58

#define REG_UTRLCLR	0x5c

#define REG_UTRLRSR	0x60



#define HCS_DP		(1u << 0)

#define HCS_UTRLRDY	(1u << 1)

#define HCS_UCRDY	(1u << 3)



#define BLOCK		4096u



static inline uint32_t ufs_rd(uint32_t off) { return *(volatile uint32_t *)(UFS_BASE + off); }

static inline void ufs_wr(uint32_t off, uint32_t v) { *(volatile uint32_t *)(UFS_BASE + off) = v; }



/* UTP transfer request descriptor (32 bytes). */

struct utrd {

	uint32_t dw0;		/* [31:28] CT, [26:25] DD, [24] I */

	uint32_t dw1;

	uint32_t dw2;		/* [7:0] OCS */

	uint32_t dw3;

	uint32_t ucdba;		/* command descriptor base, 128-byte aligned */

	uint32_t ucdbau;

	uint32_t resp;		/* [31:16] response UPIU offset, [15:0] length (DWORDs) */

	uint32_t prdt;		/* [31:16] PRDT offset, [15:0] entries (DWORDs/entries) */

};



/* UTP command descriptor: command UPIU, response UPIU, PRDT. */

struct ucd {

	uint8_t cmd[512];

	uint8_t resp[512];

	struct {

		uint32_t addr_lo, addr_hi, rsvd, dbc;	/* dbc = byte count - 1 */

	} prdt[1];

};



static struct utrd utrl[32] __attribute__((aligned(1024)));

static struct ucd ucd __attribute__((aligned(128)));

static uint8_t data[2 * BLOCK] __attribute__((aligned(4096)));



#define CHUNK_BLOCKS 16

static uint8_t chunk_buf[CHUNK_BLOCKS * BLOCK] __attribute__((aligned(4096)));



static uint32_t userdata_first = 0, userdata_last = 0;



static uint64_t ticks(void)

{

	uint64_t t;

	__asm__ volatile("isb; mrs %0, cntvct_el0" : "=r"(t));

	return t;

}



static void zero(void *p, unsigned n)

{

	volatile uint8_t *b = p;

	for (unsigned i = 0; i < n; i++)

		b[i] = 0;

}



static uint32_t be32(const uint8_t *p)

{

	return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 | p[3];

}



/*

 * Issue one SCSI command (read direction or no data) in slot 0.

 * Returns 0 on success, otherwise a code describing the failure stage.

 */

static uint32_t scsi(uint8_t lun, const uint8_t *cdb, unsigned cdb_len, void *buf, uint32_t len,

    uint32_t *ocs_out, uint32_t *status_out)

{

	static uint8_t tag;

	zero(&utrl[0], sizeof(utrl[0]));

	zero(&ucd, sizeof(ucd));



	/* Command UPIU: header (12 bytes), expected length, CDB. */

	ucd.cmd[0] = 0x01;			/* transaction type: COMMAND */

	ucd.cmd[1] = len ? 0x40 : 0x00;		/* flags: R (data in) */

	ucd.cmd[2] = lun;

	ucd.cmd[3] = ++tag;

	ucd.cmd[12] = len >> 24;

	ucd.cmd[13] = len >> 16;

	ucd.cmd[14] = len >> 8;

	ucd.cmd[15] = len;

	for (unsigned i = 0; i < cdb_len; i++)

		ucd.cmd[16 + i] = cdb[i];



	uintptr_t ucd_pa = (uintptr_t)&ucd;

	utrl[0].dw0 = (1u << 28) | ((len ? 2u : 0u) << 25) | (1u << 24);	/* UFS storage, dev->host */

	utrl[0].dw2 = 0x0f;			/* OCS: invalid until the host updates it */

	utrl[0].ucdba = (uint32_t)ucd_pa;

	utrl[0].ucdbau = (uint32_t)(ucd_pa >> 32);

	utrl[0].resp = (512u / 4) << 16 | (512u / 4);

	if (len) {

		uintptr_t buf_pa = (uintptr_t)buf;

		ucd.prdt[0].addr_lo = (uint32_t)buf_pa;

		ucd.prdt[0].addr_hi = (uint32_t)(buf_pa >> 32);

		ucd.prdt[0].dbc = len - 1;

		utrl[0].prdt = (1024u / 4) << 16 | 1;

	}

	__asm__ volatile("dsb sy" ::: "memory");



	ufs_wr(REG_UTRLDBR, 1);

	uint64_t start = ticks();

	while (ufs_rd(REG_UTRLDBR) & 1) {

		if (ticks() - start > 19200000) {	/* 1 s */

			ufs_wr(REG_UTRLCLR, ~1u);	/* clear slot 0 (write 0 to the bit) */

			return 1;			/* timeout */

		}

	}

	__asm__ volatile("dsb sy" ::: "memory");

	*ocs_out = utrl[0].dw2 & 0xff;

	*status_out = ucd.resp[7];		/* SCSI status in the response UPIU */

	if (*ocs_out)

		return 2;

	if (ucd.resp[0] != 0x21)		/* RESPONSE UPIU */

		return 3;

	return *status_out ? 4 : 0;

}



static uint32_t read_blocks(uint32_t lba, uint32_t count, void *buf)

{

	uint8_t cdb[10] = { 0x28, 0, lba >> 24, lba >> 16, lba >> 8, lba, 0, count >> 8, count, 0 };

	uint32_t ocs, st;

	return scsi(0, cdb, sizeof(cdb), buf, count * BLOCK, &ocs, &st);

}



static uint64_t le64(const uint8_t *p)

{

	uint64_t v = 0;

	for (int i = 7; i >= 0; i--)

		v = v << 8 | p[i];

	return v;

}



/* Name the filesystem whose superblock starts at block lba (f2fs/ext4). */

static const char *fs_type(uint32_t lba)

{

	if (read_blocks(lba, 1, data))

		return "read error";

	const uint8_t *sb = data + 1024;

	if (sb[0] == 0x10 && sb[1] == 0x20 && sb[2] == 0xf5 && sb[3] == 0xf2)

		return "f2fs";

	if (sb[56] == 0x53 && sb[57] == 0xef)

		return "ext4";

	return "other";

}



/* One line per GPT entry of at least 64 MiB: name, first LBA, size in MiB (4K blocks). */

static void list_partitions(uint32_t entry_lba, uint32_t nent)

{

	static uint8_t ents[128 * 128] __attribute__((aligned(4096)));

	uint32_t blocks = (nent * 128 + BLOCK - 1) / BLOCK;

	if (read_blocks(entry_lba, blocks, ents)) {

		con_str("UFS PART", "read error");

		return;

	}

	for (uint32_t i = 0; i < nent; i++) {

		const uint8_t *e = ents + i * 128;

		uint64_t first = le64(e + 32), last = le64(e + 40);

		if (!first)

			continue;

		char name[17];

		for (int c = 0; c < 16; c++) {

			uint8_t ch = e[56 + 2 * c];

			name[c] = (ch >= 32 && ch < 127) ? ch : (ch ? '?' : 0);

		}

		name[16] = 0;

		if (!__builtin_strcmp(name, "userdata")) {

			userdata_first = (uint32_t)first;

			userdata_last = (uint32_t)last;

		}

		if (((last - first + 1) >> 8) < 64)	/* keep the screen to the big ones */

			continue;

		con_hex2(name, "lba=", (uint32_t)first, "MiB=", (uint32_t)((last - first + 1) >> 8));

		if (!__builtin_strcmp(name, "userdata") || !__builtin_strcmp(name, "cust") ||

		    !__builtin_strcmp(name, "cache"))

			con_str(" fs", fs_type((uint32_t)first));

	}

}



static char to_lower(char c)

{

	return (c >= 'A' && c <= 'Z') ? (c + ('a' - 'A')) : c;

}



static int str_contains_ci(const char *haystack, const char *needle)

{

	if (!haystack || !needle) return 0;

	for (int i = 0; haystack[i]; i++) {

		int j = 0;

		while (needle[j] && (to_lower(haystack[i + j]) == to_lower(needle[j])))

			j++;

		if (!needle[j])

			return 1;

	}

	return 0;

}



static int is_diagnostic_line(const char *line)

{

	if (str_contains_ci(line, "SWEET TOP")) return 0;
	if (str_contains_ci(line, "AegirPoster")) return 0;
	if (str_contains_ci(line, "SWEET HID")) return 1;

	if (str_contains_ci(line, "fileproviderd")) return 0;

	if (str_contains_ci(line, "launchd")) return 0;

	if (str_contains_ci(line, "corpse")) return 0;

	if (str_contains_ci(line, "dump_dead")) return 0;

	if (str_contains_ci(line, "SWEET TOUCH")) return 1;

	if (str_contains_ci(line, "touch")) return 1;

	if (str_contains_ci(line, "nt36")) return 1;

	if (str_contains_ci(line, "trim")) return 1;

	if (str_contains_ci(line, "chip id")) return 1;

	if (str_contains_ci(line, "stage")) return 1;

	if (str_contains_ci(line, "qup")) return 1;

	if (str_contains_ci(line, "spi")) return 1;

	if (str_contains_ci(line, "gpio33")) return 1;

	if (str_contains_ci(line, "gpio39")) return 1;

	if (str_contains_ci(line, "download")) return 1;

	if (str_contains_ci(line, "report loop")) return 1;

	if (str_contains_ci(line, "event buf")) return 1;

	if (str_contains_ci(line, "sweettouch")) return 1;

	if (str_contains_ci(line, "panic")) return 1;

	return 0;

}



/* ---------- QUP0 SE4 SPI Driver for Shim ---------- */
#define GCC_BASE             0x00100000UL
#define GCC_VOTE             0x52008u
#define GCC_VOTE_MASK        ((0xfu << 6) | (1u << 14))
#define GCC_RCG              0x174d0u
#define TLMM_SOUTH           0x0f500000UL
#define TLMM_NORTH           0x0f900000UL
#define SE4_BASE             0x00990000UL

#define SE_GENI_FORCE_DEFAULT 0x020
#define SE_GENI_OUTPUT_CTRL  0x024
#define SE_GENI_CGC_CTRL     0x028
#define SE_GENI_STATUS       0x040
#define SE_GENI_M_CLK_CFG    0x048
#define SE_GENI_FW_REVISION  0x068
#define SE_GENI_CLK_SEL      0x07c
#define SE_SPI_CPHA          0x224
#define SE_SPI_LOOPBACK      0x22c
#define SE_SPI_CPOL          0x230
#define SE_SPI_DEMUX_OUT_INV 0x24c
#define SE_SPI_DEMUX_SEL     0x250
#define SE_GENI_BYTE_GRAN    0x254
#define SE_GENI_DMA_MODE_EN  0x258
#define SE_SPI_TRANS_CFG     0x25c
#define SE_GENI_TX_PACK0     0x260
#define SE_GENI_TX_PACK1     0x264
#define SE_SPI_WORD_LEN      0x268
#define SE_SPI_TX_TRANS_LEN  0x26c
#define SE_SPI_RX_TRANS_LEN  0x270
#define SE_GENI_RX_PACK0     0x284
#define SE_GENI_RX_PACK1     0x288
#define SE_GENI_M_CMD0       0x600
#define SE_GENI_M_CMD_CTRL   0x604
#define SE_GENI_M_IRQ_STATUS 0x610
#define SE_GENI_M_IRQ_EN     0x614
#define SE_GENI_M_IRQ_CLEAR  0x618
#define SE_GENI_S_IRQ_EN     0x644
#define SE_GENI_S_IRQ_CLEAR  0x648
#define SE_GENI_TX_FIFO      0x700
#define SE_GENI_RX_FIFO      0x780
#define SE_GENI_TX_FIFO_STAT 0x800
#define SE_GENI_RX_FIFO_STAT 0x804
#define SE_GENI_TX_WM        0x80c
#define SE_GENI_RX_WM        0x810
#define SE_GENI_RX_RFR_WM    0x814
#define SE_GSI_EVENT_EN      0xe18
#define SE_IRQ_EN            0xe1c
#define SE_HW_PARAM_1        0xe28
#define SE_DMA_GENERAL_CFG   0xe30

#define SE_PACK0             0x0007f8feu
#define SE_PACK1             0x000ffefeu
#define GENI_SPI_FULL_DUPLEX 0x3
#define GENI_OPCODE_SHIFT    27

static inline uint32_t mm_rd(uintptr_t base, uint32_t off) { return *(volatile uint32_t *)(base + off); }
static inline void mm_wr(uintptr_t base, uint32_t off, uint32_t v) { *(volatile uint32_t *)(base + off) = v; }
static inline uint32_t se_rd(uint32_t off) { return mm_rd(SE4_BASE, off); }
static inline void se_wr(uint32_t off, uint32_t v) { mm_wr(SE4_BASE, off, v); }

static int shim_wait_clear(uintptr_t base, uint32_t off, uint32_t mask)
{
	for (unsigned i = 0; i < 100000; i++) {
		if (!(mm_rd(base, off) & mask)) return 1;
	}
	return 0;
}

static int shim_se4_init(void)
{
	/* 1. GCC clocks (same as sweet_touch 0090/0091) */
	mm_wr(GCC_BASE, GCC_VOTE, mm_rd(GCC_BASE, GCC_VOTE) | GCC_VOTE_MASK);
	uint32_t dfs = mm_rd(GCC_BASE, GCC_RCG + 0x14u);
	uint32_t cfg = mm_rd(GCC_BASE, GCC_RCG + 4u);
	int level = -1;
	if (dfs & 1u) {
		for (unsigned l = 0; l < 8; l++) {
			uint32_t lv = mm_rd(GCC_BASE, GCC_RCG + 0x1cu + 4u * l);
			if (level < 0 && (lv & 0x371fu) == 1u) level = (int)l;
		}
	} else {
		cfg = (cfg & ~(0x371fu | (1u << 20))) | 1u;
		mm_wr(GCC_BASE, GCC_RCG + 4u, cfg);
		mm_wr(GCC_BASE, GCC_RCG, mm_rd(GCC_BASE, GCC_RCG) | 1u);
		shim_wait_clear(GCC_BASE, GCC_RCG, 1u);
		level = 0;
	}
	if (level < 0) level = 0;
	static const uint32_t br[] = { 0x17004, 0x17008, 0x23000, 0x23008, 0x174cc };
	for (unsigned i = 0; i < 5; i++) {
		uint32_t cbcr = mm_rd(GCC_BASE, br[i]);
		if (!(cbcr & 2u)) shim_wait_clear(GCC_BASE, br[i], 1u << 31);
	}

	/* 2. North TLMM gpio8-11 -> qup4 (func=1, 6mA) */
	for (unsigned p = 8; p <= 11; p++) {
		uint32_t old = mm_rd(TLMM_NORTH, p * 0x1000u);
		mm_wr(TLMM_NORTH, p * 0x1000u, (old & ~0x3ffu) | (1u << 2) | (2u << 6));
	}

	/* 3. GENI SE4 SPI setup */
	if (se_rd(SE_GENI_STATUS) & 1u) {
		se_wr(SE_GENI_M_CMD_CTRL, 2);
		shim_wait_clear(SE4_BASE, SE_GENI_STATUS, 1u);
	}
	uint32_t hw1 = se_rd(SE_HW_PARAM_1);
	se_wr(SE_GSI_EVENT_EN, 0);
	se_wr(SE_IRQ_EN, 0);
	se_wr(SE_GENI_S_IRQ_EN, 0);
	se_wr(SE_GENI_M_IRQ_CLEAR, 0xffffffffu);
	se_wr(SE_GENI_S_IRQ_CLEAR, 0xffffffffu);
	se_wr(SE_GENI_DMA_MODE_EN, 0);
	se_wr(SE_GENI_CGC_CTRL, se_rd(SE_GENI_CGC_CTRL) | 0x7fu);
	se_wr(SE_DMA_GENERAL_CFG, se_rd(SE_DMA_GENERAL_CFG) | 0x0fu);
	se_wr(SE_GENI_OUTPUT_CTRL, 0x7f);
	se_wr(SE_GENI_FORCE_DEFAULT, 1);
	se_wr(SE_GENI_CLK_SEL, (uint32_t)level);
	se_wr(SE_GENI_M_CLK_CFG, (4u << 4) | 1u); /* 19.2/4 = 4.8 MHz */
	se_wr(SE_SPI_LOOPBACK, 0);
	se_wr(SE_SPI_CPHA, 0);
	se_wr(SE_SPI_CPOL, 0);
	se_wr(SE_SPI_DEMUX_OUT_INV, 0);
	se_wr(SE_SPI_DEMUX_SEL, 0);
	se_wr(SE_SPI_TRANS_CFG, 0);
	se_wr(SE_SPI_WORD_LEN, 4);
	se_wr(SE_GENI_BYTE_GRAN, 0);
	se_wr(SE_GENI_TX_PACK0, SE_PACK0);
	se_wr(SE_GENI_TX_PACK1, SE_PACK1);
	se_wr(SE_GENI_RX_PACK0, SE_PACK0);
	se_wr(SE_GENI_RX_PACK1, SE_PACK1);
	se_wr(SE_GENI_TX_WM, 0);
	se_wr(SE_GENI_RX_WM, 0);
	uint32_t rfr = ((hw1 >> 16) & 0x3fu);
	se_wr(SE_GENI_RX_RFR_WM, rfr ? rfr - 1u : 0xfu);
	se_wr(SE_GENI_M_IRQ_EN, 0x3fu | (3u << 26) | (1u << 30));
	return level;
}

static int shim_spi_xfer(const uint8_t *tx, uint8_t *rx, uint32_t len)
{
	if (len == 0) return -1;
	if (se_rd(SE_GENI_STATUS) & 1u) {
		se_wr(SE_GENI_M_CMD_CTRL, 2);
		shim_wait_clear(SE4_BASE, SE_GENI_STATUS, 1u);
	}
	while (se_rd(SE_GENI_RX_FIFO_STAT) & 0x1ffffffu)
		(void)se_rd(SE_GENI_RX_FIFO);
	se_wr(SE_GENI_M_IRQ_CLEAR, 0xffffffffu);

	se_wr(SE_SPI_TX_TRANS_LEN, len);
	se_wr(SE_SPI_RX_TRANS_LEN, len);
	se_wr(SE_GENI_M_CMD0, (uint32_t)GENI_SPI_FULL_DUPLEX << GENI_OPCODE_SHIFT);

	uint32_t txi = 0, rxi = 0;
	int done = 0;
	for (unsigned idle = 0; idle < 200000; idle++) {
		uint32_t prev = txi + rxi;
		while (txi < len && (se_rd(SE_GENI_TX_FIFO_STAT) & 0x1ffffffu) < 8) {
			uint32_t word = 0;
			for (int b = 0; b < 4 && txi < len; b++)
				word |= ((uint32_t)tx[txi++]) << (b * 8);
			se_wr(SE_GENI_TX_FIFO, word);
		}
		uint32_t n = se_rd(SE_GENI_RX_FIFO_STAT) & 0x1ffffffu;
		while (n-- && rxi < len) {
			uint32_t word = se_rd(SE_GENI_RX_FIFO);
			for (int b = 0; b < 4 && rxi < len; b++) {
				if (rx) rx[rxi] = (word >> (b * 8)) & 0xff;
				rxi++;
			}
		}
		uint32_t irq = se_rd(SE_GENI_M_IRQ_STATUS);
		if (irq & 0x3eu) {
			se_wr(SE_GENI_M_IRQ_CLEAR, irq);
			se_wr(SE_GENI_M_CMD_CTRL, 2);
			return -(int)(irq & 0x3eu);
		}
		if (irq) {
			se_wr(SE_GENI_M_IRQ_CLEAR, irq);
			if (irq & 1u) done = 1;
		}
		if (done && rxi >= len) return 0;
		if (txi + rxi != prev) idle = 0;
	}
	se_wr(SE_GENI_M_CMD_CTRL, 2);
	return -2;
}

static int shim_nvt_set_page(uint32_t addr)
{
	uint8_t tx[3] = { 0xff, (uint8_t)(addr >> 15), (uint8_t)(addr >> 7) };
	return shim_spi_xfer(tx, 0, 3);
}

static int shim_nvt_write_addr(uint32_t addr, uint8_t val)
{
	if (shim_nvt_set_page(addr)) return -1;
	uint8_t tx[2] = { (uint8_t)((addr & 0x7f) | 0x80), val };
	return shim_spi_xfer(tx, 0, 2);
}

static int shim_nvt_read(uint32_t addr, uint8_t *out, uint32_t len)
{
	if (shim_nvt_set_page(addr)) return -1;
	uint8_t tx[128] = { 0 };
	uint8_t rx[128] = { 0 };
	if (len + 2 > sizeof(tx)) return -1;
	tx[0] = (uint8_t)(addr & 0x7f);
	int rc = shim_spi_xfer(tx, rx, len + 2);
	if (rc) return rc;
	for (uint32_t i = 0; i < len; i++) out[i] = rx[i + 2];
	return 0;
}

static uint8_t nvt_spi_tx_chunk[64513];

static int shim_nvt_write_buf(uint32_t addr, const uint8_t *buf, uint32_t len)
{
	if (len == 0 || len > 64512u) return -1;
	if (shim_nvt_set_page(addr)) return -1;
	nvt_spi_tx_chunk[0] = (uint8_t)((addr & 0x7fu) | 0x80u);
	for (uint32_t i = 0; i < len; i++) nvt_spi_tx_chunk[1u + i] = buf[i];
	return shim_spi_xfer(nvt_spi_tx_chunk, 0, 1u + len);
}

static int shim_nvt_write_le3(uint32_t addr, uint32_t val)
{
	uint8_t b[3] = { (uint8_t)val, (uint8_t)(val >> 8), (uint8_t)(val >> 16) };
	return shim_nvt_write_buf(addr, b, 3);
}

static int shim_nvt_write_le4(uint32_t addr, uint32_t val)
{
	uint8_t b[4] = { (uint8_t)val, (uint8_t)(val >> 8), (uint8_t)(val >> 16), (uint8_t)(val >> 24) };
	return shim_nvt_write_buf(addr, b, 4);
}

static uint32_t rd_le32(const uint8_t *p)
{
	return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

extern const uint8_t nvt_fw_csot_start[], nvt_fw_csot_end[];
extern const uint8_t nvt_fw_tm_start[], nvt_fw_tm_end[];

static int nt36532_upload_and_boot(const char *tag, const uint8_t *fw, uint32_t fw_len, int include_hdr2)
{
	if (fw_len < 0x36100u) {
		con_str(tag, "fw too small");
		return -1;
	}
	uint32_t ilm_bin  = rd_le32(fw + 0x00), ilm_sram = rd_le32(fw + 0x04), ilm_size = rd_le32(fw + 0x08);
	uint32_t dlm_bin  = rd_le32(fw + 0x0c), dlm_sram = rd_le32(fw + 0x10), dlm_size = rd_le32(fw + 0x14);
	uint32_t ilm_crc  = rd_le32(fw + 0x18), dlm_crc  = rd_le32(fw + 0x1c);
	uint32_t h1_sram  = rd_le32(fw + 0xf0), h1_size  = rd_le32(fw + 0xf4), h1_bin   = rd_le32(fw + 0xf8);
	uint32_t h2_sram  = rd_le32(fw + 0x1f0), h2_size = rd_le32(fw + 0x1f4), h2_bin  = rd_le32(fw + 0x1f8);

	/* 1. Bootloader reset */
	shim_nvt_write_addr(0x1FB43Eu, 0x69);
	con_delay_ms(5);

	/* 2. Write ILM & DLM destination, length (3B LE), and golden CRC (4B LE) */
	shim_nvt_write_le3(0x1FB528u, ilm_sram);
	shim_nvt_write_le3(0x1FB518u, ilm_size);
	shim_nvt_write_le4(0x1FB500u, ilm_crc);

	shim_nvt_write_le3(0x1FB52Cu, dlm_sram);
	shim_nvt_write_le3(0x1FB530u, dlm_size);
	shim_nvt_write_le4(0x1FB504u, dlm_crc);

	/* 3. Enable HW CRC auto-copy BEFORE SRAM writes */
	shim_nvt_write_addr(0x1FC925u, 0x56);

	/* 4. Download ILM, DLM, Header1 (and optionally Header2) in <= 0xFC00 chunks */
	struct { uint32_t bin, sram, size; } parts[4] = {
		{ ilm_bin, ilm_sram, ilm_size },
		{ dlm_bin, dlm_sram, dlm_size },
		{ h1_bin,  h1_sram,  h1_size  },
		{ h2_bin,  h2_sram,  include_hdr2 ? h2_size : 0 }
	};
	int rc_dl = 0;
	for (int p = 0; p < 4; p++) {
		if (parts[p].size == 0) continue;
		uint32_t rem = parts[p].size + 1u;
		uint32_t boff = parts[p].bin;
		uint32_t soff = parts[p].sram;
		if (boff + rem > fw_len) { rc_dl = -99; break; }
		while (rem > 0) {
			uint32_t chunk = (rem > 0xFC00u) ? 0xFC00u : rem;
			int rc = shim_nvt_write_buf(soff, fw + boff, chunk);
			if (rc) { rc_dl = rc; break; }
			soff += chunk;
			boff += chunk;
			rem -= chunk;
		}
		if (rc_dl) break;
	}

	/* 5. Poll 0x1FC925 == 0x00 */
	uint8_t crc_done = 0xff;
	int crc_tries = 0;
	for (; crc_tries < 200; crc_tries++) {
		if (shim_nvt_read(0x1FC925u, &crc_done, 1) == 0 && crc_done == 0) break;
		con_delay_ms(1);
	}
	uint8_t rb_ilm[4] = {0}, rb_dlm[4] = {0}, bld_crc = 0xff;
	shim_nvt_read(0x1FB520u, rb_ilm, 4);
	shim_nvt_read(0x1FB524u, rb_dlm, 4);
	shim_nvt_read(0x1FB533u, &bld_crc, 1);
	uint32_t r_ilm = rd_le32(rb_ilm), r_dlm = rd_le32(rb_dlm);
	con_hex2(tag, "dl_rc=", (uint32_t)rc_dl, "1FC925/tries=", ((uint32_t)crc_done << 16) | (uint32_t)crc_tries);
	con_hex2("ILM CRC", "gold=", ilm_crc, "read=", r_ilm);
	con_hex2("DLM CRC", "gold=", dlm_crc, "read=", r_dlm);

	/* 6. Clear reset buf (0x125860[0..5] = 0), write [0xAE, 0x00] to 0x125850 */
	static const uint8_t z6[6] = {0};
	static const uint8_t ae[2] = { 0xAE, 0x00 };
	shim_nvt_write_buf(0x125860u, z6, 6);
	shim_nvt_write_buf(0x125850u, ae, 2);

	/* 7. Boot ready (carrier_system=2: keep 0x1FB50D=1) */
	shim_nvt_write_addr(0x1FB50Du, 0x01);
	con_delay_ms(20);

	/* 8. Poll 0x125860 until 0xA0..0xAF */
	uint8_t rst[6] = {0};
	int rst_ok = 0, rst_tries = 0;
	for (rst_tries = 1; rst_tries <= 20; rst_tries++) {
		con_delay_ms(10);
		if (shim_nvt_read(0x125860u, rst, 5) == 0 && rst[0] >= 0xA0 && rst[0] <= 0xAF) {
			rst_ok = 1;
			break;
		}
	}
	uint32_t rst_w = ((uint32_t)rst[0] << 24) | ((uint32_t)rst[1] << 16) | ((uint32_t)rst[2] << 8) | rst[3];
	con_hex2("RST 125860", "bytes0-3=", rst_w, "tries/ok=", ((uint32_t)rst_tries << 8) | (uint32_t)rst_ok);

	/* 9. Read 0x125878 FWINFO (37 bytes) */
	uint8_t info[40] = {0};
	shim_nvt_read(0x125878u, info, 37);
	uint32_t iw0 = ((uint32_t)info[0] << 24) | ((uint32_t)info[1] << 16) | ((uint32_t)info[2] << 8) | info[3];
	uint32_t iw1 = ((uint32_t)info[4] << 24) | ((uint32_t)info[5] << 16) | ((uint32_t)info[6] << 8) | info[7];
	con_hex2("FWINFO 78", "ver/nf/x/y=", iw0, "abs_xy=", iw1);

	int info_ok = ((uint8_t)(info[0] + info[1]) == 0xFFu) && (info[0] != 0 || info[1] != 0);
	return (crc_done == 0 && r_ilm == ilm_crc && r_dlm == dlm_crc && rst_ok && info_ok) ? 0 : -1;
}

static void nt36532_poll_touch(const char *tag, unsigned seconds)
{
	con_puts(tag, GREEN);
	uint8_t buf[68];
	uint32_t prev_irq = mm_rd(TLMM_SOUTH, 39 * 0x1000u + 4u) & 1u;
	uint32_t irq_lows = 0, pkts = 0, good_pts = 0, raw_prints = 0;
	uint16_t last_x = 0xffff, last_y = 0xffff;
	uint8_t last_st = 0xff;

	for (unsigned t = 0; t < seconds * 200u; t++) {
		uint32_t irq = mm_rd(TLMM_SOUTH, 39 * 0x1000u + 4u) & 1u;
		if (irq == 0) irq_lows++;
		int do_read = (irq == 0) || (irq != prev_irq) || ((t % 10u) == 0);
		prev_irq = irq;
		if (do_read && shim_nvt_read(0x125800u, buf, 65) == 0) {
			uint8_t sum = 0;
			for (int k = 0; k < 64; k++) sum = (uint8_t)(sum + buf[k]);
			int csum_ok = ((uint8_t)(-sum) == buf[64]);
			int any_nz = 0;
			for (int s = 0; s < 10; s++) {
				const uint8_t *p = buf + s * 6;
				if (p[0] == 0 || p[0] == 0xff) continue;
				any_nz = 1;
				uint8_t id = p[0] >> 3, st = p[0] & 7;
				uint16_t x10 = ((uint16_t)p[1] << 8) | p[2];
				uint16_t y10 = ((uint16_t)p[3] << 8) | p[4];
				if (id >= 1 && id <= 10 && (st == 1 || st == 2)) {
					pkts++;
					if ((x10 != last_x || y10 != last_y || st != last_st) && good_pts < 12) {
						con_hex2("TOUCH PT", "id/st/ok=", ((uint32_t)id << 16) | ((uint32_t)st << 8) | (uint32_t)csum_ok,
							 "x10/y10=", ((uint32_t)x10 << 16) | y10);
						last_x = x10; last_y = y10; last_st = st;
						good_pts++;
					}
					break;
				}
			}
			if (!any_nz && buf[0] != 0 && buf[0] != 0xff && raw_prints < 3) {
				uint32_t w0 = ((uint32_t)buf[0] << 24) | ((uint32_t)buf[1] << 16) | ((uint32_t)buf[2] << 8) | buf[3];
				uint32_t w1 = ((uint32_t)buf[4] << 24) | ((uint32_t)buf[5] << 16) | ((uint32_t)buf[64] << 8) | (uint8_t)(-sum);
				con_hex2("TOUCH RAW", "b0-3=", w0, "b4-5/cs=", w1);
				raw_prints++;
			}
		}
		con_delay_ms(5);
	}
	con_hex2("TOUCH STAT", "irq_lows=", irq_lows, "pts/shown=", (pkts << 16) | good_pts);
}

static void probe_trim_set(const char *tag, uint32_t swrst, uint32_t rdfast, uint32_t trim_addr)
{
	int rc_r = shim_nvt_write_addr(swrst, 0x69);
	con_delay_ms(20);
	int rc_f = rdfast ? shim_nvt_write_addr(rdfast, 0x00) : 0;
	uint8_t b[8] = { 0 };
	int rc_t = shim_nvt_read(trim_addr, b, 6);
	uint32_t w0 = (b[0]<<24)|(b[1]<<16)|(b[2]<<8)|b[3];
	uint32_t w1 = (b[4]<<8)|b[5];
	(void)rc_r; (void)rc_f;
	if (rc_t) {
		con_hex2(tag, "rc=", (uint32_t)rc_t, "addr=", trim_addr);
	} else {
		con_hex2(tag, "id0-3=", w0, "id4-5=", w1);
	}
}

static void live_hardware_probe(uint64_t dtb)
{
	con_set_scale(2);
	con_puts("=== LIVE NT36532 FW + TOUCH PROBE ===", YELLOW);

	/* Detect panel from ABL /chosen/bootargs */
	char bootargs[512];
	uint32_t ba_len = fdt_chosen_str(dtb, "bootargs", bootargs, sizeof(bootargs));
	int is_tm = str_contains_ci(bootargs, "_36_");
	int is_csot = str_contains_ci(bootargs, "_42_");
	const char *dsi = 0;
	for (uint32_t i = 0; i + 4 < ba_len; i++) {
		if (bootargs[i] == 'm' && bootargs[i+1] == '8' && bootargs[i+2] == '2' && bootargs[i+3] == '_') {
			dsi = &bootargs[i > 8 ? i - 8 : 0];
			break;
		}
	}
	if (dsi) {
		char snip[64];
		int k = 0;
		for (; k < 60 && dsi[k] && dsi[k] != ' '; k++) snip[k] = dsi[k];
		snip[k] = 0;
		con_str("PANEL DT", snip);
	} else {
		con_hex2("PANEL DT", "ba_len=", ba_len, "tm/csot=", (uint32_t)((is_tm << 8) | is_csot));
	}

	/* SOUTH tile TLMM (0x0F500000) GPIO 33 RST out, GPIO 39 IRQ input pull-up */
	mm_wr(TLMM_SOUTH, 33 * 0x1000u, (2u << 6) | 0x200u);
	mm_wr(TLMM_SOUTH, 39 * 0x1000u, 3u); /* pull-up input (active-low IRQ 0x2001) */
	mm_wr(TLMM_SOUTH, 33 * 0x1000u + 4u, 0);
	con_delay_ms(15);
	mm_wr(TLMM_SOUTH, 33 * 0x1000u + 4u, 2u);
	con_delay_ms(30);

	int lvl = shim_se4_init();
	con_hex2("SE4 INIT", "clk_sel=", (uint32_t)lvl, "fw=", se_rd(SE_GENI_FW_REVISION));
	probe_trim_set("NT36532 1FB104", 0x1FB43Eu, 0x1FB535u, 0x1FB104u);

	uint32_t csot_len = (uint32_t)(nvt_fw_csot_end - nvt_fw_csot_start);
	uint32_t tm_len   = (uint32_t)(nvt_fw_tm_end - nvt_fw_tm_start);
	const uint8_t *pri_fw = is_tm ? nvt_fw_tm_start : nvt_fw_csot_start;
	uint32_t pri_len      = is_tm ? tm_len : csot_len;
	const char *pri_name  = is_tm ? "M82 TM" : "M82 CSOT";
	const uint8_t *alt_fw = is_tm ? nvt_fw_csot_start : nvt_fw_tm_start;
	uint32_t alt_len      = is_tm ? csot_len : tm_len;
	const char *alt_name  = is_tm ? "M82 CSOT" : "M82 TM";

	int ok = nt36532_upload_and_boot(pri_name, pri_fw, pri_len, 0);
	if (ok == 0) {
		nt36532_poll_touch("READY! TAP/SWIPE SCREEN NOW FOR 15s!", 15);
	} else {
		con_puts("Primary check failed/incomplete, trying Header2 + alt...", RED);
		if (nt36532_upload_and_boot("ALT+H2", pri_fw, pri_len, 1) == 0) {
			nt36532_poll_touch("READY (with H2)! TAP/SWIPE 15s!", 15);
		} else if (nt36532_upload_and_boot(alt_name, alt_fw, alt_len, 0) == 0) {
			nt36532_poll_touch("READY (ALT FW)! TAP/SWIPE 15s!", 15);
		} else {
			nt36532_poll_touch("POLLING RAW ANYWAY (10s)...", 10);
		}
	}
}

struct sweet_log_header {
	char magic[8];
	uint32_t version, size;
	uint64_t total, seq;
};

static void dump_sweetlog(uint32_t ufirst, uint32_t ulast, uint64_t dtb)
{
	(void)ufirst;
	uint32_t hdr_lba = ulast - 1024;
	con_skip();
	con_hex2("SWEETLOG", "check lba=", hdr_lba, "last=", ulast);

	uint32_t rc = read_blocks(hdr_lba, 1, data);
	if (rc) {
		con_clear(BLUE);
		con_hex2("SWEETLOG", "read err rc=", rc, "lba=", hdr_lba);
		live_hardware_probe(dtb);
		con_delay_ms(300000);
		return;
	}

	struct sweet_log_header *hdr = (struct sweet_log_header *)data;
	int found = 0;
	if (hdr->magic[0] == 'S' && hdr->magic[1] == 'W' && hdr->magic[2] == 'E' &&
	    hdr->magic[3] == 'E' && hdr->magic[4] == 'T' && hdr->magic[5] == 'L' &&
	    hdr->magic[6] == 'O' && hdr->magic[7] == 'G') {
		found = 1;
	} else {
		rc = read_blocks(hdr_lba - 1, 1, data);
		if (!rc && hdr->magic[0] == 'S' && hdr->magic[1] == 'W' && hdr->magic[2] == 'E') {
			hdr_lba = hdr_lba - 1;
			found = 1;
		}
	}

	uint32_t log_size = found ? hdr->size : 0;
	uint64_t total = found ? hdr->total : 0;
	uint64_t seq = found ? hdr->seq : 0;

	con_clear(BLUE);
	live_hardware_probe(dtb);
	if (!found) {
		con_puts("No SWEETLOG ring found. Screen held 5 min.", YELLOW);
		con_delay_ms(300000);
		return;
	}
	con_skip();
	con_puts("=== KERNEL LOG (LAST 10 MATCHES) ===", YELLOW);
	con_hex2("LOG STATS", "seq=", (uint32_t)seq, "total_bytes=", (uint32_t)total);

	uint32_t ring_lba = hdr_lba + 1;
	uint32_t total_blocks = (log_size + BLOCK - 1) / BLOCK;
	if (total_blocks > 1024) total_blocks = 1024;

	uint32_t head_block = (uint32_t)((total % log_size) / BLOCK);
	uint32_t start_blk = 0;
	uint32_t blocks_to_scan = 1;
	if (total >= log_size) {
		start_blk = (head_block + 1) % total_blocks;
		blocks_to_scan = total_blocks;
	} else {
		start_blk = 0;
		blocks_to_scan = head_block + 1;
		if (blocks_to_scan > total_blocks) blocks_to_scan = total_blocks;
	}

	char line[256];
	int line_len = 0;
	int match_count = 0;

#define MAX_TAIL_LINES 10
	static char tail_lines[MAX_TAIL_LINES][256];
	static uint32_t tail_fgs[MAX_TAIL_LINES];
	int tail_total = 0;

	for (uint32_t i_blk = 0; i_blk < blocks_to_scan; i_blk += CHUNK_BLOCKS) {
		uint32_t count = (blocks_to_scan - i_blk < CHUNK_BLOCKS) ? (blocks_to_scan - i_blk) : CHUNK_BLOCKS;
		uint32_t blk = (start_blk + i_blk) % total_blocks;
		if (blk + count > total_blocks) count = total_blocks - blk;
		rc = read_blocks(ring_lba + blk, count, chunk_buf);
		if (rc) break;

		for (uint32_t i = 0; i < count * BLOCK; i++) {
			char c = (char)chunk_buf[i];
			if (c == 0 || c == '\r') continue;
			if (c == '\n') {
				line[line_len] = 0;
				if (line_len > 0 && is_diagnostic_line(line)) {
					match_count++;
					uint32_t fg = WHITE;
					if (str_contains_ci(line, "touch")) fg = YELLOW;
					else if (str_contains_ci(line, "crash") || str_contains_ci(line, "fail") || str_contains_ci(line, "err")) fg = RED;
					int slot = tail_total % MAX_TAIL_LINES;
					for (int k = 0; k < line_len && k < 255; k++) tail_lines[slot][k] = line[k];
					tail_lines[slot][line_len < 255 ? line_len : 255] = 0;
					tail_fgs[slot] = fg;
					tail_total++;
				}
				line_len = 0;
			} else if (line_len < 250) {
				line[line_len++] = (c >= 32 && c < 127) ? c : '?';
			}
		}
	}

	int show_cnt = (tail_total < MAX_TAIL_LINES) ? tail_total : MAX_TAIL_LINES;
	int start_slot = (tail_total < MAX_TAIL_LINES) ? 0 : (tail_total % MAX_TAIL_LINES);
	for (int i = 0; i < show_cnt; i++) {
		int slot = (start_slot + i) % MAX_TAIL_LINES;
		con_puts(tail_lines[slot], tail_fgs[slot]);
	}

	con_skip();
	con_hex2("DUMP DONE", "matches=", (uint32_t)match_count, "seq=", (uint32_t)seq);
	con_puts("Screen held for 5 minutes. Take photos.", YELLOW);
	con_delay_ms(300000);
}

void test_ufs(uint64_t dtb)

{

	con_str("UFS", "milestone 5 probe (read-only)");

	uint32_t cap = ufs_rd(REG_CAP), hcs = ufs_rd(REG_HCS), hce = ufs_rd(REG_HCE);

	con_hex2("UFS", "ver=", ufs_rd(REG_VER), "cap=", cap);

	con_hex2("UFS", "hce=", hce, "hcs=", hcs);

	con_hex2("UFS", "is=", ufs_rd(REG_IS), "rsr=", ufs_rd(REG_UTRLRSR));

	con_hex2("UFS", "utrlba=", ufs_rd(REG_UTRLBA), "hi=", ufs_rd(REG_UTRLBAU));

	con_hex2("UFS", "dbr=", ufs_rd(REG_UTRLDBR), "nutrs=", (cap & 0x1f) + 1);



	if (!(hce & 1) || !(hcs & HCS_DP) || !(hcs & HCS_UTRLRDY)) {

		con_str("UFS", "host not enabled/ready: stop");

		return;

	}

	if (ufs_rd(REG_UTRLDBR)) {

		con_str("UFS", "requests outstanding: stop");

		return;

	}



	/* Take over the transfer request list: stop, point at ours, run. */

	ufs_wr(REG_UTRLRSR, 0);

	uintptr_t list = (uintptr_t)utrl;

	ufs_wr(REG_UTRLBA, (uint32_t)list);

	ufs_wr(REG_UTRLBAU, (uint32_t)(list >> 32));

	ufs_wr(REG_UTRLRSR, 1);

	con_hex2("UFS", "ours=", (uint32_t)list, "rsr=", ufs_rd(REG_UTRLRSR));



	if (ufs_rd(REG_UTRLBA) != (uint32_t)list || ufs_rd(REG_UTRLBAU) != (uint32_t)(list >> 32) ||

	    !(ufs_rd(REG_UTRLRSR) & 1)) {

		con_str("UFS", "list base not taken: stop");

		return;

	}



	uint32_t ocs = 0, st = 0, rc;

	static const uint8_t tur[6] = { 0x00 };

	for (int i = 0; i < 3; i++) {

		rc = scsi(0, tur, sizeof(tur), 0, 0, &ocs, &st);

		con_hex2("UFS TUR", "rc=", rc, "ocs/st=", ocs << 8 | st);

		if (!rc)

			break;

	}



	static const uint8_t cap10[10] = { 0x25 };	/* READ CAPACITY(10) */

	rc = scsi(0, cap10, sizeof(cap10), data, 8, &ocs, &st);

	con_hex2("UFS CAP", "rc=", rc, "ocs/st=", ocs << 8 | st);

	if (!rc)

		con_hex2("UFS LUN0", "lastlba=", be32(data), "blk=", be32(data + 4));



	/* READ(10) LBA 0, 2 blocks: protective MBR and the GPT header. */

	static const uint8_t rd10[10] = { 0x28, 0, 0, 0, 0, 0, 0, 0, 2, 0 };

	rc = scsi(0, rd10, sizeof(rd10), data, 2 * BLOCK, &ocs, &st);

	con_hex2("UFS READ", "rc=", rc, "ocs/st=", ocs << 8 | st);

	if (rc)

		return;

	con_hex2("UFS MBR", "sig=", data[510] | data[511] << 8, "type=", data[450]);

	const uint8_t *gpt = data + BLOCK;

	char sig[9];

	for (int i = 0; i < 8; i++)

		sig[i] = (gpt[i] >= 32 && gpt[i] < 127) ? gpt[i] : '.';

	sig[8] = 0;

	con_str("UFS GPT", sig);

	uint32_t nent = gpt[80] | gpt[81] << 8 | gpt[82] << 16, esz = gpt[84] | gpt[85] << 8;

	uint32_t elba = gpt[72] | gpt[73] << 8;

	con_hex2("UFS GPT", "entries=", nent, "lba=", elba);

	if (esz != 128 || nent > 128)

		return;

	list_partitions(elba, nent);



	if (userdata_last) {

		dump_sweetlog(userdata_first, userdata_last, dtb);

	} else {

		con_clear(BLUE);
		live_hardware_probe(dtb);
		con_str("SWEETLOG", "userdata not found in GPT");
		con_delay_ms(300000);

	}

}

