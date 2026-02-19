import re
from datetime import datetime
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Quadra -> FEC (ACD)", layout="wide")

st.title("Conversion Quadra (C/M) ➜ FEC importable ACD")

st.markdown(
    """
- **Lignes `C...`** : plan comptable (création du libellé du compte)
- **Lignes `M...`** : mouvements (écritures)
- **Sortie** : fichier **FEC A47 (18 colonnes)**, séparateur **TAB** ou **|**
"""
)

# --- Helpers ---
FEC_COLS_18 = [
    "JournalCode", "JournalLib", "EcritureNum", "EcritureDate",
    "CompteNum", "CompteLib", "CompAuxNum", "CompAuxLib",
    "PieceRef", "PieceDate", "EcritureLib",
    "Debit", "Credit", "EcritureLet", "DateLet", "ValidDate",
    "Montantdevise", "Idevise"
]

def parse_amount_cents(raw: str) -> str:
    """
    raw ex: '+000000007200' (centimes) => '72.00'
    On renvoie une string avec point décimal (souvent mieux accepté).
    """
    raw = raw.strip()
    if not raw:
        return "0"
    sign = 1
    if raw[0] == "-":
        sign = -1
    digits = re.sub(r"[^\d]", "", raw)
    if not digits:
        return "0"
    val = sign * (int(digits) / 100.0)
    # format sans séparateur de milliers
    return f"{val:.2f}"

def parse_ddmmyy(s: str, pivot_year: int = 70) -> str | None:
    """
    '311025' => '20251031' (pivot_year=70 => 00-69=>2000, 70-99=>1900)
    Renvoie YYYYMMDD ou None.
    """
    s = s.strip()
    m = re.match(r"^(\d{2})(\d{2})(\d{2})$", s)
    if not m:
        return None
    dd, mm, yy = map(int, m.groups())
    yyyy = 1900 + yy if yy >= pivot_year else 2000 + yy
    try:
        d = datetime(yyyy, mm, dd)
        return d.strftime("%Y%m%d")
    except ValueError:
        return None

def clean_text(x: str) -> str:
    return re.sub(r"\s+", " ", (x or "").strip())

def parse_C_line(line: str) -> tuple[str, str] | None:
    """
    D’après l’exemple:
    C61860000ABONNEMENT DILICOM            ABONNEM+000000030189...

    Hypothèse robuste:
    - CompteNum = 8 chiffres après le 'C'
    - CompteLib = texte qui suit jusqu'à avant le bloc "CODE+MONTANTS..."
      => on prend les ~40-50 premiers chars après le compte et on nettoie.

    Si tu veux “verrouiller” une largeur, on peut raffiner ensuite.
    """
    if not line.startswith("C"):
        return None
    m = re.match(r"^C(\d{8})(.*)$", line)
    if not m:
        return None
    compte = m.group(1)
    rest = m.group(2)

    # On coupe au premier motif de type CODE+000000 (ex: ABONNEM+000000...)
    cut = re.split(r"[A-Z0-9]{5,10}\+[-+]?0{3,}\d+", rest, maxsplit=1)
    lib = cut[0]

    lib = clean_text(lib)
    # garde-fou : si lib vide, on prend un fallback
    if not lib:
        lib = f"Compte {compte}"
    return compte, lib

def parse_M_line(line: str, pivot_year: int) -> dict | None:
    """
    Exemple:
    M08112400AC000010925TGLBD                C+000000007200        311025gn   1/09 ... EURAC    GLBD ... 0000011198\\SER...

    Hypothèses (basées sur ton échantillon):
    - CompteNum = positions 1:9 (8 chiffres)
    - JournalCode = positions 9:11 (2 chars)
    - EcritureNum = positions 11:20 (9 chars)
    - Le bloc qui suit peut contenir un code auxiliaire précédé de 'T' (ex: TGLBD)
    - Sens/Montant : motif (C|D)+000000....
    - Date pièce : motif 6 chiffres (ddmmyy) juste après le montant (ex: 311025)
    - Devise : motif EUR...
    - PieceRef : dernier token non vide (souvent contient '\\')
    """
    if not line.startswith("M"):
        return None

    if len(line) < 20:
        return None

    compte = line[1:9].strip()
    journal = line[9:11].strip()
    ecriture_num = line[11:20].strip()

    tail = line[20:]

    # Aux (souvent "TGLBD")
    comp_aux_num = ""
    comp_aux_lib = ""
    m_aux = re.search(r"\bT([A-Z0-9]{2,20})\b", tail)
    if m_aux:
        comp_aux_num = m_aux.group(1)
        comp_aux_lib = comp_aux_num

    # Sens + Montant
    m_amt = re.search(r"\b([CD])\s*([+-]\d{10,})\b", tail)
    # parfois collé: "C+0000..."
    if not m_amt:
        m_amt = re.search(r"\b([CD])([+-]\d{10,})\b", tail)
    if not m_amt:
        return None

    sens = m_amt.group(1)
    amt_raw = m_amt.group(2)
    amount = parse_amount_cents(amt_raw)

    debit = "0.00"
    credit = "0.00"
    if sens == "D":
        debit = amount
    else:
        credit = amount

    # Date (ddmmyy) après le montant
    after_amt = tail[m_amt.end():]
    m_date = re.search(r"\b(\d{6})\b", after_amt)
    ecr_date = None
    if m_date:
        ecr_date = parse_ddmmyy(m_date.group(1), pivot_year=pivot_year)

    # Devise
    m_dev = re.search(r"\b([A-Z]{3})\b", tail)
    idevise = m_dev.group(1) if m_dev else ""

    # PieceRef = dernier “token” non vide
    tokens = [t for t in re.split(r"\s+", line.strip()) if t]
    piece_ref = tokens[-1] if tokens else ecriture_num

    # EcritureLib : on met l’aux si présent, sinon le piece_ref
    ecriture_lib = comp_aux_num or piece_ref

    # ValidDate : par défaut = EcritureDate si dispo
    valid_date = ecr_date or ""

    return {
        "JournalCode": journal,
        "JournalLib": journal,          # tu peux remplacer par une table de correspondance
        "EcritureNum": ecriture_num,
        "EcritureDate": ecr_date or "",
        "CompteNum": compte,
        "CompteLib": "",                # rempli ensuite via plan comptable (lignes C)
        "CompAuxNum": comp_aux_num,
        "CompAuxLib": comp_aux_lib,
        "PieceRef": piece_ref,
        "PieceDate": ecr_date or "",
        "EcritureLib": ecriture_lib,
        "Debit": debit,
        "Credit": credit,
        "EcritureLet": "",
        "DateLet": "",
        "ValidDate": valid_date,
        "Montantdevise": "",            # si tu veux, on peut recopier Debit/Credit ici
        "Idevise": idevise,
    }

# --- UI ---
col1, col2, col3 = st.columns(3)

with col1:
    sep_choice = st.selectbox("Séparateur de sortie FEC", ["TAB", "|"], index=0)
with col2:
    pivot = st.number_input("Pivot année (YY >= pivot => 19YY sinon 20YY)", min_value=0, max_value=99, value=70, step=1)
with col3:
    add_header = st.checkbox("Ajouter une ligne d’en-tête (souvent NON)", value=False)

uploaded = st.file_uploader("Dépose ton fichier Quadra (TXT)", type=["txt", "asc", "dat"])
st.divider()

if uploaded:
    raw = uploaded.read().decode("latin1", errors="replace")
    lines = [l.rstrip("\n\r") for l in raw.splitlines() if l.strip()]

    # 1) Plan comptable depuis C
    plan = {}
    for l in lines:
        if l.startswith("C"):
            parsed = parse_C_line(l)
            if parsed:
                plan[parsed[0]] = parsed[1]

    # 2) Ecritures depuis M
    rows = []
    for l in lines:
        if l.startswith("M"):
            r = parse_M_line(l, pivot_year=int(pivot))
            if r:
                rows.append(r)

    df = pd.DataFrame(rows, columns=FEC_COLS_18)

    # Inject CompteLib depuis plan
    if not df.empty:
        df["CompteLib"] = df["CompteNum"].map(plan).fillna(df["CompteNum"].map(lambda x: f"Compte {x}"))

        # Garde-fous: dates vides => warning
        missing_dates = (df["EcritureDate"].astype(str).str.strip() == "").sum()

        left, right = st.columns([2, 1])
        with left:
            st.subheader("Aperçu")
            st.dataframe(df.head(50), use_container_width=True)
        with right:
            st.subheader("Stats")
            st.write(f"Comptes (C) trouvés : **{len(plan)}**")
            st.write(f"Lignes M converties : **{len(df)}**")
            if missing_dates:
                st.warning(f"{missing_dates} ligne(s) sans date (EcritureDate/PieceDate).")

        # Export texte FEC
        sep = "\t" if sep_choice == "TAB" else "|"
        out_lines = []

        if add_header:
            out_lines.append(sep.join(FEC_COLS_18))

        # IMPORTANT: FEC attend souvent pas d'espaces parasites
        for _, r in df.iterrows():
            out_lines.append(sep.join("" if pd.isna(r[c]) else str(r[c]) for c in FEC_COLS_18))

        out_text = "\n".join(out_lines)

        st.download_button(
            "⬇️ Télécharger le FEC (txt)",
            data=out_text.encode("utf-8"),
            file_name="export_fec.txt",
            mime="text/plain"
        )
    else:
        st.error("Aucune ligne M exploitable trouvée. (Le parsing doit être ajusté à ton export.)")
else:
    st.info("Dépose un fichier pour lancer la conversion.")
