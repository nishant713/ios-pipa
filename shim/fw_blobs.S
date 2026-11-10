	.section .rodata
	.balign 16
	.globl nvt_fw_csot_start
	.globl nvt_fw_csot_end
nvt_fw_csot_start:
	.incbin "../research/pipa-fw/novatek_nt36532_m82_fw_csot.bin"
nvt_fw_csot_end:

	.balign 16
	.globl nvt_fw_tm_start
	.globl nvt_fw_tm_end
nvt_fw_tm_start:
	.incbin "../research/pipa-fw/novatek_nt36532_m82_fw_tm.bin"
nvt_fw_tm_end:
