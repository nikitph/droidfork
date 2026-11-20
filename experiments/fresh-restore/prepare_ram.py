"""Export main RAM from a sealed, uncompressed Android Emulator v2 checkpoint."""

import argparse
import hashlib
import os
import struct
import tempfile
from pathlib import Path

HEADER = 16384


def export(directory):
    with (directory / "ram.bin").open("rb") as source:
        (index_offset,) = struct.unpack(">Q", source.read(8))
        source.seek(index_offset)
        index = source.read()
        version, flags, _ = struct.unpack_from(">III", index)
        if (version, flags) != (2, 0):
            raise ValueError("requires an uncompressed version-2 checkpoint")
        cursor, source_offset = 12, 8

        def varint():
            nonlocal cursor
            value = 0
            for shift in range(0, 70, 7):
                b = index[cursor]
                cursor += 1
                value |= (b & 127) << shift
                if not b & 128:
                    return value
            raise ValueError("invalid page index")

        while cursor < len(index):
            size = index[cursor]
            cursor += 1
            name = index[cursor : cursor + size]
            cursor += size
            count, page_size, block_flags, path_size = struct.unpack_from(
                ">4I", index, cursor
            )
            cursor += 16 + path_size
            if not count:
                continue
            if name != b"mach-virt.ram" or block_flags or page_size != HEADER:
                raise ValueError(f"unsupported RAM block layout: {name!r}")
            destination = directory / "droidfork-main.raw"
            with tempfile.NamedTemporaryFile(dir=directory, delete=False) as out:
                temp = Path(out.name)
                try:
                    out.write(b"DFRAM001" + hashlib.sha256(index).digest())
                    out.truncate(HEADER + count * page_size)
                    run_start, run_out, run_size = 0, 0, 0

                    def flush(run_start, run_out, run_size):
                        if run_size:
                            source.seek(run_start)
                            data = source.read(run_size)
                            if len(data) != run_size:
                                raise EOFError("short page run")
                            out.seek(run_out)
                            out.write(data)

                    for i in range(count):
                        units = varint()
                        if not units:
                            continue
                        if units != 1:
                            raise ValueError("unexpected page size")
                        delta = varint()
                        source_offset += (
                            (-1 if delta & 1 else 1) * (delta >> 1) * page_size
                        )
                        cursor += 16  # v2 per-page hash
                        if not (8 <= source_offset <= index_offset - page_size):
                            raise ValueError("page falls outside snapshot data")
                        target = HEADER + i * page_size
                        if (
                            source_offset != run_start + run_size
                            or target != run_out + run_size
                            or run_size >= 4 << 20
                        ):
                            flush(run_start, run_out, run_size)
                            run_start, run_out, run_size = source_offset, target, 0
                        run_size += page_size
                    flush(run_start, run_out, run_size)
                    out.flush()
                    os.fsync(out.fileno())
                    temp.chmod(0o400)
                    os.replace(temp, destination)
                finally:
                    temp.unlink(missing_ok=True)
            return destination
        raise ValueError("no main RAM block")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot_directory", type=Path)
    print(export(parser.parse_args().snapshot_directory))
