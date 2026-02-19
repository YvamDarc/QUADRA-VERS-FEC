import re
from datetime import datetime
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Quadra → FEC (ACD)", layout="wide")
st.title("Quadra → FEC (import ACD)")

FEC_COLS = [
    "JournalCode", "JournalLib", "EcritureNum", "EcritureDate",
    "CompteNum", "CompteLib", "CompAuxNum", "CompAuxLib",
    "PieceRef", "PieceDate", "EcritureLib",
    "Debit", "Credit", "EcritureLet", "DateLet", "ValidDate",
    "Montantdevise", "Idevise"
]

def safe_decode(b: bytes) -> str:
    # Quadra est souvent en ANSI
    for enc in ("latin1", "cp1252", "utf-8"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", errors="replace")

def clean_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def ddmmyy_to_yyyymmdd(s: str, pivot: int = 70) -> str:
    """
    Ex: 311025 -> 20251031 (pivot 70 => 00-69 => 2000, 70-99 => 1900)
    """
    s = (s or "").strip()
    m = re.fullmatch(r"(\d{2})(\d{2})(\d{2})", s)
    if not m:
        return ""
    dd, mm, yy = map(int, m.groups())
    yyyy = (1900 + yy) if yy >= pivot else (2000 + yy)
    try:
        return datetime(yyyy, mm, dd).strftime("%Y%m%d")
    except ValueError:
        return ""

def amount_cents_to_str(x: str) -> str:
    """
    '+000000007200' -> '72.00'
    """
    x = (x or "").strip()
    if not x:
        return "0.00"
    sign = -1 if x.startswith("-") else 1
    digits = re.sub(r"[^\d]", "", x)
    if not digits:
        return "0.00"
    return f"{sign * (int(digits) / 100):.2f}"

def parse_C(line: str):
    """
    Exemple:
    C61860000ABONNEMENT DILICOM            ABONNEM+000000030189...
    -> compte=61860000, lib="ABONNEMENT DILICOM"
    """
    m = re.match(r"^C(\d{8})(.*)$", line)
    if not m:
        return None
    compte = m.group(1)
    rest = m.group(2)

    # coupe au premier motif CODE+000000...
    parts = re.split(r"[A-Z0-9]{5,10}\+[+-]?\d{6,}", rest, maxsplit=1)
    lib = clean_spaces(parts[0] if parts else rest)
    if not lib:
        lib = f"Compte {compte}"
    return compte, lib

def parse_M(line: str, pivot_year: int):
    """
    Parsing basé sur ton exemple :
    M08112400AC000010925TGLBD                C+000000007200        311025gn   1/09 ... EURAC ... 0000011198\\SER...

    On extrait :
    - CompteNum : 8 chiffres après M
    - JournalCode : 2 chars suivants
    - EcritureNum : 9 chars suivants
    - CompAuxNum : token TXXXX -> XXXX (si présent)
    - Sens+Montant : C/D + montant
    - Date : premier 6 chiffres après montant (ddmmyy)
    - PieceRef : dernier token de la ligne
    """
    if not line.startswith("M") or len(line) < 20:
        return None

    compte = line[1:9].strip()
    journal = line[9:11].strip()
    ecr_num = line[11:20].strip()
    tail = line[20:]

    # Aux (TGLBD => GLBD)
    aux_num, aux_lib = "", ""
    m_aux = re.search(r"\bT([A-Z0-9]{2,30})\b", tail)
    if m_aux:
        aux_num = m_aux.group(1)
        aux_lib = aux_num

    # Sens + montant
    m_amt = re.search(r"\b([CD])\s*([+-]\d{10,})\b", tail) or re.search(r"\b([CD])([+-]\d{10,})\b", tail)
    if not m_amt:
        return None
    sens = m_amt.group(1)
    amt = amount_cents_to_str(m_amt.group(2))

    debit, credit = "0.00", "0.00"
    if sens == "D":
        debit = amt
    else:
        credit = amt

    # Date (ddmmyy) après le montant
    after = tail[m_amt.end():]
    m_date = re.search(r"\b(\d{6})\b", after)
    ecr_date = ddmmyy_to_yyyymmdd(m_date.group(1), pivot=pivot_year) if m_date else ""

    # Devise (souvent EUR)
    m_dev = re.search(r"\b([A-Z]{3})\b", tail)
    idevise = m_dev.group(1) if m_dev else ""

    # PieceRef = dernier token non vide
    tokens = [t for t in re.split(r"\s+", line.strip()) if t]
    piece_ref = tokens[-1] if tokens else ecr_num

    ecr_lib = aux_num or piece_ref

    return {
        "JournalCode": journal,
        "JournalLib": journal,
        "EcritureNum": ecr_num,
        "EcritureDate": ecr_date,
        "CompteNum": compte,
        "CompteLib": "",  # rempli après via plan C
        "CompAuxNum": aux_num,
        "CompAuxLib": aux_lib,
        "PieceRef": piece_ref,
        "PieceDate": ecr_date,
        "EcritureLib": ecr_lib,
        "Debit": debit,
        "Credit": credit,
        "EcritureLet": "",
        "DateLet": "",
        "ValidDate": ecr_date,
        "Montantdevise": "",
        "Idevise": idevise,
    }

# --- UI ---
c1, c2, c3 = st.columns(3)
with c1:
    sep_choice = st.selectbox("Séparateur FEC", ["TAB", "|"], index=0)
with c2:
    pivot = st.number_input("Pivot année (YY)", min_value=0, max_value=99, value=70, step=1)
with c3:
    add_header = st.checkbox("Ajouter en-tête (souvent NON)", value=False)

uploaded = st.file_uploader("Dépose ton export Quadra (TXT)", type=["txt", "asc", "dat"])

if not uploaded:
    st.info("Dépose un fichier Quadra pour générer le FEC.")
    st.stop()

raw = safe_decode(uploaded.read())
lines = [l.rstrip("\n\r") for l in raw.splitlines() if l.strip()]

# Plan comptable
plan = {}
for l in lines:
    if l.startswith("C"):
        p = parse_C(l)
        if p:
            plan[p[0]] = p[1]

# Mouvements
rows = []
for l in lines:
    if l.startswith("M"):
        r = parse_M(l, pivot_year=int(pivot))
        if r:
            rows.append(r)

if not rows:
    st.error("Aucune ligne M n’a été convertie. (Ton export M est peut-être d’un autre format)")
    st.stop()

df = pd.DataFrame(rows, columns=FEC_COLS)

# Injecte CompteLib depuis plan comptable
df["CompteLib"] = df["CompteNum"].map(plan).fillna(df["CompteNum"].map(lambda x: f"Compte {x}"))

st.subheader("Aperçu (100 premières lignes)")
st.dataframe(df.head(100), use_container_width=True)

st.write(f"Comptes C trouvés : **{len(plan)}** — Lignes M converties : **{len(df)}**")

sep = "\t" if sep_choice == "TAB" else "|"
out_lines = []
if add_header:
    out_lines.append(sep.join(FEC_COLS))

for _, r in df.iterrows():
    out_lines.append(sep.join("" if pd.isna(r[c]) else str(r[c]) for c in FEC_COLS))

out_text = "\n".join(out_lines)

st.download_button(
    "⬇️ Télécharger le FEC",
    data=out_text.encode("utf-8"),
    file_name="export_fec.txt",
    mime="text/plain"
)
