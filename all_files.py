from collections import Counter
import csv
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree


EXTENSIONS_SUPPORTEES = {
    ".pdf",
    ".xls",
    ".xlsx",
    ".csv",
    ".docx",
    ".doc",
    ".png",
    ".jpg",
    ".jpeg",
    ".txt",
    ".md",
}

MOTS_VIDES = {
    "alors",
    "avec",
    "avoir",
    "cela",
    "dans",
    "des",
    "elle",
    "elles",
    "entre",
    "est",
    "etre",
    "être",
    "fait",
    "font",
    "ils",
    "les",
    "leur",
    "leurs",
    "mais",
    "nous",
    "par",
    "pas",
    "plus",
    "pour",
    "que",
    "qui",
    "sont",
    "sur",
    "une",
    "vous",
    "the",
    "and",
    "for",
    "from",
    "that",
    "this",
    "with",
}


def formater_taille(taille):
    """Formate une taille en octets vers une valeur lisible."""
    if taille < 1024:
        return f"{taille} B"
    if taille < 1024 * 1024:
        return f"{taille / 1024:.1f} KB"
    return f"{taille / (1024 * 1024):.1f} MB"


def collecter_fichiers(dossier, recursif=True, extensions=None):
    """Retourne les fichiers collectés dans un dossier."""
    extensions = extensions or EXTENSIONS_SUPPORTEES
    if recursif:
        return [
            f
            for f in dossier.rglob("*")
            if f.is_file() and f.suffix.lower() in extensions
        ]
    return [
        f
        for f in dossier.iterdir()
        if f.is_file() and f.suffix.lower() in extensions
    ]


def extraire_texte_pdf(fichier, max_pages=20):
    """Extrait le texte d'un PDF avec pypdf si la librairie est disponible."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""

    try:
        reader = PdfReader(fichier)
        textes = []
        for page in reader.pages[:max_pages]:
            texte_page = page.extract_text() or ""
            if texte_page.strip():
                textes.append(texte_page)
        return "\n".join(textes)
    except Exception:
        return ""


def extraire_texte_csv(fichier, max_lignes=80):
    """Extrait les premières lignes d'un CSV."""
    lignes = []
    try:
        with fichier.open("r", encoding="utf-8", errors="ignore", newline="") as flux:
            lecteur = csv.reader(flux)
            for index, ligne in enumerate(lecteur):
                if index >= max_lignes:
                    break
                lignes.append(" | ".join(cellule.strip() for cellule in ligne if cellule.strip()))
    except Exception:
        return ""
    return "\n".join(ligne for ligne in lignes if ligne)


def extraire_texte_docx(fichier):
    """Extrait les paragraphes principaux d'un fichier DOCX."""
    try:
        with zipfile.ZipFile(fichier) as archive:
            contenu = archive.read("word/document.xml")
    except Exception:
        return ""

    try:
        racine = ElementTree.fromstring(contenu)
    except ElementTree.ParseError:
        return ""

    espaces_noms = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphes = []
    for paragraphe in racine.findall(".//w:p", espaces_noms):
        morceaux = [
            noeud.text
            for noeud in paragraphe.findall(".//w:t", espaces_noms)
            if noeud.text
        ]
        texte = "".join(morceaux).strip()
        if texte:
            paragraphes.append(texte)
    return "\n".join(paragraphes)


def extraire_texte_xlsx(fichier, max_cellules=300):
    """Extrait des valeurs texte d'un fichier XLSX sans dépendance externe."""
    try:
        with zipfile.ZipFile(fichier) as archive:
            chaines_partagees = extraire_chaines_partagees_xlsx(archive)
            valeurs = []
            feuilles = sorted(
                nom
                for nom in archive.namelist()
                if nom.startswith("xl/worksheets/sheet") and nom.endswith(".xml")
            )
            for feuille in feuilles:
                valeurs.extend(extraire_valeurs_feuille_xlsx(archive, feuille, chaines_partagees))
                if len(valeurs) >= max_cellules:
                    break
    except Exception:
        return ""

    return "\n".join(valeur for valeur in valeurs[:max_cellules] if valeur)


def extraire_chaines_partagees_xlsx(archive):
    """Lit la table des chaînes partagées d'un fichier XLSX."""
    try:
        contenu = archive.read("xl/sharedStrings.xml")
    except KeyError:
        return []

    espaces_noms = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    try:
        racine = ElementTree.fromstring(contenu)
    except ElementTree.ParseError:
        return []

    chaines = []
    for item in racine.findall(".//x:si", espaces_noms):
        textes = [noeud.text for noeud in item.findall(".//x:t", espaces_noms) if noeud.text]
        chaines.append("".join(textes))
    return chaines


def extraire_valeurs_feuille_xlsx(archive, feuille, chaines_partagees):
    """Extrait les cellules texte d'une feuille XLSX."""
    espaces_noms = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    try:
        racine = ElementTree.fromstring(archive.read(feuille))
    except Exception:
        return []

    valeurs = []
    for cellule in racine.findall(".//x:c", espaces_noms):
        type_cellule = cellule.attrib.get("t")
        valeur = cellule.find("x:v", espaces_noms)
        texte_inline = cellule.find(".//x:t", espaces_noms)

        if type_cellule == "s" and valeur is not None and valeur.text:
            try:
                valeurs.append(chaines_partagees[int(valeur.text)])
            except (IndexError, ValueError):
                continue
        elif texte_inline is not None and texte_inline.text:
            valeurs.append(texte_inline.text)
        elif valeur is not None and valeur.text:
            valeurs.append(valeur.text)
    return valeurs


def extraire_texte_simple(fichier, taille_max=200_000):
    """Extrait le texte de fichiers simples comme TXT ou Markdown."""
    try:
        with fichier.open("r", encoding="utf-8", errors="ignore") as flux:
            return flux.read(taille_max)
    except Exception:
        return ""


def extraire_texte_fichier(fichier):
    """Route l'extraction selon l'extension du fichier."""
    extension = fichier.suffix.lower()
    if extension == ".pdf":
        return extraire_texte_pdf(fichier)
    if extension == ".csv":
        return extraire_texte_csv(fichier)
    if extension == ".docx":
        return extraire_texte_docx(fichier)
    if extension == ".xlsx":
        return extraire_texte_xlsx(fichier)
    if extension in {".txt", ".md"}:
        return extraire_texte_simple(fichier)
    return ""


def nettoyer_texte(texte):
    """Normalise les espaces pour faciliter le résumé."""
    return re.sub(r"\s+", " ", texte).strip()


def decouper_en_phrases(texte):
    """Découpe un texte en phrases ou segments lisibles."""
    phrases = re.split(r"(?<=[.!?])\s+", texte)
    if len(phrases) <= 1:
        phrases = re.split(r"[\n;]+", texte)
    return [phrase.strip() for phrase in phrases if phrase.strip()]


def generer_resume_texte(texte, nombre_phrases=3, longueur_max=700):
    """
    Génère un résumé extractif simple à partir du contenu.

    Les phrases les plus représentatives sont choisies avec un score basé sur la
    fréquence des mots importants, puis replacées dans l'ordre du document.
    """
    texte = nettoyer_texte(texte)
    if not texte:
        return ""
    if len(texte) <= longueur_max:
        return texte

    phrases = decouper_en_phrases(texte)
    if not phrases:
        return texte[:longueur_max].rstrip() + "..."

    mots = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9']+", texte.lower())
    frequences = Counter(mot for mot in mots if len(mot) > 3 and mot not in MOTS_VIDES)
    if not frequences:
        return texte[:longueur_max].rstrip() + "..."

    scores = []
    for index, phrase in enumerate(phrases):
        mots_phrase = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9']+", phrase.lower())
        score = sum(frequences[mot] for mot in mots_phrase if mot in frequences)
        scores.append((score, index, phrase))

    meilleures_phrases = sorted(scores, reverse=True)[:nombre_phrases]
    phrases_ordonnees = [phrase for _, _, phrase in sorted(meilleures_phrases, key=lambda item: item[1])]
    resume = nettoyer_texte(" ".join(phrases_ordonnees))

    if len(resume) > longueur_max:
        return resume[:longueur_max].rstrip() + "..."
    return resume


def generer_resume_fichier(fichier):
    """Génère le résumé d'un fichier collecté à partir de son contenu."""
    texte = extraire_texte_fichier(fichier)
    if texte:
        return generer_resume_texte(texte)

    extension = fichier.suffix.lower()
    if extension in {".png", ".jpg", ".jpeg"}:
        return "Résumé indisponible : l'extraction du texte des images nécessite un outil OCR."
    if extension in {".doc", ".xls"}:
        return "Résumé indisponible : l'ancien format binaire nécessite une librairie spécialisée."
    return "Résumé indisponible : aucun contenu texte exploitable n'a été trouvé."



def afficher_avec_statistiques(chemin, recursif=True, avec_resumes=True, max_resumes=None):
    """
    Affiche les fichiers avec statistiques par extension et résumés de contenu.
    """
    dossier = Path(chemin)

    if not dossier.exists():
        print(f"❌ Dossier inexistant")
        return []

    fichiers = collecter_fichiers(dossier, recursif=recursif)

    # Statistiques
    compteur = Counter()
    taille_totale = 0
    fichiers_par_ext = {}

    for ext in EXTENSIONS_SUPPORTEES:
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
    for ext in sorted(EXTENSIONS_SUPPORTEES):
        count = compteur[ext]
        if count > 0:
            # Taille totale pour cette extension
            taille_ext = sum(f.stat().st_size for f in fichiers_par_ext[ext])
            taille_str = formater_taille(taille_ext)
            print(f"  {ext.upper()[1:]:4} : {count:4} fichier(s) - Total: {taille_str:>10}")

    print(f"\n  TOTAL : {len(fichiers)} fichier(s) - {formater_taille(taille_totale)}")

    # Liste détaillée (optionnelle)
    if fichiers and len(fichiers) <= 50:
        print("\n📄 LISTE DÉTAILLÉE:\n")
        for fichier in sorted(fichiers):
            taille_str = formater_taille(fichier.stat().st_size)

            # Chemin relatif
            try:
                rel_path = fichier.relative_to(dossier)
            except ValueError:
                rel_path = fichier.name

            print(f"📄 {rel_path} ({taille_str})")

    if avec_resumes and fichiers:
        print("\n📝 RÉSUMÉS DES FICHIERS:\n")
        fichiers_a_resumer = sorted(fichiers)
        if max_resumes is not None:
            fichiers_a_resumer = fichiers_a_resumer[:max_resumes]

        for fichier in fichiers_a_resumer:
            try:
                rel_path = fichier.relative_to(dossier)
            except ValueError:
                rel_path = fichier.name

            print(f"📄 {rel_path}")
            print(f"   {generer_resume_fichier(fichier)}\n")

        if max_resumes is not None and len(fichiers) > max_resumes:
            print(f"... et {len(fichiers) - max_resumes} autre(s) fichier(s) non résumé(s)")

    return fichiers


if __name__ == "__main__":
    afficher_avec_statistiques(
        "/mnt/nvme-SAMSUNG_MZALQ256HBJD-00BL2_S65FNX0T804700-part4/Book",
        recursif=True,
        avec_resumes=True,
    )
