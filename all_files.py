from pathlib import Path
from collections import Counter
from pypdf import PdfReader
from transformers import pipeline

def afficher_avec_statistiques(chemin, recursif=True):
    """
    Affiche les fichiers avec statistiques par extension
    """
    dossier = Path(chemin)

    if not dossier.exists():
        print(f"❌ Dossier inexistant")
        return None

    extensions = {'.pdf', '.xls', '.csv', '.docx', '.doc', '.png', '.jpeg'}

    # Recherche
    if recursif:
        fichiers = [f for f in dossier.rglob("*") if f.is_file() and f.suffix.lower() in extensions]
    else:
        fichiers = [f for f in dossier.iterdir() if f.is_file() and f.suffix.lower() in extensions]

    # Statistiques
    compteur = Counter()
    taille_totale = 0
    fichiers_par_ext = {}

    for ext in extensions:
        fichiers_par_ext[ext] = []

    for fichier in fichiers:
        ext = fichier.suffix.lower()
        compteur[ext] += 1
        taille_totale += fichier.stat().st_size
        fichiers_par_ext[ext].append(fichier)

    # Affichage
    print(f"\n📁 {dossier.absolute()}")
    print("=" * 60)
    print(f"Mode: {'Récursif' if recursif else 'Dossier uniquement'}")
    print("=" * 60)

    # Résumé par extension
    print("\n📊 RÉSUMÉ PAR EXTENSION:\n")
    for ext in sorted(extensions):
        count = compteur[ext]
        if count > 0:
            # Taille totale pour cette extension
            taille_ext = sum(f.stat().st_size for f in fichiers_par_ext[ext])
            taille_str = f"{taille_ext / (1024 * 1024):.1f} MB" if taille_ext > 1024 * 1024 else f"{taille_ext / 1024:.1f} KB"
            print(f"  {ext.upper()[1:]:4} : {count:4} fichier(s) - Total: {taille_str:>10}")

    print(f"\n  TOTAL : {len(fichiers)} fichier(s) - {taille_totale / (1024 * 1024):.1f} MB")

    # Liste détaillée (optionnelle)
    if fichiers and len(fichiers) <= 50:
        print("\n📄 LISTE DÉTAILLÉE:\n")
        for fichier in sorted(fichiers):
            taille = fichier.stat().st_size
            taille_str = f"{taille / 1024:.1f} KB" if taille < 1024 * 1024 else f"{taille / (1024 * 1024):.1f} MB"

            # Chemin relatif
            try:
                rel_path = fichier.relative_to(dossier)
            except:
                rel_path = fichier.name

            print(f"📄 {rel_path} ({taille_str})")

    return fichiers


# Utilisation
fichiers = afficher_avec_statistiques("/mnt/nvme-SAMSUNG_MZALQ256HBJD-00BL2_S65FNX0T804700-part4/Book", recursif=True)
print()
reader = PdfReader(fichiers[0])
