import re
from datetime import datetime
import pandas as pd
import streamlit as st

# =========================
# CONFIG
# =========================
st.set_page_config(page_title="Quadra → FEC (ACD)", layout="wide")
st.title("Quadra ASCII → FEC (import ACD)")
st.caption("Parsing en positions fixes (types M/C/R/I…), conforme au cahier ASCII QuadraCOMPTA.")

# FEC (18 colonnes obligatoires)
FEC_COLS = [
    "JournalCode", "JournalLib", "EcritureNum", "EcritureDate",
    "CompteNum", "CompteLib", "CompAuxNum", "CompAuxLib",
    "PieceRef", "PieceDate", "EcritureLib",
    "Debit", "Credit", "EcritureLet", "DateLet", "ValidDate",
    "Montantdevise", "Idevise"
]

# =========================
# UTILITAIRES
# =========================
def safe_decode(b: bytes) -> str:
    for enc in ("latin1", "cp1252", "utf-8"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", errors="replace")

def sfix(line: str, pos1: int, length: int) -> str:
    """
    Slice positions fixes Quadra (pos1 = position 1-based).
    Retourne '' si la ligne est trop courte.
    """
    start = pos1 - 1
    end = start + length
    if start >= len(line):
        return ""
    return line[start:end]

def clean_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def ddmmyy_to_yyyymmdd(ddmmyy: str, pivot: int = 70) -> str:
    ddmmyy = re.sub(r"\D", "", (ddmmyy or "").strip())
    if len(ddmmyy) != 6:
        return ""
    dd, mm, yy = int(ddmmyy[0:2]), int(ddmmyy[2:4]), int(ddmmyy[4:6])
    yyyy = (1900 + yy) if yy >= pivot else (2000 + yy)
    try:
        return datetime(yyyy, mm, dd).strftime("%Y%m%d")
    except ValueError:
        return ""

def signed_cents_to_amount_str(signed13: str) -> str:
    """
    Quadra: "Montant en centimes signé (position 43=signe) 43 13"
    => ex: '+000000001318' => 13.18
    """
    x = (signed13 or "").strip()
    if not x:
        return "0.00"
    sign = -1 if x[0] == "-" else 1
    digits = re.sub(r"[^\d]", "", x)
    if not digits:
        return "0.00"
    return f"{sign * (int(digits) / 100):.2f}"

def nonempty(*vals) -> str:
    for v in vals:
        v = (v or "").strip()
        if v:
            return v
    return ""

def make_ecriture_num(journal: str, date_yyyymmdd: str, piece: str, seq: int) -> str:
    base = f"{journal}{date_yyyymmdd}{piece}".strip()
    if not base:
        base = f"ECR{seq}"
    # sécurise longueur raisonnable
    return (base[:18] + f"{seq%100:02d}") if len(base) > 20 else base

# =========================
# SPEC QUADRA (QC_ASC)
# =========================
# Lignes M (écritures) — positions fixes (1-based) : voir QC_ASC (mise à jour 2023). :contentReference[oaicite:2]{index=2}
# * Type=M pos1 len1
# * Compte pos2 len8
# * Journal(2) pos10 len2
# * Folio pos12 len3
# * Date écriture pos15 len6 (JJMMAA)
# Code libellé pos21 len1
# Libellé libre pos22 len20
# * Sens pos42 len1 (D/C)
# * Montant signé pos43 len13
# Contrepartie pos56 len8
# Date échéance pos64 len6 (JJMMAA)
# Code lettrage pos70 len2
# Code stats pos72 len3
# N° pièce (5) pos75 len5
# Code affaire pos80 len10
# Quantité1 pos90 len10
# N° pièce (8) pos100 len8
# Devise pos108 len3
# Journal(3) pos111 len3 (QC Windows)
# Flag TVA gérée pos114 len1 ; Code TVA pos115 len1 ; Méthode TVA pos116 len1
# Libellé 30 pos117 len30
# Code TVA(2) pos147 len2
# N° pièce 10 pos149 len10
# Réservé pos159 len10
# Montant devise signé pos169 len13
# Pièce jointe nom fichier pos182 len12
# Quantité2 pos194 len10
# Export: NumUniq pos204 len10 ; Operateur pos214 len4 ; Date système pos218 len14 ; N° pièce prioritaire pos232 len20
#
# Lignes C (comptes) : pos1 type=C ; pos2 compte(8) ; pos10 lib(30) … :contentReference[oaicite:3]{index=3}
#
# Lignes R (règlements tiers) : type=R ; pos2 date échéance ; pos8 montant échéance … Doivent suivre la M correspondante. :contentReference[oaicite:4]{index=4}
#
# Lignes I (lignes analytiques) : type=I ; pos2 % ; pos7 montant ; pos20 centre ; pos30 nature (QC Windows). Doivent suivre la M correspondante. :contentReference[oaicite:5]{index=5}

def parse_C(line: str):
    if not line or line[0:1] != "C":
        return None
    compte = sfix(line, 2, 8).strip()
    lib = sfix(line, 10, 30)
    lib = clean_spaces(lib)
    if compte:
        return compte, (lib if lib else f"Compte {compte}")
    return None

def parse_M(line: str, pivot_year: int):
    if not line or line[0:1] != "M":
        return None

    compte = sfix(line, 2, 8).strip()
    journal2 = sfix(line, 10, 2).strip()
    folio = sfix(line, 12, 3).strip()
    date_jjmmaa = sfix(line, 15, 6).strip()
    date_yyyymmdd = ddmmyy_to_yyyymmdd(date_jjmmaa, pivot=pivot_year)

    lib20 = clean_spaces(sfix(line, 22, 20))
    lib30 = clean_spaces(sfix(line, 117, 30))
    ecriture_lib = nonempty(lib30, lib20)

    sens = sfix(line, 42, 1).strip().upper()
    montant_signed13 = sfix(line, 43, 13)
    montant = signed_cents_to_amount_str(montant_signed13)

    debit = "0.00"
    credit = "0.00"
    if sens == "D":
        debit = montant
    elif sens == "C":
        credit = montant
    else:
        # si sens inconnu, on laisse à 0 et on garde le montant dans lib (debug)
        pass

    contrepartie = sfix(line, 56, 8).strip()
    ech_jjmmaa = sfix(line, 64, 6).strip()
    ech_yyyymmdd = ddmmyy_to_yyyymmdd(ech_jjmmaa, pivot=pivot_year)

    lettrage2 = sfix(line, 70, 2).strip()
    piece5 = sfix(line, 75, 5).strip()
    piece8 = sfix(line, 100, 8).strip()
    piece10 = sfix(line, 149, 10).strip()
    piece20 = sfix(line, 232, 20).strip()  # prioritaire (règle Quadra) :contentReference[oaicite:6]{index=6}

    # Règle de priorité pour le N° de pièce (doc Quadra) : 232 > 149 > 100 > 75 :contentReference[oaicite:7]{index=7}
    piece_ref = nonempty(piece20, piece10, piece8, piece5)

    devise = sfix(line, 108, 3).strip()
    montant_devise_signed13 = sfix(line, 169, 13)
    montant_devise = ""
    idevise = ""
    if devise:
        idevise = devise
        md = signed_cents_to_amount_str(montant_devise_signed13)
        # si le champ est vide (0.00) et qu’on veut aussi le montant principal, on pourrait le recopier,
        # mais on reste strict: on ne remplit que si présent.
        if md != "0.00":
            montant_devise = md

    piece_jointe = sfix(line, 182, 12).strip()
    code_affaire = sfix(line, 80, 10).strip()

    return {
        # champs "métier" (non FEC)
        "_Folio": folio,
        "_Contrepartie": contrepartie,
        "_DateEcheance": ech_yyyymmdd,
        "_CodeAffaire": code_affaire,
        "_PieceJointe": piece_jointe,
        "_Journal2": journal2,

        # champs FEC
        "JournalCode": journal2,
        "JournalLib": journal2,
        "EcritureNum": "",  # créé ensuite (séquence continue)
        "EcritureDate": date_yyyymmdd,
        "CompteNum": compte,
        "CompteLib": "",     # injecté depuis C
        "CompAuxNum": "",
        "CompAuxLib": "",
        "PieceRef": piece_ref,
        "PieceDate": date_yyyymmdd,
        "EcritureLib": ecriture_lib,
        "Debit": debit,
        "Credit": credit,
        "EcritureLet": lettrage2,  # on met le code lettrage (2) dans EcritureLet
        "DateLet": "",
        "ValidDate": date_yyyymmdd,
        "Montantdevise": montant_devise,
        "Idevise": idevise,
    }

def parse_R(line: str, pivot_year: int):
    if not line or line[0:1] != "R":
        return None
    # Type=R pos1 ; Date échéance pos2 len6 ; Montant échéance pos8 len13 ; Mode reglt pos21 len2 ; Journal banque pos23 len2 ... :contentReference[oaicite:8]{index=8}
    ech = ddmmyy_to_yyyymmdd(sfix(line, 2, 6).strip(), pivot=pivot_year)
    mnt = signed_cents_to_amount_str(sfix(line, 8, 13))
    mode = sfix(line, 21, 2).strip()
    jbnk = sfix(line, 23, 2).strip()
    ref = sfix(line, 25, 10).strip()
    return {"_R_DateEcheance": ech, "_R_MontantEcheance": mnt, "_R_ModeRglt": mode, "_R_JournalBanque": jbnk, "_R_RefTire": ref}

def parse_I(line: str):
    if not line or line[0:1] != "I":
        return None
    # Type=I pos1 ; % pos2 len5 ; Montant pos7 len13 ; Centre pos20 len10 ; Nature pos30 len10 (QC Windows) :contentReference[oaicite:9]{index=9}
    pct = sfix(line, 2, 5).strip()
    mnt = signed_cents_to_amount_str(sfix(line, 7, 13))
    centre = sfix(line, 20, 10).strip()
    nature = sfix(line, 30, 10).strip()
    return {"_I_Pct": pct, "_I_Montant": mnt, "_I_Centre": centre, "_I_Nature": nature}

# =========================
# UI
# =========================
c1, c2, c3, c4 = st.columns(4)
with c1:
    sep_choice = st.selectbox("Séparateur FEC", ["TAB", "|"], index=0)
with c2:
    pivot = st.number_input("Pivot année (YY)", min_value=0, max_value=99, value=70, step=1)
with c3:
    add_header = st.checkbox("Ajouter en-tête FEC", value=True)
with c4:
    inject_ech = st.checkbox("Ajouter l’échéance dans EcritureLib", value=True)

uploaded = st.file_uploader("Dépose ton fichier ASCII Quadra (TXT)", type=["txt", "asc", "dat"])

if not uploaded:
    st.info("Dépose un fichier pour générer le FEC.")
    st.stop()

raw = safe_decode(uploaded.read())
lines = [l.rstrip("\n\r") for l in raw.splitlines() if l.strip()]

# =========================
# PARSING
# =========================
plan = {}
m_rows = []
meta_rows = []  # pour export contrôle (incl échéance, pièce jointe, analytique…)

last_m_idx = None  # index dans m_rows pour rattacher R/I qui suivent la M

for line in lines:
    t = line[0:1]

    if t == "C":
        p = parse_C(line)
        if p:
            plan[p[0]] = p[1]

    elif t == "M":
        m = parse_M(line, pivot_year=int(pivot))
        if m:
            m_rows.append(m)
            meta_rows.append({
                "Journal": m["JournalCode"],
                "DateEcriture": m["EcritureDate"],
                "Compte": m["CompteNum"],
                "SensDebit": m["Debit"],
                "SensCredit": m["Credit"],
                "PieceRef": m["PieceRef"],
                "Libelle": m["EcritureLib"],
                "DateEcheance(M)": m["_DateEcheance"],
                "CodeAffaire": m["_CodeAffaire"],
                "PieceJointe": m["_PieceJointe"],
            })
            last_m_idx = len(m_rows) - 1

    elif t == "R" and last_m_idx is not None:
        r = parse_R(line, pivot_year=int(pivot))
        if r:
            # on attache l’échéance R (si présente) : souvent plus “métier” que celle de M
            if r["_R_DateEcheance"]:
                m_rows[last_m_idx]["_DateEcheance"] = r["_R_DateEcheance"]
            # on stocke aussi en meta
            meta_rows[-1].update({
                "DateEcheance(R)": r["_R_DateEcheance"],
                "MontantEcheance(R)": r["_R_MontantEcheance"],
                "ModeRglt(R)": r["_R_ModeRglt"],
                "JournalBanque(R)": r["_R_JournalBanque"],
                "RefTire(R)": r["_R_RefTire"],
            })

    elif t == "I" and last_m_idx is not None:
        i = parse_I(line)
        if i:
            # on concatène l’info analytique dans le libellé meta (sans toucher au FEC par défaut)
            meta_rows[-1].update({
                "Anal_Centre": i["_I_Centre"],
                "Anal_Nature": i["_I_Nature"],
                "Anal_Montant": i["_I_Montant"],
                "Anal_Pct": i["_I_Pct"],
            })

# =========================
# CONSTRUCTION FEC
# =========================
if not m_rows:
    st.error("Aucune ligne M lue. Vérifie que ton fichier est bien au format ASCII QuadraCOMPTA.")
    st.stop()

df = pd.DataFrame(m_rows)

# Injecte CompteLib depuis le plan (lignes C)
df["CompteLib"] = df["CompteNum"].map(plan).fillna(df["CompteNum"].map(lambda x: f"Compte {x}"))

# Ajoute échéance dans libellé si demandé
if inject_ech:
    def _lib_with_ech(row):
        ech = (row.get("_DateEcheance") or "").strip()
        if ech:
            base = row["EcritureLib"] or ""
            return (base[:200] + f" | ECH:{ech}") if base else f"ECH:{ech}"
        return row["EcritureLib"]
    df["EcritureLib"] = df.apply(_lib_with_ech, axis=1)

# EcritureNum = séquence continue (obligatoire FEC) :contentReference[oaicite:10]{index=10}
# On crée un numéro stable et unique : journal + date + pièce + compteur
seen = {}
ecriture_nums = []
for idx, row in df.iterrows():
    key = (row["JournalCode"], row["EcritureDate"], row["PieceRef"])
    seen[key] = seen.get(key, 0) + 1
    ecriture_nums.append(make_ecriture_num(row["JournalCode"], row["EcritureDate"], row["PieceRef"], seen[key]))
df["EcritureNum"] = ecriture_nums

# Assure les colonnes FEC
df_fec = df[FEC_COLS].copy()

# =========================
# AFFICHAGE
# =========================
left, right = st.columns([2, 1])

with left:
    st.subheader("Aperçu FEC (100 premières lignes)")
    st.dataframe(df_fec.head(100), use_container_width=True)

with right:
    st.subheader("Contrôles")
    st.write(f"Comptes (C) trouvés : **{len(plan)}**")
    st.write(f"Lignes M converties : **{len(df_fec)}**")
    missing_dates = (df_fec["EcritureDate"].astype(str).str.strip() == "").sum()
    if missing_dates:
        st.warning(f"{missing_dates} ligne(s) sans Date écriture (pos 15–20).")

    st.caption("Note : l’échéance n’est pas un champ FEC standard ; option = ajout dans libellé ou export annexe. ")

# Export annexe (contrôle complet)
df_meta = pd.DataFrame(meta_rows)

st.subheader("Aperçu contrôle (incl. échéance, pièce jointe, analytique)")
st.dataframe(df_meta.head(100), use_container_width=True)

# =========================
# EXPORTS
# =========================
sep = "\t" if sep_choice == "TAB" else "|"

out_lines = []
if add_header:
    out_lines.append(sep.join(FEC_COLS))

for _, r in df_fec.iterrows():
    out_lines.append(sep.join("" if pd.isna(r[c]) else str(r[c]) for c in FEC_COLS))

fec_text = "\n".join(out_lines)

st.download_button(
    "⬇️ Télécharger le FEC (18 colonnes)",
    data=fec_text.encode("utf-8"),
    file_name="export_fec.txt",
    mime="text/plain"
)

# Export annexe CSV de contrôle
csv_bytes = df_meta.to_csv(index=False, sep=";", encoding="utf-8").encode("utf-8")
st.download_button(
    "⬇️ Télécharger le contrôle (CSV ;)",
    data=csv_bytes,
    file_name="controle_quadra.csv",
    mime="text/csv"
)
