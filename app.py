import streamlit as st
import pdfplumber
import pandas as pd
import io
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

st.set_page_config(page_title="Comparateur Commande vs BL", layout="wide")
st.title("🧾 Comparateur Commande vs Bon de livraison — Multi (1 Excel, 1 onglet/commande)")
st.write("Téléverse plusieurs PDF commandes (un PDF peut contenir plusieurs commandes) et plusieurs PDF BL. L'outil va détecter les numéros de commande, matcher automatiquement et produire un Excel avec 1 onglet par commande.")

# --------------------------
# Helpers: détection n° commande
# --------------------------
def find_order_numbers_in_text(text):
    """Retourne la liste des numéros de commande détectés dans le texte (unique)."""
    if not text:
        return []
    patterns = [
        r"Commande\s*n[°º]?\s*[:\s-]*?(\d{5,10})",   # Commande n°03128899
        r"N[°º]?\s*commande\s*[:\s-]*?(\d{5,10})",  # N° commande 03128899
        r"Bon\s+de\s+Livraison\s+Nr\.?\s*[:\s-]*?(\d{5,10})",
        r"N[°º]?\s*[:\s-]*?commande[:\s-]*?(\d{5,10})",
        r"\b(\d{6,10})\b"  # fallback: any 6-10 digit group (less strict)
    ]
    found = []
    for pat in patterns:
        for m in re.finditer(pat, text, flags=re.IGNORECASE):
            num = m.group(1)
            if num and num not in found:
                found.append(num)
    # Filter improbable numbers: keep ones that appeared with word "commande" first if any
    if any(re.search(r"commande", m, flags=re.IGNORECASE) for m in re.findall(r".{0,30}", text)):
        # not used; keep as is
        pass
    return found

# --------------------------
# Extraction functions
# --------------------------
def extract_records_from_command_pdf(pdf_file):
    """
    Prend un fichier PDF (streamlit UploadedFile or bytes-like) et retourne :
      - dict: order_number -> DataFrame(ref, code_article, qte_commande)
    Si aucun n° commande trouvé, génère un identifiant basé sur filename_index.
    On cherche d'abord des EAN (13 chiffres). Si pas d'EAN, on essaye de capturer la 'Réf. frn' (première colonne).
    """
    orders = defaultdict(list)
    try:
        with pdfplumber.open(pdf_file) as pdf:
            full_text = ""
            for page in pdf.pages:
                txt = page.extract_text() or ""
                full_text += "\n" + txt

                lines = txt.split("\n")
                for ligne in lines:
                    # on cherche un EAN 13 dans la ligne
                    ean_match = re.search(r"\b(\d{13})\b", ligne)
                    # On tente aussi de récupérer la "réf frn" si la ligne commence par un nombre
                    parts = ligne.split()
                    reffrn = None
                    if parts and re.match(r"^\d+$", parts[0]):
                        reffrn = parts[0]
                    # quantité : heuristique - nombre entier proche de la fin (avant 'EUR' ou 'Pcb' etc.)
                    qte = None
                    # recherche des nombres dans la ligne (entiers)
                    nums = re.findall(r"\b\d+\b", ligne)
                    if nums:
                        # souvent quantité est l'avant-dernier nombre; sinon dernier
                        if len(nums) >= 2:
                            qte = int(nums[-2])
                        else:
                            qte = int(nums[-1])
                    # code_article (deuxième colonne) si possible
                    code_article = parts[1] if len(parts) > 1 else ""

                    # choisir la clé 'ref' : EAN si trouvé sinon reffrn
                    if ean_match:
                        ref = ean_match.group(1)
                    elif reffrn:
                        ref = reffrn
                    else:
                        ref = None

                    if ref and qte is not None:
                        orders["__ALL__"].append({
                            "ref": ref,
                            "code_article": code_article,
                            "qte_commande": int(qte)
                        })
            # On récupère les numéros de commande trouvés
            order_nums = find_order_numbers_in_text(full_text)
            if not order_nums:
                # Si pas de n° commande trouvé, on donnera un identifiant unique plus tard
                order_nums = []
    except Exception as e:
        st.error(f"Erreur lecture PDF commande: {e}")
        return {}
    # Si on a pas de numéro, create one placeholder later (we'll handle outside)
    # For now group all extracted lines under key "__ALL__" and attach full_text for detecting order numbers
    return {"records": orders.get("__ALL__", []), "full_text": full_text, "order_numbers": order_nums}

def extract_records_from_bl_pdf(pdf_file):
    """
    Extrait lignes BL : retourne dict:
      { order_number(s) detected in pdf : DataFrame(ref, qte_bl) }
    Si aucun n° commande détecté, order_numbers list vide and records under '__ALL__'
    Les doublons de ref sont sommés.
    """
    records = []
    full_text = ""
    try:
        with pdfplumber.open(pdf_file) as pdf:
            for page in pdf.pages:
                txt = page.extract_text() or ""
                full_text += "\n" + txt
                lines = txt.split("\n")
                for ligne in lines:
                    # chercher EAN 13
                    ean = None
                    m = re.search(r"\b(\d{13})\b", ligne)
                    if m:
                        ean = m.group(1)
                    # trouver les nombres (quantités) en tant qu'entiers ou décimaux
                    nums = re.findall(r"[\d,.]+", ligne)
                    qte = None
                    if nums:
                        # heuristique : nombre juste avant le code-barres (ean) ou l'avant-dernier
                        if m and len(nums) >= 2:
                            # trouver which numeric token contains the ean? If ean in nums, remove
                            nums_clean = [n for n in nums if re.sub(r"[,.]", "", n) not in (m.group(1) if m else "")]
                            if nums_clean:
                                candidate = nums_clean[-1]
                                try:
                                    qte = float(candidate.replace(",", "."))
                                except:
                                    qte = None
                        elif len(nums) >= 2:
                            try:
                                qte = float(nums[-2].replace(",", "."))
                            except:
                                qte = None
                        else:
                            try:
                                qte = float(nums[-1].replace(",", "."))
                            except:
                                qte = None
                    if ean and qte is not None:
                        records.append({"ref": ean, "qte_bl": qte})
    except Exception as e:
        st.error(f"Erreur lecture PDF BL: {e}")
        return {}
    order_nums = find_order_numbers_in_text(full_text)
    return {"records": records, "full_text": full_text, "order_numbers": order_nums}

# --------------------------
# UI: Upload multiple files
# --------------------------
st.markdown("### 1) Téléverse les fichiers")
commande_files = st.file_uploader("📥 PDF(s) Commande client (plusieurs possibles)", type=["pdf"], accept_multiple_files=True)
bl_files = st.file_uploader("📥 PDF(s) Bon de livraison (plusieurs possibles)", type=["pdf"], accept_multiple_files=True)

# Options
st.markdown("### Options")
opt_auto_assign_missing = st.checkbox("Afficher BL ou commande manquante (alerte si un fichier sans match)", value=True)
st.markdown("---")

# --------------------------
# Traitement et matching
# --------------------------
if st.button("🔍 Lancer la comparaison"):
    if not commande_files:
        st.error("📁 Aucune commande uploadée.")
        st.stop()
    if not bl_files:
        st.error("📁 Aucun BL uploadé.")
        st.stop()

    # --- Extraire toutes les commandes ---
    commandes_dict = {}  # order_num -> df
    commandes_texts = {}  # order_num -> sample text (for debugging)
    # We'll also keep a fallback map when no order number found: use generated id based on filename index
    fallback_counter = 0
    for f in commande_files:
        res = extract_records_from_command_pdf(f)
        recs = res.get("records", [])
        txt = res.get("full_text", "")
        order_nums = res.get("order_numbers", [])
        if not order_nums:
            # assign a fallback ID: filename_index
            fallback_counter += 1
            generated = f"NO_CMD_{Path(f.name).stem}_{fallback_counter}"
            order_nums = [generated]
        # It's possible one PDF contains multiple order numbers; try to split records per order number
        # Heuristic: if multiple order numbers in PDF, we assign the whole set of records to each order number.
        for on in order_nums:
            if on not in commandes_dict:
                commandes_dict[on] = []
                commandes_texts[on] = txt
            # append all recs (we'll deduplicate later)
            commandes_dict[on].extend(recs)

    # Clean commandes_dict: create DataFrame per order and aggregate by ref summing qte_commande
    for on, recs in list(commandes_dict.items()):
        if recs:
            df = pd.DataFrame(recs)
            # If there are duplicates per ref, sum quantities (some commandes might list same ref twice)
            df = df.groupby(["ref", "code_article"], as_index=False).agg({"qte_commande": "sum"})
        else:
            # empty df with expected columns
            df = pd.DataFrame(columns=["ref", "code_article", "qte_commande"])
        commandes_dict[on] = df

    # --- Extraire tous les BL ---
    bls_dict = {}  # order_num -> df
    bl_texts = {}
    fallback_counter = 0
    bl_records_no_order = []  # records whose pdf had no order number
    for f in bl_files:
        res = extract_records_from_bl_pdf(f)
        recs = res.get("records", [])
        txt = res.get("full_text", "")
        order_nums = res.get("order_numbers", [])
        if not order_nums:
            # fallback id
            fallback_counter += 1
            generated = f"NO_BL_{Path(f.name).stem}_{fallback_counter}"
            # store under generated key but also keep note for "BL without commande" later
            order_nums = [generated]
            bl_records_no_order.append({"file": f.name, "generated_key": generated, "records": recs})
        for on in order_nums:
            if on not in bls_dict:
                bls_dict[on] = []
                bl_texts[on] = txt
            bls_dict[on].extend(recs)

    # Aggregate BL records per order: sum duplicates
    for on, recs in list(bls_dict.items()):
        if recs:
            df = pd.DataFrame(recs)
            df = df.groupby("ref", as_index=False).agg({"qte_bl": "sum"})
        else:
            df = pd.DataFrame(columns=["ref", "qte_bl"])
        bls_dict[on] = df

    # -------------------------
    # Matching: for each commande, find BL with same order number
    # -------------------------
    results_per_order = {}  # order -> dict with df_compare, stats
    unmatched_commands = []
    unmatched_bls = set(bls_dict.keys())  # we'll remove matched ones

    for order_num, df_cmd in commandes_dict.items():
        # find matching BL key: exact match on order number
        if order_num in bls_dict:
            df_bl = bls_dict[order_num]
            unmatched_bls.discard(order_num)
        else:
            # Not found: mark as missing
            df_bl = pd.DataFrame(columns=["ref", "qte_bl"])

        # Merge on 'ref'
        if df_cmd.empty:
            merged = pd.merge(df_cmd, df_bl, on="ref", how="left")
        else:
            merged = pd.merge(df_cmd, df_bl, on="ref", how="left")
        # ensure numeric types
        if "qte_commande" in merged.columns:
            merged["qte_commande"] = pd.to_numeric(merged["qte_commande"], errors="coerce").fillna(0).astype(float)
        else:
            merged["qte_commande"] = 0.0
        if "qte_bl" in merged.columns:
            merged["qte_bl"] = pd.to_numeric(merged["qte_bl"], errors="coerce").fillna(float("nan"))
        else:
            merged["qte_bl"] = float("nan")

        # Status determination
        def status_row(r):
            if pd.isna(r["qte_bl"]):
                return "MISSING_IN_BL"
            try:
                if int(r["qte_commande"]) != int(r["qte_bl"]):
                    return "QTY_DIFF"
                else:
                    return "OK"
            except:
                return "QTY_DIFF"

        merged["status"] = merged.apply(status_row, axis=1)

        # Stats
        n_missing = (merged["status"] == "MISSING_IN_BL").sum()
        n_qtydiff = (merged["status"] == "QTY_DIFF").sum()
        n_ok = (merged["status"] == "OK").sum()

        results_per_order[order_num] = {
            "merged": merged,
            "n_missing": int(n_missing),
            "n_qtydiff": int(n_qtydiff),
            "n_ok": int(n_ok),
            "bl_exists": (order_num in bls_dict and not bls_dict[order_num].empty)
        }

        if (order_num not in bls_dict) or (bls_dict.get(order_num) is None) or bls_dict.get(order_num).empty:
            unmatched_commands.append(order_num)

    # Any BL keys still unmatched are BLs that don't have a corresponding commande
    bls_without_matching_command = [k for k in bls_dict.keys() if k not in commandes_dict.keys()]

    # -------------------------
    # Output: affichage résumé & détails
    # -------------------------
    st.subheader("📊 Résumé")
    st.write(f"- Commandes détectées : **{len(commandes_dict)}**")
    st.write(f"- BL détectés : **{len(bls_dict)}**")
    st.write(f"- Commandes sans BL : **{len(unmatched_commands)}**")
    st.write(f"- BL sans commande : **{len(bls_without_matching_command)}**")

    if unmatched_commands:
        st.warning("⚠️ Commandes sans BL trouvé :")
        for oc in unmatched_commands:
            st.write(f"- Commande **{oc}** (aucun BL correspondant trouvé)")

    if bls_without_matching_command:
        st.warning("⚠️ BL sans commande correspondante :")
        for ob in bls_without_matching_command:
            st.write(f"- BL identifié par **{ob}**")

    st.markdown("---")
    st.subheader("🔎 Détails par commande")
    # Use expanders for each command
    for order_num, info in results_per_order.items():
        merged = info["merged"]
        with st.expander(f"Commande {order_num} — OK:{info['n_ok']} | QTY_DIFF:{info['n_qtydiff']} | MISSING:{info['n_missing']}"):
            st.write(f"**BL trouvé :** {'Oui' if info['bl_exists'] else 'Non'}")
            st.dataframe(merged[["ref", "code_article", "qte_commande", "qte_bl", "status"]].sort_values(by="status"))

    # Also show BLs that had no matching command and sample their content
    if bls_without_matching_command:
        st.markdown("---")
        st.subheader("📦 BL sans commande (aperçu)")
        for ob in bls_without_matching_command:
            df = bls_dict.get(ob, pd.DataFrame(columns=["ref", "qte_bl"]))
            st.write(f"- BL id: **{ob}** — lignes: {len(df)}")
            st.dataframe(df)

    # -------------------------
    # Générer UN SEUL Excel avec 1 onglet par commande
    # -------------------------
    output = io.BytesIO()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"Differences_all_commands_{timestamp}.xlsx"
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        # Summary sheet
        summary_rows = []
        for order_num, info in results_per_order.items():
            summary_rows.append({
                "order_num": order_num,
                "bl_found": info["bl_exists"],
                "n_ok": info["n_ok"],
                "n_qtydiff": info["n_qtydiff"],
                "n_missing": info["n_missing"]
            })
        summary_df = pd.DataFrame(summary_rows)
        summary_df.to_excel(writer, sheet_name="Summary", index=False)

        # add sheet per order with full merged table
        for order_num, info in results_per_order.items():
            merged = info["merged"].copy()
            # sanitize sheet name (max 31 chars)
            sheet_name = f"C_{order_num}"
            if len(sheet_name) > 31:
                sheet_name = sheet_name[:31]
            # ensure columns exist
            cols = ["ref", "code_article", "qte_commande", "qte_bl", "status"]
            for c in cols:
                if c not in merged.columns:
                    merged[c] = ""
            merged = merged[cols]
            merged.to_excel(writer, sheet_name=sheet_name, index=False)

        # If there are BLs without matching commands, add a sheet listing them
        if bls_without_matching_command:
            all_unmatched = []
            for ob in bls_without_matching_command:
                df = bls_dict.get(ob, pd.DataFrame(columns=["ref", "qte_bl"]))
                if not df.empty:
                    df2 = df.copy()
                    df2["bl_id"] = ob
                    all_unmatched.append(df2)
            if all_unmatched:
                df_unmatched_bls = pd.concat(all_unmatched, ignore_index=True)
                # shorten sheet name if needed
                sn = "BL_without_cmd"
                if len(sn) > 31:
                    sn = sn[:31]
                df_unmatched_bls.to_excel(writer, sheet_name=sn, index=False)

    st.success("✅ Comparaison terminée — Télécharge le fichier Excel ci-dessous")
    st.download_button(
        label="📥 Télécharger le fichier Excel (1 onglet par commande)",
        data=output.getvalue(),
        file_name=filename,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    # small debug/info area (optional)
    st.markdown("---")
    st.write("Astuce : si certaines références n'apparaissent pas, vérifie que les PDFs contiennent bien les EAN 13 ou des 'Réf' numériques ; l'outil privilégie l'EAN (13 chiffres) pour faire le matching.")

