import re
from datetime import datetime
import pandas as pd
import streamlit as st

# =========================================================
# APP
# =========================================================
st.set_page_config(page_title="Quadra → FEC (ACD)", layout="wide")
st.title("Quadra ASCII → FEC (import ACD)")

FEC_COLS = [
    "JournalCode", "JournalLib", "EcritureNum", "EcritureDate",
    "CompteNum", "CompteLib", "CompAuxNum", "CompAuxLib",
    "PieceRef", "PieceDate", "EcritureLib",
    "Debit", "Credit", "EcritureLet", "DateLet", "ValidDate",
    "Montantdevise", "Idevise"
]

# =========================================================
# OUTILS
# =========================================================
def safe_decode(b: bytes) -> str:
    for enc in ("latin1", "cp1252", "utf-8"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", errors="replace")

def sfix(line: str, pos1: int, length: int) -> str:
    """Slice positions fixes (pos1 = 1-based)."""
    start = pos1 - 1
    end = start + length
    if start >= len(line):
        return ""
    return line[start:end]

def clean_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def ddmmyy_to_yyyymmdd(ddmmyy: str, pivot: int = 70) -> str:
    """
    Quadra: dates en JJMMAA.
    pivot=70 => 00-69 => 2000-2069 ; 70-99 => 1970-1999
    """
    ddmmyy = re.sub(r"\D", "", (ddmmyy or "").strip())
    if len(ddmmyy) != 6:
        return ""

    dd = int(ddmmyy[0:2])
    mm = int(ddmmyy[2:4])
    yy = int(ddmmyy[4:6])

    yyyy = (1900 + yy) if yy >= pivot else (2000 + yy)

    try:
        return datetime(yyyy, mm, dd).strftime("%Y%m%d")
    except ValueError:
        return ""

def signed_cents_to_amount_str(s: str) -> str:
    """
    Ex: '+000000001318' => 13.18
    Ex: '-000000001318' => -13.18
    """
    s = (s or "").strip()
    if not s:
        return "0.00"
    sign = -1 if s.startswith("-") else 1
    digits = re.sub(r"[^\d]", "", s)
    if not digits:
        return "0.00"
    return f"{sign * (int(digits) / 100):.2f}"

def sanitize_piece_ref(s: str) -> str:
    """Vire les caractères invisibles/bizarres et garde seulement les chiffres."""
    s = (s or "")
    s = s.replace("\x00", "").replace("\x1a", "").replace("\ufeff", "")
    s = re.sub(r"\D", "", s)
    return s.strip()

def nonempty(*vals) -> str:
    for v in vals:
        v = (v or "").strip()
        if v:
            return v
    return ""

def make_ecriture_num(journal: str, date_yyyymmdd: str, piece: str, seq: int) -> str:
    """
    Numéro d'écriture stable (FEC).
    On fait: JOURNAL + DATE + PIECE + compteur
    """
    j = (journal or "").strip()
    d = (date_yyyymmdd or "").strip()
    p = sanitize_piece_ref(piece)
    base = f"{j}{d}{p}"
    if not base:
        base = "ECR"
    return f"{base}-{seq}"

# =========================================================
# PARSING QUADRA (positions fixes)
# =========================================================
# NB : les exports peuvent varier selon paramétrage, mais on s’aligne sur le format ASCII "classique".

def parse_C(line: str):
    # C + Compte(8) à partir de pos2 + Libellé (30) à partir de pos10
    if not line.startswith("C"):
        return None
    compte = sfix(line, 2, 8).strip()
    lib = clean_spaces(sfix(line, 10, 30))
    if not compte:
        return None
    if not lib:
        lib = f"Compte {compte}"
    return compte, lib

def parse_M(line: str, pivot_year: int):
    if not line.startswith("M"):
        return None

    compte = sfix(line, 2, 8).strip()
    journal = sfix(line, 10, 2).strip()

    # Date écriture (JJMMAA) pos15 len6
    date_jjmmaa = sfix(line, 15, 6).strip()
    date_yyyymmdd = ddmmyy_to_yyyymmdd(date_jjmmaa, pivot=pivot_year)

    # Libellés (court 20 / long 30)
    lib20 = clean_spaces(sfix(line, 22, 20))
    lib30 = clean_spaces(sfix(line, 117, 30))
    ecriture_lib = nonempty(lib30, lib20)

    # Sens pos42 ; Montant signé pos43 len13
    sens = sfix(line, 42, 1).strip().upper()
    montant_signed = sfix(line, 43, 13)
    montant = signed_cents_to_amount_str(montant_signed)

    debit = "0.00"
    credit = "0.00"
    if sens == "D":
        debit = montant
    elif sens == "C":
        credit = montant

    # Échéance pos64 len6 (si remplie)
    ech_jjmmaa = sfix(line, 64, 6).strip()
    date_ech = ddmmyy_to_yyyymmdd(ech_jjmmaa, pivot=pivot_year)

    # Pièce: plusieurs champs possibles selon versions => on prend le plus "fort"
    piece5 = sfix(line, 75, 5).strip()
    piece8 = sfix(line, 100, 8).strip()
    piece10 = sfix(line, 149, 10).strip()
    piece20 = sfix(line, 232, 20).strip()
    piece_ref = sanitize_piece_ref(nonempty(piece20, piece10, piece8, piece5))

    # Devise pos108 len3 + montant devise pos169 len13
    devise = sfix(line, 108, 3).strip()
    mdev = sfix(line, 169, 13)

    idevise = devise if devise else ""
    montantdevise = ""
    if devise:
        mv = signed_cents_to_amount_str(mdev)
        if mv != "0.00":
            montantdevise = mv

    # Lettrage (version courte pos70 len2) : on stocke dans EcritureLet
    ecriture_let = sfix(line, 70, 2).strip()

    return {
        "_DateEcheance": date_ech,  # pas dans FEC standard, mais on peut l’afficher / injecter dans lib
        "JournalCode": journal,
        "JournalLib": journal,
        "EcritureNum": "",  # généré après
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
        "EcritureLet": ecriture_let,
        "DateLet": "",
        "ValidDate": "",     # IMPORTANT : vide => pas d’écriture validée à l’import
        "Montantdevise": montantdevise,
        "Idevise": idevise,
    }

# =========================================================
# UI
# =========================================================
c1, c2, c3 = st.columns(3)
with c1:
    sep_choice = st.selectbox("Séparateur FEC", ["TAB", "|"], index=0)
with c2:
    pivot = st.number_input("Pivot année (laisse 70)", min_value=0, max_value=99, value=70, step=1)
with c3:
    add_ech_in_lib = st.checkbox("Ajouter échéance dans libellé", value=True)

uploaded = st.file_uploader("Dépose ton fichier ASCII Quadra (TXT)", type=["txt", "asc", "dat"])

if not uploaded:
    st.info("Dépose un fichier pour générer le FEC.")
    st.stop()

raw = safe_decode(uploaded.read())
lines = [l.rstrip("\n\r") for l in raw.splitlines() if l.strip()]

# =========================================================
# PARSING
# =========================================================
plan = {}
m_rows = []

for line in lines:
    if line.startswith("C"):
        p = parse_C(line)
        if p:
            plan[p[0]] = p[1]
    elif line.startswith("M"):
        m = parse_M(line, pivot_year=int(pivot))
        if m:
            m_rows.append(m)

if not m_rows:
    st.error("Aucune ligne M trouvée / parseable.")
    st.stop()

df = pd.DataFrame(m_rows)

# Inject CompteLib depuis C
df["CompteLib"] = df["CompteNum"].map(plan).fillna(df["CompteNum"].map(lambda x: f"Compte {x}"))

# Ajouter échéance dans lib si souhaité (car pas de colonne FEC pour ça)
if add_ech_in_lib:
    def lib_plus_ech(row):
        ech = (row.get("_DateEcheance") or "").strip()
        if ech:
            base = row["EcritureLib"] or ""
            return f"{base} | ECH:{ech}" if base else f"ECH:{ech}"
        return row["EcritureLib"]
    df["EcritureLib"] = df.apply(lib_plus_ech, axis=1)

# Génère EcritureNum (obligatoire FEC)
seen = {}
nums = []
for _, r in df.iterrows():
    key = (r["JournalCode"], r["EcritureDate"], r["PieceRef"])
    seen[key] = seen.get(key, 0) + 1
    nums.append(make_ecriture_num(r["JournalCode"], r["EcritureDate"], r["PieceRef"], seen[key]))
df["EcritureNum"] = nums

df_fec = df[FEC_COLS].copy()

# =========================================================
# AFFICHAGE
# =========================================================
st.subheader("Aperçu FEC")
st.dataframe(df_fec.head(200), use_container_width=True)

st.write(f"Comptes C trouvés : **{len(plan)}**")
st.write(f"Lignes M converties : **{len(df_fec)}**")

# =========================================================
# EXPORT
# =========================================================
sep = "\t" if sep_choice == "TAB" else "|"
out_lines = [sep.join(FEC_COLS)]
for _, r in df_fec.iterrows():
    out_lines.append(sep.join("" if pd.isna(r[c]) else str(r[c]) for c in FEC_COLS))

fec_text = "\n".join(out_lines)

st.download_button(
    "⬇️ Télécharger le FEC",
    data=fec_text.encode("utf-8"),
    file_name="export_fec.txt",
    mime="text/plain"
)
