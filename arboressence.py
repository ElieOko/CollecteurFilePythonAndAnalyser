from pathlib import Path


def afficher_arborescence_complete(chemin, prefix="", afficher_fichiers=True):
    """
    Arborescence complète avec fichiers (sans limite)

    Args:
        chemin: Chemin du dossier
        prefix: Préfixe d'indentation
        afficher_fichiers: Afficher ou non les fichiers
    """

    dossier = Path(chemin)

    if not dossier.exists():
        print(f"{prefix}❌ Dossier inexistant")
        return

    try:
        elements = list(dossier.iterdir())
        dossiers = sorted([e for e in elements if e.is_dir()])
        fichiers = sorted([e for e in elements if e.is_file()])

        # Afficher les dossiers
        for i, d in enumerate(dossiers):
            est_dernier = (i == len(dossiers) - 1 and (not afficher_fichiers or len(fichiers) == 0))

            if est_dernier:
                print(f"{prefix}└── 📁 {d.name}")
                nouveau_prefix = prefix + "    "
            else:
                print(f"{prefix}├── 📁 {d.name}")
                nouveau_prefix = prefix + "│   "

            # Parcourir les sous-dossiers
            afficher_arborescence_complete(d, nouveau_prefix, afficher_fichiers)

        # Afficher les fichiers (si demandé)
        if afficher_fichiers and fichiers:
            for i, f in enumerate(fichiers):
                est_dernier = (i == len(fichiers) - 1)

                if est_dernier:
                    print(f"{prefix}└── 📄 {f.name}")
                else:
                    print(f"{prefix}├── 📄 {f.name}")

    except PermissionError:
        print(f"{prefix}    [Accès refusé]")
    except Exception as e:
        print(f"{prefix}    [Erreur: {e}]")


# Utilisation
print("\n📂 ARBORESCENCE COMPLÈTE (sans limite):")
print("=" * 60)
afficher_arborescence_complete("/mnt/nvme-SAMSUNG_MZALQ256HBJD-00BL2_S65FNX0T804700-part4", afficher_fichiers=False)