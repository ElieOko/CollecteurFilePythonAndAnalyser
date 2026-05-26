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
DETECTION_MODELE_DEFAUT = "MoritzLaurer/mDeBERTa-v3-base-mnli-xnli"
_PIPELINE_RESUME = None
_PIPELINE_DETECTION = None


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


def lignes_significatives_indexees(texte, limite=180):
    """Retourne les lignes utiles avec leur numéro pour citer les preuves."""
    lignes = []
    for numero, ligne in enumerate(texte.splitlines(), start=1):
        ligne = nettoyer_texte(ligne)
        if not ligne or len(ligne) < 2:
            continue
        if re.fullmatch(r"[-_=|\s]+", ligne):
            continue
        lignes.append({"numero": numero, "texte": ligne})
        if len(lignes) >= limite:
            break
    if lignes:
        return lignes
    return [
        {"numero": index, "texte": phrase}
        for index, phrase in enumerate(decouper_en_phrases(texte)[:limite], start=1)
    ]


def citation_ligne(numero):
    """Formate une citation de ligne à la manière des réponses sourcées."""
    return f"[L{numero}]" if numero else "[ligne non localisée]"


def citation_evidence(evidence):
    """Ajoute la citation de ligne à une preuve extraite."""
    return f"{evidence['texte']} {citation_ligne(evidence.get('ligne'))}"


def trouver_evidence_ligne(lignes_indexees, valeur):
    """Retrouve la première ligne qui contient une valeur extraite."""
    valeur_norm = nettoyer_texte(valeur).lower()
    for ligne in lignes_indexees:
        if valeur_norm and valeur_norm in ligne["texte"].lower():
            return {"texte": raccourcir(ligne["texte"]), "ligne": ligne["numero"]}
    return {"texte": raccourcir(valeur), "ligne": None}


def evidences_depuis_valeurs(lignes_indexees, valeurs, limite=5):
    """Associe des valeurs textuelles à leurs lignes d'origine."""
    evidences = []
    vus = set()
    for valeur in valeurs:
        evidence = trouver_evidence_ligne(lignes_indexees, valeur)
        cle = (evidence["texte"].lower(), evidence.get("ligne"))
        if evidence["texte"] and cle not in vus:
            evidences.append(evidence)
            vus.add(cle)
        if len(evidences) >= limite:
            break
    return evidences


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
    mots_financiers_forts = (
        "ttc", "ht", "tva", "montant", "prix", "payer", "solde", "acompte",
        "remise", "honoraires", "loyer", "dépôt", "somme", "frais", "net à payer", "net a payer",
    )
    mots_total_financier = ("total ttc", "total ht", "total à payer", "total a payer")
    for ligne in lignes:
        if not motif_montant.search(ligne):
            continue
        ligne_min = ligne.lower()
        contexte_financier = any(mot in ligne_min for mot in mots_financiers_forts)
        total_financier = any(mot in ligne_min for mot in mots_total_financier)
        contient_devise = re.search(r"(?i)(€|\$|usd|eur|euro|fcfa|xaf|xof)", ligne)
        if contexte_financier or total_financier or contient_devise:
            montants.append(ligne)
    return liste_unique(montants, limite=limite)


def extraire_dates(texte, limite=5):
    """Extrait les dates courantes du document."""
    mois = "janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|septembre|octobre|novembre|décembre|decembre"
    motifs = [
        rf"\b\d{{1,2}}\s+(?:{mois})\s+\d{{4}}\b",
        r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",
        r"\b\d{4}-\d{2}-\d{2}\b",
    ]
    dates = []
    for motif in motifs:
        dates.extend(re.findall(motif, texte, flags=re.IGNORECASE))
    return liste_unique(dates, limite=limite)


def extraire_references(texte, limite=6):
    """Extrait les lignes qui ressemblent à des références documentaires."""
    mots_reference = (
        "facture", "invoice", "devis", "contrat", "référence", "reference", "réf",
        "n°", "numéro", "numero", "siret", "siren", "commande", "client",
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


def extraire_extrait_detection(texte, limite=3500):
    """Prépare un extrait représentatif pour la classification du document."""
    lignes = lignes_significatives(texte, limite=160)
    debut = lignes[:35]
    fin = lignes[-15:] if len(lignes) > 35 else []
    lignes_argent = extraire_montants_contextualises(texte, limite=10)
    extrait = "\n".join(liste_unique(debut + lignes_argent + fin, limite=70))
    return extrait[:limite]


def indices_facture(texte, montants=None):
    """Vérifie que le contenu contient de vrais indices de facture/devis."""
    texte_min = texte.lower()
    montants = montants or []
    mots_forts = (
        "facture", "invoice", "devis", "avoir", "proforma", "bon de commande",
        "reçu", "receipt", "net à payer", "net a payer",
    )
    mots_financiers = ("tva", "ttc", "ht", "total", "montant", "paiement", "échéance", "echeance")
    return any(mot in texte_min for mot in mots_forts) and (
        bool(montants) or sum(1 for mot in mots_financiers if mot in texte_min) >= 2
    )


def indices_contrat(texte):
    """Vérifie que le contenu contient de vrais indices de contrat/engagement."""
    texte_min = texte.lower()
    mots_contrat = (
        "contrat", "convention", "accord", "clause", "article", "signature",
        "parties", "soussigné", "soussignes", "durée", "obligations", "résiliation",
    )
    return sum(1 for mot in mots_contrat if mot in texte_min) >= 2


def indices_tableau(texte, fichier=None):
    """Détecte un document tabulaire sans le confondre avec une facture."""
    extension = fichier.suffix.lower() if fichier else ""
    return extension in {".csv", ".xlsx", ".xls"} or " | " in texte


def profil_depuis_categorie(categorie):
    """Convertit une catégorie de classification en profil et objectif métier."""
    mapping = {
        "facture_devis": (
            "une facture, un devis ou un document de paiement",
            "identifier une transaction, les sommes à payer et les références de facturation",
        ),
        "contrat": (
            "un contrat ou document d'engagement",
            "formaliser les parties, les obligations, les dates et les éventuelles conditions financières",
        ),
        "document_financier": (
            "un document contenant des informations financières",
            "mettre en évidence les montants, leur contexte et les éléments de suivi financier",
        ),
        "tableau": (
            "un jeu de données ou tableau",
            "organiser des informations sous forme de lignes, colonnes ou valeurs comparables",
        ),
        "technique": (
            "un contenu technique",
            "expliquer un fonctionnement, une procédure ou une logique de traitement",
        ),
        "pedagogique": (
            "un support pédagogique ou documentaire",
            "transmettre des connaissances ou structurer une explication",
        ),
        "administratif": (
            "un document administratif ou opérationnel",
            "présenter des informations de gestion, de suivi ou d'organisation",
        ),
        "general": (
            "un document général",
            "présenter les informations principales du contenu",
        ),
    }
    return mapping.get(categorie, mapping["general"])


def normaliser_categorie_llm(label):
    """Rattache le libellé LLM à une catégorie interne stable."""
    label = label.lower()
    if "facture" in label or "devis" in label or "paiement" in label or "reçu" in label:
        return "facture_devis"
    if "contrat" in label or "convention" in label or "engagement" in label:
        return "contrat"
    if "financier" in label or "bancaire" in label:
        return "document_financier"
    if "tableau" in label or "données" in label or "donnees" in label:
        return "tableau"
    if "technique" in label:
        return "technique"
    if "pédagogique" in label or "pedagogique" in label or "cours" in label:
        return "pedagogique"
    if "administratif" in label or "rapport" in label:
        return "administratif"
    return "general"


def classement_llm_valide(categorie, score, texte, fichier=None, montants=None):
    """Empêche le LLM de surclasser un document sans indices concrets."""
    montants = montants or []
    if score < 0.55:
        return False
    if categorie == "facture_devis":
        return indices_facture(texte, montants)
    if categorie == "contrat":
        return indices_contrat(texte)
    if categorie == "document_financier":
        return bool(montants) and score >= 0.60
    if categorie == "tableau":
        return indices_tableau(texte, fichier=fichier) or score >= 0.75
    return score >= 0.60


def detecter_profil_document_llm(texte, fichier=None, montants=None):
    """Utilise un modèle zero-shot LLM pour classifier le document si disponible."""
    global _PIPELINE_DETECTION

    try:
        from transformers import pipeline
    except ImportError:
        return None

    modele = os.getenv("DETECTION_MODELE", DETECTION_MODELE_DEFAUT)
    labels = [
        "facture, devis ou document de paiement",
        "contrat, convention ou document d'engagement",
        "document financier non facturier",
        "tableau ou jeu de données",
        "document technique",
        "support pédagogique ou cours",
        "rapport ou document administratif",
        "document général",
    ]
    extrait = extraire_extrait_detection(texte)
    if not extrait:
        return None

    try:
        if _PIPELINE_DETECTION is None:
            _PIPELINE_DETECTION = pipeline(
                "zero-shot-classification",
                model=modele,
            )
        resultat = _PIPELINE_DETECTION(
            extrait,
            candidate_labels=labels,
            hypothesis_template="Ce document est {}.",
            multi_label=False,
        )
    except Exception:
        return None

    if not resultat or not resultat.get("labels"):
        return None

    label = resultat["labels"][0]
    score = float(resultat.get("scores", [0])[0])
    categorie = normaliser_categorie_llm(label)
    if not classement_llm_valide(categorie, score, texte, fichier=fichier, montants=montants):
        return None

    profil, objectif = profil_depuis_categorie(categorie)
    return {
        "profil": profil,
        "objectif": objectif,
        "categorie": categorie,
        "source": "llm",
        "score": score,
        "label": label,
    }


def detecter_profil_document(texte, fichier=None, montants=None, utiliser_llm_detection=True):
    """Déduit le type probable du document après lecture du contenu."""
    montants = montants or []

    if utiliser_llm_detection:
        detection_llm = detecter_profil_document_llm(texte, fichier=fichier, montants=montants)
        if detection_llm:
            return detection_llm

    texte_min = texte.lower()

    if indices_tableau(texte, fichier=fichier):
        profil, objectif = profil_depuis_categorie("tableau")
        return {"profil": profil, "objectif": objectif, "categorie": "tableau", "source": "heuristique", "score": 1.0}

    if indices_facture(texte, montants):
        profil, objectif = profil_depuis_categorie("facture_devis")
        return {"profil": profil, "objectif": objectif, "categorie": "facture_devis", "source": "heuristique", "score": 1.0}

    if indices_contrat(texte):
        profil, objectif = profil_depuis_categorie("contrat")
        return {"profil": profil, "objectif": objectif, "categorie": "contrat", "source": "heuristique", "score": 1.0}

    if montants:
        profil, objectif = profil_depuis_categorie("document_financier")
        return {"profil": profil, "objectif": objectif, "categorie": "document_financier", "source": "heuristique", "score": 0.85}

    profils = [
        (
            "technique",
            {"python", "code", "fonction", "fichier", "dossier", "erreur", "système", "traitement"},
        ),
        (
            "pedagogique",
            {"chapitre", "cours", "exemple", "méthode", "apprendre", "question", "réponse", "notion"},
        ),
        (
            "administratif",
            {"rapport", "note", "procédure", "demande", "service", "suivi", "organisation"},
        ),
    ]

    meilleure_categorie = "general"
    meilleur_score = 0
    for categorie, mots_cles in profils:
        score = sum(1 for mot in mots_cles if mot in texte_min)
        if score > meilleur_score:
            meilleur_score = score
            meilleure_categorie = categorie

    profil, objectif = profil_depuis_categorie(meilleure_categorie)
    return {
        "profil": profil,
        "objectif": objectif,
        "categorie": meilleure_categorie,
        "source": "heuristique",
        "score": meilleur_score,
    }


def analyser_document(texte, fichier=None, utiliser_llm_detection=True):
    """Analyse le contenu pour produire une synthèse ancrée dans le document."""
    lignes = lignes_significatives(texte)
    lignes_indexees = lignes_significatives_indexees(texte, limite=260)
    montants = extraire_montants_contextualises(texte)
    dates = extraire_dates(texte)
    references = extraire_references(texte)
    parties = extraire_parties(texte)
    titre = detecter_titre(lignes)
    entete = liste_unique(lignes[:5], limite=3)
    pied = liste_unique([ligne for ligne in lignes[-5:] if ligne not in montants], limite=3) if len(lignes) > 5 else []
    detection = detecter_profil_document(
        texte,
        fichier=fichier,
        montants=montants,
        utiliser_llm_detection=utiliser_llm_detection,
    )
    return {
        "profil": detection["profil"],
        "objectif": detection["objectif"],
        "categorie": detection["categorie"],
        "source_detection": detection["source"],
        "score_detection": detection["score"],
        "label_detection": detection.get("label", ""),
        "titre": titre,
        "entete": entete,
        "pied": pied,
        "montants": montants,
        "dates": dates,
        "references": references,
        "parties": parties,
        "mots_cles": extraire_mots_significatifs(texte),
        "nombre_lignes": len(lignes),
        "lignes_indexees": lignes_indexees,
        "evidence_titre": trouver_evidence_ligne(lignes_indexees, titre) if titre else None,
        "evidences_entete": evidences_depuis_valeurs(lignes_indexees, entete, limite=3),
        "evidences_pied": evidences_depuis_valeurs(lignes_indexees, pied, limite=3),
        "evidences_montants": evidences_depuis_valeurs(lignes_indexees, montants, limite=8),
        "evidences_dates": evidences_depuis_valeurs(lignes_indexees, dates, limite=5),
        "evidences_references": evidences_depuis_valeurs(lignes_indexees, references, limite=6),
        "evidences_parties": evidences_depuis_valeurs(lignes_indexees, parties, limite=5),
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


def ajouter_section_evidence(morceaux, titre, evidences, limite=5):
    """Ajoute une section citée uniquement si des preuves existent."""
    if not evidences:
        return
    lignes = [citation_evidence(evidence) for evidence in evidences[:limite]]
    morceaux.append(f"{titre} " + " ; ".join(lignes) + ".")


def informations_manquantes(analyse):
    """Liste les informations attendues mais non trouvées selon le type détecté."""
    categorie = analyse["categorie"]
    manquants = []
    if categorie == "facture_devis":
        if not analyse["montants"]:
            manquants.append("montants")
        if not analyse["dates"]:
            manquants.append("date de facture")
        if not analyse["references"]:
            manquants.append("numéro ou référence")
        if not analyse["parties"]:
            manquants.append("client/fournisseur")
    elif categorie == "contrat":
        if not analyse["parties"]:
            manquants.append("parties signataires")
        if not analyse["dates"]:
            manquants.append("date ou durée")
        if not analyse["montants"]:
            manquants.append("conditions financières")
    elif categorie == "document_financier" and not analyse["montants"]:
        manquants.append("montants exploitables")
    return manquants


def confiance_detection(analyse):
    """Exprime la confiance sans prétendre à une certitude absolue."""
    score = analyse.get("score_detection", 0)
    source = analyse.get("source_detection", "heuristique")
    if source == "llm":
        base = "élevée" if score >= 0.75 else "moyenne"
        return f"{base} (LLM validé par indices du document, score {score:.2f})"
    if analyse["categorie"] in {"facture_devis", "contrat"}:
        return "élevée (indices explicites trouvés dans le document)"
    if analyse["categorie"] == "document_financier":
        return "moyenne (montants détectés, sans preuve de facture)"
    return "moyenne (classification prudente basée sur le contenu)"


def generer_synthese_locale(
    texte,
    fichier=None,
    longueur_max=1400,
    utiliser_llm_detection=True,
):
    """Produit une synthèse sourcée, vérifiable et limitée aux preuves du fichier."""
    analyse = analyser_document(
        texte,
        fichier=fichier,
        utiliser_llm_detection=utiliser_llm_detection,
    )
    if not analyse["mots_cles"] and not analyse["montants"] and not analyse["titre"]:
        return "Le contenu contient trop peu de texte exploitable pour produire une synthèse fiable."

    morceaux = [
        "Résumé vérifié basé uniquement sur le contenu extrait du fichier.",
        f"Nature détectée : {analyse['profil']} ; confiance {confiance_detection(analyse)}.",
    ]

    if analyse["evidence_titre"]:
        morceaux.append(f"Titre ou en-tête principal : {citation_evidence(analyse['evidence_titre'])}.")
        evidences_entete_restantes = [
            evidence
            for evidence in analyse["evidences_entete"]
            if evidence.get("ligne") != analyse["evidence_titre"].get("ligne")
        ]
        ajouter_section_evidence(morceaux, "Extraits clés du début du document :", evidences_entete_restantes, limite=3)
    else:
        ajouter_section_evidence(morceaux, "Indices d'en-tête :", analyse["evidences_entete"], limite=3)

    ajouter_section_evidence(morceaux, "Références identifiantes :", analyse["evidences_references"], limite=4)
    ajouter_section_evidence(morceaux, "Parties ou acteurs cités :", analyse["evidences_parties"], limite=4)
    ajouter_section_evidence(morceaux, "Dates visibles :", analyse["evidences_dates"], limite=4)
    ajouter_section_evidence(morceaux, "Montants détectés :", analyse["evidences_montants"], limite=6)

    if analyse["categorie"] == "facture_devis":
        morceaux.append(
            "Interprétation : le fichier ressemble à une facture/devis uniquement parce que des indices de facturation et des montants contextualisés sont présents."
        )
    elif analyse["categorie"] == "contrat":
        morceaux.append(
            "Interprétation : le fichier ressemble à un contrat car il contient plusieurs indices d'engagement, de parties ou de clauses."
        )
    elif analyse["categorie"] == "document_financier":
        morceaux.append(
            "Interprétation : des montants sont présents, mais les preuves ne suffisent pas à conclure qu'il s'agit d'une facture."
        )
    else:
        themes = joindre_liste(analyse["mots_cles"][:4])
        morceaux.append(f"Interprétation : le contenu porte principalement sur {themes}.")

    ajouter_section_evidence(morceaux, "Éléments de fin de document :", analyse["evidences_pied"], limite=2)

    manquants = informations_manquantes(analyse)
    if manquants:
        morceaux.append("Non trouvé explicitement dans le contenu extrait : " + joindre_liste(manquants) + ".")

    morceaux.append("Aucune information non citée ci-dessus n'est ajoutée au résumé.")
    return limiter_texte(" ".join(morceaux), longueur_max)


def generer_resume_texte(
    texte,
    nombre_phrases=3,
    longueur_max=1400,
    fichier=None,
    utiliser_ia=False,
    utiliser_llm_detection=True,
):
    """
    Génère un résumé vérifiable façon réponse sourcée.

    Le résumé final est construit uniquement à partir des éléments cités dans
    le fichier. Le LLM peut aider à détecter le type de document, mais il
    n'écrit pas librement les faits du résumé.
    """
    texte_original = texte
    texte_nettoye = nettoyer_texte(texte_original)
    if not texte_nettoye:
        return ""

    # Pour garantir un résultat exact, le LLM n'écrit pas le résumé final.
    # Il peut seulement aider à la détection du type via utiliser_llm_detection.
    return generer_synthese_locale(
        texte_original,
        fichier=fichier,
        longueur_max=longueur_max,
        utiliser_llm_detection=utiliser_llm_detection,
    )


def generer_resume_fichier(fichier, utiliser_ia=False, utiliser_llm_detection=True):
    """Génère une synthèse réaliste d'un fichier collecté à partir de son contenu."""
    texte = extraire_texte_fichier(fichier)
    if texte:
        return generer_resume_texte(
            texte,
            fichier=fichier,
            utiliser_ia=utiliser_ia,
            utiliser_llm_detection=utiliser_llm_detection,
        )

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
    utiliser_llm_detection=True,
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
            print(
                f"   {generer_resume_fichier(fichier, utiliser_ia=utiliser_ia, utiliser_llm_detection=utiliser_llm_detection)}\n"
            )

        if max_resumes is not None and len(fichiers) > max_resumes:
            print(f"... et {len(fichiers) - max_resumes} autre(s) fichier(s) non résumé(s)")

    return fichiers


if __name__ == "__main__":
    afficher_avec_statistiques(
        "/mnt/nvme-SAMSUNG_MZALQ256HBJD-00BL2_S65FNX0T804700-part4/Book",
        recursif=True,
        avec_resumes=True,
    )
