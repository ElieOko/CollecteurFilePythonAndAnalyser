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


def preparer_variantes_ocr(image):
    """Crée plusieurs versions optimisées d'une image pour améliorer l'OCR."""
    try:
        from PIL import ImageEnhance, ImageFilter, ImageOps
    except ImportError:
        return [image.convert("RGB")]

    image = ImageOps.exif_transpose(image).convert("RGB")
    largeur, hauteur = image.size
    facteur = max(1, int(1400 / max(largeur, 1))) if largeur < 1400 else 1
    if facteur > 1:
        image = image.resize((largeur * facteur, hauteur * facteur))

    gris = ImageOps.grayscale(image)
    contraste = ImageEnhance.Contrast(gris).enhance(2.0)
    nettete = ImageEnhance.Sharpness(contraste).enhance(1.8)
    seuil = nettete.point(lambda pixel: 255 if pixel > 170 else 0)
    inverse = ImageOps.invert(seuil)
    adoucie = nettete.filter(ImageFilter.MedianFilter(size=3))

    return [image, gris, contraste, nettete, seuil, inverse, adoucie]


def executer_ocr_image(image, langues=OCR_LANGUES):
    """Lance Tesseract sur plusieurs variantes et conserve le meilleur texte."""
    try:
        import pytesseract
    except ImportError:
        return ""

    langues_a_tester = [langues]
    if langues != "eng":
        langues_a_tester.append("eng")
    langues_a_tester.append(None)

    configurations = [
        "--oem 3 --psm 6",
        "--oem 3 --psm 4",
        "--oem 3 --psm 11",
        "--oem 3 --psm 12",
        "",
    ]

    meilleur_texte = ""
    for variante in preparer_variantes_ocr(image):
        for langue in langues_a_tester:
            for configuration in configurations:
                try:
                    options = {"config": configuration} if configuration else {}
                    if langue:
                        texte = pytesseract.image_to_string(variante, lang=langue, **options)
                    else:
                        texte = pytesseract.image_to_string(variante, **options)
                except Exception:
                    continue
                texte = nettoyer_texte(texte)
                if len(texte) > len(meilleur_texte):
                    meilleur_texte = texte
    return meilleur_texte


def extraire_texte_image_ocr(fichier, langues=OCR_LANGUES):
    """Extrait le texte d'une image avec OCR si Pillow, pytesseract et Tesseract sont disponibles."""
    try:
        from PIL import Image
    except ImportError:
        return ""

    try:
        with Image.open(fichier) as image:
            return executer_ocr_image(image, langues=langues)
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


def lignes_significatives(texte, limite=120):
    """Retourne les lignes utiles pour repérer titres, en-têtes et pieds de page."""
    lignes = []
    for ligne in texte.splitlines():
        ligne = nettoyer_texte(ligne)
        if not ligne or len(ligne) < 2:
            continue
        if re.fullmatch(r"[-_=|\s]+", ligne):
            continue
        lignes.append(ligne)
        if len(lignes) >= limite:
            break
    if lignes:
        return lignes
    return decouper_en_phrases(texte)[:limite]


def raccourcir(element, limite=140):
    """Raccourcit une ligne de contexte sans la dénaturer."""
    element = nettoyer_texte(element)
    if len(element) <= limite:
        return element
    return element[:limite].rsplit(" ", 1)[0].rstrip(" .,;:") + "..."


def liste_unique(elements, limite=6):
    """Conserve l'ordre en supprimant les doublons."""
    resultat = []
    vus = set()
    for element in elements:
        element = raccourcir(element)
        cle = element.lower()
        if element and cle not in vus:
            resultat.append(element)
            vus.add(cle)
        if len(resultat) >= limite:
            break
    return resultat


def detecter_titre(lignes):
    """Cherche un titre explicite dans les premières lignes du document."""
    mots_titre = ("facture", "invoice", "contrat", "devis", "reçu", "receipt", "convention", "attestation")
    candidates = lignes[:15]
    for ligne in candidates:
        if any(mot in ligne.lower() for mot in mots_titre):
            return raccourcir(ligne, limite=100)
    for ligne in candidates:
        if 4 <= len(ligne) <= 100 and not re.search(r"\d{1,2}/\d{1,2}/\d{2,4}", ligne):
            return raccourcir(ligne, limite=100)
    return ""


def extraire_montants_contextualises(texte, limite=8):
    """Extrait les montants et leur ligne de contexte pour les factures/contrats."""
    motif_montant = re.compile(
        r"(?i)(?:€|\$|usd|eur|euros?|fcfa|xaf|xof)?\s*"
        r"(?:\d{1,3}(?:[ . ,]\d{3})+|\d+)"
        r"(?:[,.]\d{2})?\s*"
        r"(?:€|\$|usd|eur|euros?|fcfa|xaf|xof)?"
    )
    lignes = lignes_significatives(texte, limite=250)
    montants = []
    mots_financiers = (
        "total", "ttc", "ht", "tva", "montant", "prix", "payer", "solde",
        "acompte", "remise", "facture", "devis", "honoraires", "loyer", "dépôt",
    )
    for ligne in lignes:
        if not motif_montant.search(ligne):
            continue
        contexte_financier = any(mot in ligne.lower() for mot in mots_financiers)
        contient_devise = re.search(r"(?i)(€|\$|usd|eur|euro|fcfa|xaf|xof)", ligne)
        if contexte_financier or contient_devise:
            montants.append(ligne)
    if not montants:
        montants = [match.group(0) for match in motif_montant.finditer(texte) if match.group(0).strip()]
    return liste_unique(montants, limite=limite)


def extraire_dates(texte, limite=5):
    """Extrait les dates courantes du document."""
    mois = "janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|septembre|octobre|novembre|décembre|decembre"
    motifs = [
        rf"\d{{1,2}}\s+(?:{mois})\s+\d{{4}}",
        r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}",
        r"\d{4}-\d{2}-\d{2}",
    ]
    dates = []
    for motif in motifs:
        dates.extend(re.findall(motif, texte, flags=re.IGNORECASE))
    return liste_unique(dates, limite=limite)


def extraire_references(texte, limite=6):
    """Extrait les lignes qui ressemblent à des références documentaires."""
    mots_reference = (
        "facture", "invoice", "devis", "contrat", "référence", "reference", "ref",
        "n°", "no", "numéro", "numero", "siret", "siren", "commande", "client",
    )
    lignes = lignes_significatives(texte, limite=220)
    refs = [ligne for ligne in lignes if any(mot in ligne.lower() for mot in mots_reference)]
    return liste_unique(refs, limite=limite)


def extraire_parties(texte, limite=5):
    """Repère des lignes décrivant client, fournisseur ou parties contractantes."""
    mots_parties = (
        "client", "fournisseur", "vendeur", "acheteur", "société", "societe",
        "entre ", "représenté", "represente", "prestataire", "bénéficiaire", "beneficiaire",
    )
    lignes = lignes_significatives(texte, limite=180)
    parties = [ligne for ligne in lignes if any(mot in ligne.lower() for mot in mots_parties)]
    return liste_unique(parties, limite=limite)


def detecter_profil_document(texte, fichier=None, montants=None):
    """Déduit le type probable du document à partir du vocabulaire et des montants."""
    extension = fichier.suffix.lower() if fichier else ""
    texte_min = texte.lower()
    montants = montants or []

    score_facture = sum(
        1
        for mot in ("facture", "invoice", "devis", "reçu", "receipt", "tva", "ttc", "ht", "net à payer", "total")
        if mot in texte_min
    )
    score_contrat = sum(
        1
        for mot in ("contrat", "convention", "accord", "clause", "signature", "article", "parties", "soussigné", "durée")
        if mot in texte_min
    )

    if extension in {".csv", ".xlsx", ".xls"} or " | " in texte:
        return (
            "un jeu de données ou tableau",
            "organiser des informations sous forme de lignes, colonnes ou valeurs comparables",
        )
    if score_facture >= 2 or (score_facture >= 1 and montants):
        return (
            "une facture, un devis ou un document de paiement",
            "identifier une transaction, les sommes à payer et les références de facturation",
        )
    if score_contrat >= 2:
        return (
            "un contrat ou document d'engagement",
            "formaliser les parties, les obligations, les dates et les éventuelles conditions financières",
        )
    if montants:
        return (
            "un document contenant des informations financières",
            "mettre en évidence des montants et leur contexte",
        )

    profils = [
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

    meilleur_profil = ("un document général", "présenter les informations principales du contenu")
    meilleur_score = 0
    for libelle, objectif, mots_cles in profils:
        score = sum(1 for mot in mots_cles if mot in texte_min)
        if score > meilleur_score:
            meilleur_score = score
            meilleur_profil = (libelle, objectif)
    return meilleur_profil


def analyser_document(texte, fichier=None):
    """Analyse le contenu pour produire une synthèse ancrée dans le document."""
    lignes = lignes_significatives(texte)
    montants = extraire_montants_contextualises(texte)
    profil, objectif = detecter_profil_document(texte, fichier=fichier, montants=montants)
    return {
        "profil": profil,
        "objectif": objectif,
        "titre": detecter_titre(lignes),
        "entete": liste_unique(lignes[:5], limite=3),
        "pied": liste_unique(lignes[-5:], limite=3),
        "montants": montants,
        "dates": extraire_dates(texte),
        "references": extraire_references(texte),
        "parties": extraire_parties(texte),
        "mots_cles": extraire_mots_significatifs(texte),
        "nombre_lignes": len(lignes),
    }


def limiter_texte(texte, longueur_max):
    """Coupe proprement une synthèse trop longue."""
    texte = nettoyer_texte(texte)
    if len(texte) <= longueur_max:
        return texte
    coupe = texte[:longueur_max].rsplit(" ", 1)[0]
    return coupe.rstrip(" .,;") + "..."


def generer_resume_ia(texte, longueur_max=1000):
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
            max_length=min(220, max(80, longueur_max // 4)),
            min_length=35,
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


def phrase_liste(prefixe, elements):
    """Construit une phrase uniquement si des éléments fiables sont disponibles."""
    if not elements:
        return ""
    return f"{prefixe} {joindre_liste(elements)}."


def generer_synthese_locale(texte, fichier=None, longueur_max=1000):
    """Produit une synthèse structurée, précise et ancrée dans le contenu du fichier."""
    texte = nettoyer_texte(texte)
    analyse = analyser_document(texte, fichier=fichier)
    mots_cles = analyse["mots_cles"]
    if not mots_cles and not analyse["montants"]:
        return "Le contenu contient trop peu de texte exploitable pour produire une synthèse fiable."

    profil = analyse["profil"]
    objectif = analyse["objectif"]
    titre = analyse["titre"]
    themes_principaux = joindre_liste(mots_cles[:4])

    morceaux = []
    if titre:
        morceaux.append(f"Document identifié comme {profil}, avec comme titre ou en-tête principal : « {titre} ».")
    else:
        morceaux.append(f"Document identifié comme {profil}.")

    if analyse["references"]:
        morceaux.append(phrase_liste("Références ou lignes d'identification repérées :", analyse["references"][:3]))
    elif analyse["entete"]:
        morceaux.append(phrase_liste("Indices d'en-tête repérés :", analyse["entete"][:3]))

    if analyse["parties"]:
        morceaux.append(phrase_liste("Parties ou acteurs mentionnés :", analyse["parties"][:3]))

    if analyse["montants"]:
        morceaux.append(phrase_liste("Sommes d'argent détectées avec leur contexte :", analyse["montants"][:5]))

    if analyse["dates"]:
        morceaux.append(phrase_liste("Dates importantes visibles :", analyse["dates"][:4]))

    if "facture" in profil or "paiement" in profil:
        morceaux.append(
            f"L'objectif du document est {formater_objectif(objectif)} ; les montants ci-dessus doivent donc être considérés comme des éléments centraux du résumé."
        )
    elif "contrat" in profil or "engagement" in profil:
        if analyse["montants"]:
            morceaux.append(
                "Comme il s'agit d'un engagement, les montants indiquent probablement des conditions financières, frais, paiements ou pénalités à vérifier."
            )
        morceaux.append(f"L'objectif du document est {formater_objectif(objectif)}.")
    else:
        morceaux.append(
            f"Le contenu porte principalement sur {themes_principaux} et sert à {objectif}."
        )

    if analyse["pied"] and analyse["pied"] != analyse["entete"]:
        morceaux.append(phrase_liste("Éléments de pied de page ou de fin du document :", analyse["pied"][:2]))

    return limiter_texte(" ".join(morceaux), longueur_max)


def generer_resume_texte(
    texte,
    nombre_phrases=3,
    longueur_max=1000,
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
