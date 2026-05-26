from collections import Counter
from contextlib import contextmanager
import csv
import logging
import os
import re
import warnings
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
    "rester",
    "comprendre",
    "peuvent",
    "automatiquement",
    "aide",
    "resume",
    "résumé",
    "document",
    "contenu",
    "rapidement",
    "permettre",
    "permet",
    "doit",
    "cette",
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


OCR_LANGUES = "fra+eng"
AVERTISSEMENT_PDF_DICTIONNAIRE = "Multiple definitions in dictionary"
RESUME_MODELE_DEFAUT = "csebuetnlp/mT5_multilingual_XLSum"
_PIPELINE_RESUME = None


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


@contextmanager
def ignorer_avertissements_pypdf():
    """Réduit le bruit des PDF mal formés que pypdf sait quand même lire."""
    logger = logging.getLogger("pypdf")
    ancien_niveau = logger.level
    logger.setLevel(logging.ERROR)
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=f".*{AVERTISSEMENT_PDF_DICTIONNAIRE}.*",
            )
            yield
    finally:
        logger.setLevel(ancien_niveau)


def extraire_texte_pdf(fichier, max_pages=20):
    """Extrait le texte d'un PDF, avec fallback OCR si le texte n'est pas lisible."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return extraire_texte_pdf_ocr(fichier, max_pages=max_pages)

    try:
        with ignorer_avertissements_pypdf():
            reader = PdfReader(str(fichier), strict=False)
            textes = []
            for page in list(reader.pages)[:max_pages]:
                texte_page = page.extract_text() or ""
                if texte_page.strip():
                    textes.append(texte_page)
            texte = "\n".join(textes)
    except Exception:
        return extraire_texte_pdf_ocr(fichier, max_pages=max_pages)

    if texte.strip():
        return texte
    return extraire_texte_pdf_ocr(fichier, max_pages=max_pages)


def executer_ocr_image(image, langues=OCR_LANGUES):
    """Lance Tesseract sur une image PIL et essaie un fallback si la langue manque."""
    try:
        import pytesseract
    except ImportError:
        return ""

    langues_a_tester = [langues]
    if langues != "eng":
        langues_a_tester.append("eng")
    langues_a_tester.append(None)

    for langue in langues_a_tester:
        try:
            if langue:
                texte = pytesseract.image_to_string(image, lang=langue)
            else:
                texte = pytesseract.image_to_string(image)
        except Exception:
            continue
        if texte.strip():
            return texte
    return ""


def extraire_texte_image_ocr(fichier, langues=OCR_LANGUES):
    """Extrait le texte d'une image avec OCR si Pillow, pytesseract et Tesseract sont disponibles."""
    try:
        from PIL import Image
    except ImportError:
        return ""

    try:
        with Image.open(fichier) as image:
            return executer_ocr_image(image.convert("RGB"), langues=langues)
    except Exception:
        return ""


def extraire_texte_pdf_ocr(fichier, max_pages=20, langues=OCR_LANGUES):
    """Convertit les pages PDF en images et applique l'OCR quand les outils sont présents."""
    try:
        from pdf2image import convert_from_path
    except ImportError:
        return ""

    try:
        images = convert_from_path(
            str(fichier),
            dpi=200,
            first_page=1,
            last_page=max_pages,
        )
    except Exception:
        return ""

    textes = []
    for image in images:
        texte = executer_ocr_image(image, langues=langues)
        if texte.strip():
            textes.append(texte)
        try:
            image.close()
        except Exception:
            pass
    return "\n".join(textes)


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
    if extension in {".png", ".jpg", ".jpeg"}:
        return extraire_texte_image_ocr(fichier)
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


def extraire_mots_significatifs(texte, limite=10):
    """Identifie les mots forts sans reprendre directement des phrases du document."""
    mots = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9']+", texte.lower())
    frequences = Counter(
        mot
        for mot in mots
        if len(mot) > 3 and mot not in MOTS_VIDES and not mot.isdigit()
    )
    return [mot for mot, _ in frequences.most_common(limite)]


def joindre_liste(elements):
    """Transforme une liste courte en expression française lisible."""
    elements = [element for element in elements if element]
    if not elements:
        return "des informations générales"
    if len(elements) == 1:
        return elements[0]
    return ", ".join(elements[:-1]) + " et " + elements[-1]


def detecter_profil_document(texte, fichier=None):
    """Déduit le type probable du document à partir de son vocabulaire."""
    extension = fichier.suffix.lower() if fichier else ""
    texte_min = texte.lower()

    profils = [
        (
            "un document administratif ou contractuel",
            "formaliser des informations, des engagements ou des éléments de suivi",
            {"contrat", "signature", "article", "clause", "client", "adresse", "document"},
        ),
        (
            "un document financier ou commercial",
            "présenter des montants, des transactions ou des informations de facturation",
            {"facture", "montant", "total", "tva", "prix", "paiement", "devis", "commande"},
        ),
        (
            "un contenu technique",
            "expliquer un fonctionnement, une procédure ou une logique de traitement",
            {"python", "code", "fonction", "fichier", "dossier", "erreur", "système", "traitement"},
        ),
        (
            "un support pédagogique ou documentaire",
            "transmettre des connaissances ou structurer une explication",
            {"chapitre", "cours", "exemple", "méthode", "apprendre", "question", "réponse", "notion"},
        ),
    ]

    if extension in {".csv", ".xlsx", ".xls"} or " | " in texte:
        return (
            "un jeu de données ou tableau",
            "organiser des informations sous forme de lignes, colonnes ou valeurs comparables",
        )

    meilleur_profil = ("un document général", "présenter les informations principales du contenu")
    meilleur_score = 0
    for libelle, objectif, mots_cles in profils:
        score = sum(1 for mot in mots_cles if mot in texte_min)
        if score > meilleur_score:
            meilleur_score = score
            meilleur_profil = (libelle, objectif)
    return meilleur_profil


def limiter_texte(texte, longueur_max):
    """Coupe proprement une synthèse trop longue."""
    texte = nettoyer_texte(texte)
    if len(texte) <= longueur_max:
        return texte
    coupe = texte[:longueur_max].rsplit(" ", 1)[0]
    return coupe.rstrip(" .,;") + "..."


def generer_resume_ia(texte, longueur_max=700):
    """Utilise un modèle de résumé si transformers est installé et configuré."""
    global _PIPELINE_RESUME

    try:
        from transformers import pipeline
    except ImportError:
        return ""

    modele = os.getenv("RESUME_MODELE", RESUME_MODELE_DEFAUT)
    try:
        if _PIPELINE_RESUME is None:
            _PIPELINE_RESUME = pipeline("summarization", model=modele)
        resultat = _PIPELINE_RESUME(
            texte[:4500],
            max_length=min(180, max(60, longueur_max // 4)),
            min_length=25,
            do_sample=False,
        )
    except Exception:
        return ""

    if not resultat:
        return ""
    resume = resultat[0].get("summary_text", "")
    return limiter_texte(resume, longueur_max)


def formater_objectif(objectif):
    """Ajoute correctement de/d' devant un verbe à l'infinitif."""
    if objectif[:1].lower() in {"a", "e", "i", "o", "u", "y", "h"}:
        return f"d'{objectif}"
    return f"de {objectif}"


def generer_synthese_locale(texte, fichier=None, longueur_max=700):
    """Produit une synthèse reformulée sans recopier les phrases du fichier."""
    texte = nettoyer_texte(texte)
    mots_cles = extraire_mots_significatifs(texte)
    if not mots_cles:
        return "Le contenu contient trop peu de texte exploitable pour produire une synthèse fiable."

    profil, objectif = detecter_profil_document(texte, fichier=fichier)
    themes_principaux = joindre_liste(mots_cles[:3])
    themes_secondaires = joindre_liste(mots_cles[3:7])
    nombre_phrases = len(decouper_en_phrases(texte))

    if len(texte) < 300 or nombre_phrases <= 2:
        synthese = (
            f"Ce contenu court semble être {profil} centré sur {themes_principaux}. "
            f"Il fournit surtout une information rapide autour de {themes_secondaires}, "
            f"avec pour objectif probable {formater_objectif(objectif)}."
        )
    else:
        synthese = (
            f"Ce document semble être {profil} centré sur {themes_principaux}. "
            f"Il met en relation plusieurs éléments autour de {themes_secondaires}, "
            f"ce qui indique que son objectif principal est {formater_objectif(objectif)}. "
            f"En résumé, il sert surtout à donner une vue structurée des thèmes suivants : {themes_principaux}, "
            "et à faciliter l'exploitation de ces informations."
        )

    return limiter_texte(synthese, longueur_max)


def generer_resume_texte(
    texte,
    nombre_phrases=3,
    longueur_max=700,
    fichier=None,
    utiliser_ia=False,
):
    """
    Génère un résumé réaliste en reformulant le contenu.

    Par défaut, la fonction produit une synthèse locale non extractive. Si
    utiliser_ia=True et qu'un modèle transformers est disponible, elle tente
    d'abord un résumé abstractive puis revient à la synthèse locale en fallback.
    """
    texte = nettoyer_texte(texte)
    if not texte:
        return ""

    if utiliser_ia:
        resume_ia = generer_resume_ia(texte, longueur_max=longueur_max)
        if resume_ia:
            return resume_ia

    return generer_synthese_locale(texte, fichier=fichier, longueur_max=longueur_max)


def generer_resume_fichier(fichier, utiliser_ia=False):
    """Génère une synthèse réaliste d'un fichier collecté à partir de son contenu."""
    texte = extraire_texte_fichier(fichier)
    if texte:
        return generer_resume_texte(texte, fichier=fichier, utiliser_ia=utiliser_ia)

    extension = fichier.suffix.lower()
    if extension in {".png", ".jpg", ".jpeg"}:
        return "Résumé indisponible : OCR impossible ou aucun texte détecté dans l'image."
    if extension == ".pdf":
        return "Résumé indisponible : aucun texte PDF exploitable et OCR PDF non disponible ou sans résultat."
    if extension in {".doc", ".xls"}:
        return "Résumé indisponible : l'ancien format binaire nécessite une librairie spécialisée."
    return "Résumé indisponible : aucun contenu texte exploitable n'a été trouvé."



def afficher_avec_statistiques(
    chemin,
    recursif=True,
    avec_resumes=True,
    max_resumes=None,
    utiliser_ia=False,
):
    """
    Affiche les fichiers avec statistiques par extension et synthèses de contenu.
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
        print("\n📝 SYNTHÈSES DES FICHIERS:\n")
        fichiers_a_resumer = sorted(fichiers)
        if max_resumes is not None:
            fichiers_a_resumer = fichiers_a_resumer[:max_resumes]

        for fichier in fichiers_a_resumer:
            try:
                rel_path = fichier.relative_to(dossier)
            except ValueError:
                rel_path = fichier.name

            print(f"📄 {rel_path}")
            print(f"   {generer_resume_fichier(fichier, utiliser_ia=utiliser_ia)}\n")

        if max_resumes is not None and len(fichiers) > max_resumes:
            print(f"... et {len(fichiers) - max_resumes} autre(s) fichier(s) non résumé(s)")

    return fichiers


if __name__ == "__main__":
    afficher_avec_statistiques(
        "/mnt/nvme-SAMSUNG_MZALQ256HBJD-00BL2_S65FNX0T804700-part4/Book",
        recursif=True,
        avec_resumes=True,
    )
