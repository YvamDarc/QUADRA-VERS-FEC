def parse_M(line: str, pivot_year: int):
    """
    Supporte 2 formats :
    - Format A (ancien) : dates ddmmyy dans la ligne
    - Format B (nouveau) : "... AC+000000001318 .... 00011 EUR ... 00011"
    """

    if not line.startswith("M") or len(line) < 20:
        return None

    compte = line[1:9].strip()
    journal = line[9:11].strip()
    ecr_num = line[11:20].strip()
    tail = line[20:]

    # 1) Ancre: sens + montant (C+.... ou D+....) -> c'est LE repère fiable
    m_amt = re.search(r"([CD])\+([0-9]{6,})", tail)
    if not m_amt:
        return None

    sens = m_amt.group(1)
    amt_raw = "+" + m_amt.group(2)
    amt = amount_cents_to_str(amt_raw)

    debit, credit = "0.00", "0.00"
    if sens == "D":
        debit = amt
    else:
        credit = amt

    left = tail[:m_amt.start()]      # texte avant C+/D+
    right = tail[m_amt.end():]       # texte après le montant

    # 2) Nettoyage du libellé avant montant
    # On vire un éventuel code 2 lettres juste avant C/D (ex: " AC" / " AD")
    left_clean = re.sub(r"\b[A-Z0-9]{2}\s*$", "", left).strip()
    left_clean = clean_spaces(left_clean)

    # 3) Date: si on trouve ddmmyy (format ancien), sinon vide
    m_date = re.search(r"\b(\d{6})\b", right)
    ecr_date = ddmmyy_to_yyyymmdd(m_date.group(1), pivot=pivot_year) if m_date else ""

    # 4) Format B: tente de récupérer PieceRef + devise + libellé
    # On cherche "NNNNN EUR ..." (les pièces chez toi sont sur 5 chiffres)
    piece_ref = ""
    idevise = ""
    lib_after = ""

    m_piece = re.search(r"\b(\d{3,10})\s+([A-Z]{3})\s+(.*)$", right.strip())
    if m_piece:
        piece_ref = m_piece.group(1)
        idevise = m_piece.group(2)
        lib_after = clean_spaces(m_piece.group(3))
        # souvent le lib finit par répéter la pièce : "... 00011"
        lib_after = re.sub(r"\s+\d{3,10}\s*$", "", lib_after).strip()

    # fallback si on n'a pas matché
    if not piece_ref:
        tokens = [t for t in re.split(r"\s+", right.strip()) if t]
        piece_ref = tokens[0] if tokens else ecr_num

    # 5) Tiers / auxiliaire : on peut prendre le début du left_clean comme CompAuxLib
    # (optionnel — si tu veux vraiment le tiers dans Aux, on affine ensuite)
    aux_num = ""
    aux_lib = ""

    # 6) Libellé d'écriture : priorise le lib après devise, sinon le texte avant montant
    ecr_lib = lib_after if lib_after else left_clean

    return {
        "JournalCode": journal,
        "JournalLib": journal,
        "EcritureNum": ecr_num,
        "EcritureDate": ecr_date,      # vide si pas de date dans le flux
        "CompteNum": compte,
        "CompteLib": "",               # injecté depuis les C
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
