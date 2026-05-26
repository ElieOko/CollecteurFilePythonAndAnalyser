import psutil
import sys
import pip._internal.utils.misc

print("\nHard Disk:\n")

for part in psutil.disk_partitions():
    if sys.platform == "win32":
        # Windows: afficher les lettres de lecteur
        if 'cdrom' not in part.opts.lower():
            try:
                usage = psutil.disk_usage(part.mountpoint)
                print(f"{part.mountpoint} - {usage.percent:.0f}% utilisé - {pip._internal.utils.misc.format_size(usage.free)} libre")
            except:
                print(f"{part.mountpoint} - [Accès refusé]")
    else:
        # Linux/Mac
        if 'loop' not in part.device and 'cdrom' not in part.opts:
            try:
                usage = psutil.disk_usage(part.mountpoint)
                print(f"{part.mountpoint} | {usage.percent:.0f}% utilisé - {pip._internal.utils.misc.format_size(usage.free)} libre")
            except:
                pass