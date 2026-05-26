import os


def navigate(path):
    items = os.listdir(path)
    # Séparer dossiers et fichiers
    dossiers = []
    fichiers = []

    for element in items:
        chemin_complet = os.path.join(path, element)
        if os.path.isdir(chemin_complet):
            dossiers.append(element)
        else:
            fichiers.append(element)
    return dossiers, fichiers