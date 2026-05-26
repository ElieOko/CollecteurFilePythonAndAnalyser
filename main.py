from pathlib import Path

from WriterFolder import write_folder
from navigate import navigate

drive = "/mnt/nvme-SAMSUNG_MZALQ256HBJD-00BL2_S65FNX0T804700-part4"
#print(f"Fichiers dans Downloads: {len([f for f in (Path.home() / 'Downloads').iterdir() if f.is_file()])}")
print(f"Fichiers dans files: {len([f for f in (Path(drive) / 'Book').iterdir() if f.is_file()])}")
collector = navigate("/mnt/nvme-SAMSUNG_MZALQ256HBJD-00BL2_S65FNX0T804700-part4/Book")
folders = collector[0]
files = collector[1]

write_folder(folders)
