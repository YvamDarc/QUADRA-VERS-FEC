import streamlit as st

st.set_page_config(page_title="Quadra -> FEC (ACD)", layout="wide")
st.title("Quadra -> FEC (ACD)")
st.caption("Mode debug : si ça casse, l'erreur s'affiche ci-dessous.")

try:
    import re
    from datetime import datetime
    import pandas as pd

    FEC_COLS_18 = [
        "JournalCode", "JournalLib", "EcritureNum", "EcritureDate",
        "CompteNum", "CompteLib", "CompAuxNum", "CompAuxLib",
        "PieceRef", "PieceDate", "EcritureLib",
        "Debit", "Credit", "EcritureLet", "DateLet", "ValidDate",
        "Montantdevise", "Idevise"
    ]

    def parse_amount_cents(raw: str) -> str:
        raw = (raw or "").strip()
        if not raw:
            return "0.00"
        sign = -1 if raw.startswith("-") else 1
        digits = re.sub(r"[^\d]", "", raw)
        if not digits:
            return "0.00"
        val = sign * (int(digits) / 100.0)
        return f"{val:.2f}"

    def parse_ddmmyy(s: str, pivot_year: int = 70):
        s = (s or "").strip()
        m = re.match(r"^(\d{2})(\d{2})(\d{2})$", s)
        if not m:
            return ""
        dd, mm, yy = map(int, m.groups())
        yyyy = 1900 + yy if yy >= pivot_year else 2000 + yy
        try:
            d = datetime(yyyy, mm, dd)
            return d.strftime("%Y%m%d")
        except ValueError:
            return ""

    def clean_text(x: str) -> str:
        return re.sub(r"\s+", " ", (x or "").strip())

    def parse_C_line(line: str):
        if not line.startswith("C"):
            return None
        m = re.match(r"^C(\d{8})(.*)$", line)
        if not m:
            return None
        compte = m.group(1)
        rest = m.group(2)

        cut = re.split(r"[A-Z0-9]{5,10}\+[-+]?0{3,}\d+", rest, maxsplit=1)
        lib = clean_text(cut[0] if cut else rest)
        if not lib:
            lib = f"Compte {compte}"
        return compte, lib

    def parse_M_line(line: str, pivot_year: int):
        if not line.startswith("M") or len(line) < 20:
            return None

        compte = line[1:9].strip()
        journal = line[9:11].strip()
        ecriture_num = line[11:20].strip()
        tail = line[20:]

        comp_aux_num = ""
        comp_aux_lib = ""
        m_aux = re.search(r"\bT([A-Z0-9]{2,20})\b", tail)
        if m_aux:
            comp_aux_num = m_aux.group(1)
            comp_aux_lib = comp_aux_num

        m_amt = re.search(r"\b([CD])\s*([+-]\d{10,})\b", tail) or re.search(r"\b([CD])([+-]\d{10,})\b", tail)
        if not m_amt:
            return None

        sens = m_amt.group(1)
        amt_raw = m_amt.group(2)
        amount = parse_amount_cents(amt_raw)

        debit = "0.00"
        credit = "0.00"
        if sens == "D":
            debit = amount
        el
