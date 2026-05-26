import os

def voir_contenu(repertoire):
    """Afficher le contenu d'un répertoire"""

    # Vérifier si le répertoire existe
    if not os.path.exists(repertoire):
        print(f"❌ Le répertoire {repertoire} n'existe pas")
        return

    print(f"\n📁 CONTENU DE : {repertoire}")
    print("=" * 60)

    try:
        # Lister tous les éléments
        elements = os.listdir(repertoire)

        # Séparer dossiers et fichiers
        dossiers = []
        fichiers = []

        for element in elements:
            chemin_complet = os.path.join(repertoire, element)
            if os.path.isdir(chemin_complet):
                dossiers.append(element)
            else:
                fichiers.append(element)

        # Afficher les dossiers
        if dossiers:
            print("\n📂 DOSSIERS :")
            for dossier in sorted(dossiers):
                print(f"  📁 {dossier}")

        # Afficher les fichiers
        if fichiers:
            print("\n📄 FICHIERS :")
            for fichier in sorted(fichiers)[:20]:  # Limite à 20 fichiers
                # Obtenir la taille
                chemin_fichier = os.path.join(repertoire, fichier)
                taille = os.path.getsize(chemin_fichier)
                print(f"  📄 {fichier:<40} ({taille:,} bytes)")

            if len(fichiers) > 20:
                print(f"  ... et {len(fichiers) - 20} autres fichiers")

        print(f"\n📊 Total: {len(dossiers)} dossier(s), {len(fichiers)} fichier(s)")
        return dossiers
    except PermissionError:
        print("❌ Permission refusée pour ce répertoire")
    except Exception as e:
        print(f"❌ Erreur: {e}")

# Utilisation
if __name__ == "__main__":
    #voir_contenu("C:\\")
    content = voir_contenu("/mnt/nvme-SAMSUNG_MZALQ256HBJD-00BL2_S65FNX0T804700-part4")
    