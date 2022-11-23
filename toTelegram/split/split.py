

import os
import math

class Split:
    def __init__(self, path):
        self.path = path
        self.size = os.path.getsize(self.path)

    def __call__(self, chunk_size: int, output: str):
        if self.size < chunk_size:
            raise ValueError

        folder = os.path.dirname(output)
        filename = os.path.basename(output)
        total_parts = math.ceil(self.size/chunk_size)
        file_number = 1

        if not os.path.exists(folder):
            os.makedirs(folder)

        paths = []
        with open(self.path, "rb") as f:
            chunk = f.read(chunk_size)
            while chunk:
                filename_chunk = f"{filename}_{file_number}-{total_parts}"
                print("\t", filename_chunk)
                path_chunk = os.path.join(folder, filename_chunk)
                with open(path_chunk, "wb") as chunk_file:
                    chunk_file.write(chunk)
                paths.append(path_chunk)
                # print(f"{file_number}/{total_parts}", humanize.naturalsize(len(chunk)))
                file_number += 1
                chunk = f.read(chunk_size)
        return paths
