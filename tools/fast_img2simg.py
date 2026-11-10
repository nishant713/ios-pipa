import sys
import struct
import os
import time

SPARSE_HEADER_MAGIC = 0xED26FF3A
CHUNK_TYPE_RAW = 0xCAC1
CHUNK_TYPE_FILL = 0xCAC2
CHUNK_TYPE_DONT_CARE = 0xCAC3
CHUNK_TYPE_CRC32 = 0xCAC4

BLK_SIZE = 4096
CHUNK_MAX_BLKS = 32768 # 128MB max per raw chunk

def img2simg(src_path, dst_path):
    t0 = time.time()
    file_size = os.path.getsize(src_path)
    if file_size % BLK_SIZE != 0:
        raise ValueError(f"File size {file_size} is not a multiple of {BLK_SIZE}")
    
    total_blocks = file_size // BLK_SIZE
    print(f"Converting {src_path} ({file_size / (1024**3):.2f} GiB, {total_blocks} blocks) to sparse format...")
    
    with open(src_path, 'rb') as fin, open(dst_path, 'wb') as fout:
        # Write placeholder header (28 bytes)
        fout.write(b'\x00' * 28)
        
        chunk_count = 0
        current_type = None
        current_blocks = 0
        raw_chunks_data = []
        raw_bytes_count = 0
        
        def flush_chunk():
            nonlocal chunk_count, current_type, current_blocks, raw_chunks_data, raw_bytes_count
            if current_blocks == 0:
                return
            if current_type == CHUNK_TYPE_DONT_CARE:
                # DONT_CARE chunk
                hdr = struct.pack('<2H2I', CHUNK_TYPE_DONT_CARE, 0, current_blocks, 12)
                fout.write(hdr)
                chunk_count += 1
            elif current_type == CHUNK_TYPE_RAW:
                # RAW chunk
                hdr = struct.pack('<2H2I', CHUNK_TYPE_RAW, 0, current_blocks, 12 + raw_bytes_count)
                fout.write(hdr)
                for part in raw_chunks_data:
                    fout.write(part)
                chunk_count += 1
            current_type = None
            current_blocks = 0
            raw_chunks_data.clear()
            raw_bytes_count = 0

        # Read in 4MB chunks (1024 blocks)
        READ_BLKS = 1024
        READ_BYTES = READ_BLKS * BLK_SIZE
        ZERO_CHUNK = b'\x00' * READ_BYTES
        ZERO_BLK = b'\x00' * BLK_SIZE
        
        processed_bytes = 0
        last_report = t0
        
        while True:
            buf = fin.read(READ_BYTES)
            if not buf:
                break
            
            # Fast path: entire 4MB buffer is all zero
            if buf == ZERO_CHUNK:
                blks = READ_BLKS
                if current_type == CHUNK_TYPE_DONT_CARE:
                    current_blocks += blks
                else:
                    flush_chunk()
                    current_type = CHUNK_TYPE_DONT_CARE
                    current_blocks = blks
            else:
                # Process block by block within buffer
                num_blks = len(buf) // BLK_SIZE
                for i in range(num_blks):
                    blk = buf[i*BLK_SIZE : (i+1)*BLK_SIZE]
                    is_zero = (blk == ZERO_BLK)
                    blk_type = CHUNK_TYPE_DONT_CARE if is_zero else CHUNK_TYPE_RAW
                    
                    if current_type is None:
                        current_type = blk_type
                        current_blocks = 1
                        if blk_type == CHUNK_TYPE_RAW:
                            raw_chunks_data.append(blk)
                            raw_bytes_count += BLK_SIZE
                    elif current_type == blk_type:
                        if blk_type == CHUNK_TYPE_RAW and current_blocks >= CHUNK_MAX_BLKS:
                            flush_chunk()
                            current_type = CHUNK_TYPE_RAW
                            current_blocks = 1
                            raw_chunks_data.append(blk)
                            raw_bytes_count += BLK_SIZE
                        else:
                            current_blocks += 1
                            if blk_type == CHUNK_TYPE_RAW:
                                raw_chunks_data.append(blk)
                                raw_bytes_count += BLK_SIZE
                    else:
                        flush_chunk()
                        current_type = blk_type
                        current_blocks = 1
                        if blk_type == CHUNK_TYPE_RAW:
                            raw_chunks_data.append(blk)
                            raw_bytes_count += BLK_SIZE
            
            processed_bytes += len(buf)
            now = time.time()
            if now - last_report >= 2.0:
                pct = (processed_bytes / file_size) * 100
                speed = (processed_bytes / (now - t0)) / (1024**2)
                print(f"Progress: {pct:.1f}% ({processed_bytes/(1024**3):.2f}/{file_size/(1024**3):.2f} GiB) - {speed:.1f} MB/s")
                last_report = now
        
        flush_chunk()
        
        # Write sparse header
        header = struct.pack('<I4H4I',
            SPARSE_HEADER_MAGIC,
            1, # major
            0, # minor
            28, # file_hdr_sz
            12, # chunk_hdr_sz
            BLK_SIZE,
            total_blocks,
            chunk_count,
            0 # checksum
        )
        fout.seek(0)
        fout.write(header)
    
    elapsed = time.time() - t0
    out_size = os.path.getsize(dst_path)
    print(f"Complete in {elapsed:.2f}s! Total blocks: {total_blocks}, Chunks: {chunk_count}, Sparse size: {out_size/(1024**3):.2f} GiB")

if __name__ == '__main__':
    if len(sys.argv) != 3:
        print("Usage: python fast_img2simg.py <raw_img> <sparse_img>")
        sys.exit(1)
    img2simg(sys.argv[1], sys.argv[2])
