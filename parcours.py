from pathlib import Path


def afficher_contenus(chemin):
    """Afficher tous les contenus d'un dossier"""

    dossier = Path(chemin)

    if not dossier.exists():
        print(f"❌ Le dossier n'existe pas: {chemin}")
        return

    print(f"\n📁 {chemin}")
    print("=" * 50)

    # Lister tous les éléments
    for item in sorted(dossier.iterdir()):
        if item.is_dir():
            print(f"📂 {item.name}")
        else:
            # Afficher la taille pour les fichiers
            taille = item.stat().st_size
            if taille < 1024:
                taille_str = f"{taille} B"
            elif taille < 1024 * 1024:
                taille_str = f"{taille / 1024:.1f} KB"
            else:
                taille_str = f"{taille / (1024 * 1024):.1f} MB"

            print(f"📄 {item.name} ({taille_str})")


# Utilisation
afficher_contenus("/mnt/nvme-SAMSUNG_MZALQ256HBJD-00BL2_S65FNX0T804700-part4")