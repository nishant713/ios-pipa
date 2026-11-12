import os

path = r"C:\Users\nisha\.gemini\antigravity\scratch\ios-pipa\shim\ufs.c"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

target = """\tuint32_t head_block = (uint32_t)((total % log_size) / BLOCK);
\tuint32_t blocks_to_scan = 150;
\tuint32_t start_blk = 0;
\tif (total > log_size) {
\t\tstart_blk = (head_block + total_blocks - blocks_to_scan) % total_blocks;
\t} else if (head_block > blocks_to_scan) {
\t\tstart_blk = head_block - blocks_to_scan;
\t} else {
\t\tblocks_to_scan = (head_block > 0) ? head_block : 1;
\t\tstart_blk = 0;
\t}

\tchar line[256];
\tint line_len = 0;
\tint match_count = 0;

\tfor (uint32_t i_blk = 0; i_blk < blocks_to_scan; i_blk += CHUNK_BLOCKS) {
\t\tuint32_t count = (blocks_to_scan - i_blk < CHUNK_BLOCKS) ? (blocks_to_scan - i_blk) : CHUNK_BLOCKS;
\t\tuint32_t blk = (start_blk + i_blk) % total_blocks;
\t\trc = read_blocks(ring_lba + blk, count, chunk_buf);
\t\tif (rc) break;

\t\tfor (uint32_t i = 0; i < count * BLOCK; i++) {
\t\t\tchar c = (char)chunk_buf[i];
\t\t\tif (c == 0) continue;
\t\t\tif (c == '\\r') continue;
\t\t\tif (c == '\\n') {
\t\t\t\tline[line_len] = 0;
\t\t\t\tif (line_len > 0) {
\t\t\t\t\tif (is_diagnostic_line(line)) {
\t\t\t\t\t\tmatch_count++;
\t\t\t\t\t\tuint32_t fg = WHITE;
\t\t\t\t\t\tif (str_contains_ci(line, "touch")) fg = YELLOW;
\t\t\t\t\t\telse if (str_contains_ci(line, "crash")) fg = RED;
\t\t\t\t\t\telse if (str_contains_ci(line, "fail") || str_contains_ci(line, "err")) fg = RED;
\t\t\t\t\t\tcon_puts(line, fg);
\t\t\t\t\t}
\t\t\t\t}
\t\t\t\tline_len = 0;
\t\t\t} else {
\t\t\t\tif (line_len < 250) {
\t\t\t\t\tline[line_len++] = (c >= 32 && c < 127) ? c : '?';
\t\t\t\t}
\t\t\t}
\t\t}
\t}"""

replacement = """\tuint32_t head_block = (uint32_t)((total % log_size) / BLOCK);
\tuint32_t tail_blocks = 25;
\tuint32_t start_blk = 0;
\tuint32_t blocks_to_scan = tail_blocks;
\tif (total > log_size) {
\t\tstart_blk = (head_block + total_blocks - tail_blocks) % total_blocks;
\t} else if (head_block > tail_blocks) {
\t\tstart_blk = head_block - tail_blocks;
\t} else {
\t\tstart_blk = 0;
\t\tblocks_to_scan = (head_block > 0) ? head_block : 1;
\t}

\tchar line[256];
\tint line_len = 0;
\tint match_count = 0;

#define MAX_TAIL_LINES 42
\tstatic char tail_lines[MAX_TAIL_LINES][256];
\tstatic uint32_t tail_fgs[MAX_TAIL_LINES];
\tint tail_total = 0;

\tfor (uint32_t i_blk = 0; i_blk < blocks_to_scan; i_blk += CHUNK_BLOCKS) {
\t\tuint32_t count = (blocks_to_scan - i_blk < CHUNK_BLOCKS) ? (blocks_to_scan - i_blk) : CHUNK_BLOCKS;
\t\tuint32_t blk = (start_blk + i_blk) % total_blocks;
\t\trc = read_blocks(ring_lba + blk, count, chunk_buf);
\t\tif (rc) break;

\t\tfor (uint32_t i = 0; i < count * BLOCK; i++) {
\t\t\tchar c = (char)chunk_buf[i];
\t\t\tif (c == 0) continue;
\t\t\tif (c == '\\r') continue;
\t\t\tif (c == '\\n') {
\t\t\t\tline[line_len] = 0;
\t\t\t\tif (line_len > 0) {
\t\t\t\t\tif (is_diagnostic_line(line)) {
\t\t\t\t\t\tmatch_count++;
\t\t\t\t\t\tuint32_t fg = WHITE;
\t\t\t\t\t\tif (str_contains_ci(line, "touch")) fg = YELLOW;
\t\t\t\t\t\telse if (str_contains_ci(line, "crash")) fg = RED;
\t\t\t\t\t\telse if (str_contains_ci(line, "fail") || str_contains_ci(line, "err")) fg = RED;
\t\t\t\t\t\tint slot = tail_total % MAX_TAIL_LINES;
\t\t\t\t\t\tfor (int k = 0; k < line_len && k < 255; k++) tail_lines[slot][k] = line[k];
\t\t\t\t\t\ttail_lines[slot][line_len < 255 ? line_len : 255] = 0;
\t\t\t\t\t\ttail_fgs[slot] = fg;
\t\t\t\t\t\ttail_total++;
\t\t\t\t\t}
\t\t\t\t}
\t\t\t\tline_len = 0;
\t\t\t} else {
\t\t\t\tif (line_len < 250) {
\t\t\t\t\tline[line_len++] = (c >= 32 && c < 127) ? c : '?';
\t\t\t\t}
\t\t\t}
\t\t}
\t}

\tint show_cnt = (tail_total < MAX_TAIL_LINES) ? tail_total : MAX_TAIL_LINES;
\tint start_slot = (tail_total < MAX_TAIL_LINES) ? 0 : (tail_total % MAX_TAIL_LINES);
\tfor (int i = 0; i < show_cnt; i++) {
\t\tint slot = (start_slot + i) % MAX_TAIL_LINES;
\t\tcon_puts(tail_lines[slot], tail_fgs[slot]);
\t}"""

# Normalise newlines for replacement
c_norm = content.replace("\r\n", "\n")
assert target in c_norm, "Target not found in normalized content"
c_new = c_norm.replace(target, replacement).replace("\n", "\r\n")

with open(path, "w", encoding="utf-8") as f:
    f.write(c_new)

print("Patch applied cleanly to shim/ufs.c!")
