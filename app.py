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
st.write("Téléverse plusieurs PDF commandes et plusieurs PDF BL. L'outil va détecter les numéros de commande, matcher automatiquement et produire un Excel avec 1 onglet par commande.")

# --------------------------
# Helpers: détection n° commande
# --------------------------
def find_order_numbers_in_text(text):
    if not text:
        return []
    patterns = [
        r"Commande\s*n[°º]?\s*[:\s-]*?(\d{5,10})",
        r"N[°º]?\s*commande\s*[:\s-]*?(\d{5,10})",
        r"Bon\s+de\s+Livraison\s+Nr\.?\s*[:\s-]*?(\d{5,10})",
        r"N[°º]?\s*[:\s-]*?commande[:\s-]*?(\d{5,10})",
        r"\b(\d{6,10})\b"
    ]
    found = []
    for pat in patterns:
        for m in re.finditer(pat, text, flags=re.IGNORECASE):
            num = m.group(1)
            if num and num not in found:
                found.append(num)
    return found

# --------------------------
# Extraction functions
# --------------------------
def extract_records_from_command_pdf(pdf_file):
    orders = defaultdict(list)
    try:
        with pdfplumber.open(pdf_file) as pdf:
            full_text = ""
            for page in pdf.pages:
                txt = page.extract_text() or ""
                full_text += "\n" + txt
                lines = txt.split("\n")
                for ligne in lines:
                    parts = ligne.split()
                    if not parts:
                        continue
                    # EAN 13 si présent
                    ean_match = re.search(r"\b(\d{13})\b", ligne)
                    # Réf frn plausible
                    reffrn = parts[0] if re.match(r"^\d+$", parts[0]) and len(parts[0]) >= 5 else None
                    # code_article (2e colonne si existante)
                    code_article = parts[1] if len(parts) > 1 else ""
                    # quantité heuristique
                    nums = re.findall(r"\b\d+\b", ligne)
                    qte = int(nums[-2]) if len(nums) >= 2 else (int(nums[-1]) if nums else None)
                    # choisir la clé ref
                    if ean_match:
                        ref = ean_match.group(1)
                    elif reffrn:
                        ref = reffrn
                    else:
                        ref = None
                    # Filtrer les lignes fantômes
                    if ref and (re.match(r"^\d{13}$", ref) or (reffrn and len(reffrn) >= 5)) and qte is not None:
                        orders["__ALL__"].append({
                            "ref": ref,
                            "code_article": code_article,
                            "qte_commande": qte
                        })
        order_nums = find_order_numbers_in_text(full_text)
    except Exception as e:
        st.error(f"Erreur lecture PDF commande: {e}")
        return {}
    return {"records": orders.get("__ALL__", []), "full_text": full_text, "order_numbers": order_nums}

def extract_records_from_bl_pdf(pdf_file):
    records = []
    full_text = ""
    try:
        with pdfplumber.open(pdf_file) as pdf:
            for page in pdf.pages:
                txt = page.extract_text() or ""
                full_text += "\n" + txt
                lines = txt.split("\n")
                for ligne in lines:
                    m = re.search(r"\b(\d{13})\b", ligne)
                    if not m:
                        continue
                    ean = m.group(1)
                    nums = re.findall(r"[\d,.]+", ligne)
                    qte = None
                    if nums:
                        nums_clean = [n for n in nums if re.sub(r"[,.]", "", n) != ean]
                        if nums_clean:
                            try:
                                qte = float(nums_clean[-1].replace(",", "."))
                            except:
                                qte = None
                    if ean and qte is not None:
                        records.append({"ref": ean, "qte_bl": qte})
        order_nums = find_order_numbers_in_text(full_text)
    except Exception as e:
        st.error(f"Erreur lecture PDF BL: {e}")
        return {}
    return {"records": records, "full_text": full_text, "order_numbers": order_nums}

# --------------------------
# UI: Upload multiple files
# --------------------------
commande_files = st.file_uploader("📥 PDF(s) Commande client", type=["pdf"], accept_multiple_files=True)
bl_files = st.file_uploader("📥 PDF(s) Bon de livraison", type=["pdf"], accept_multiple_files=True)

if st.button("🔍 Lancer la comparaison"):
    if not commande_files or not bl_files:
        st.error("📁 Veuillez uploader les fichiers commandes et BL.")
        st.stop()

    commandes_dict = {}
    commandes_texts = {}
    fallback_counter = 0
    for f in commande_files:
        res = extract_records_from_command_pdf(f)
        recs = res.get("records", [])
        txt = res.get("full_text", "")
        order_nums = res.get("order_numbers", [])
        if not order_nums:
            fallback_counter += 1
            generated = f"NO_CMD_{Path(f.name).stem}_{fallback_counter}"
            order_nums = [generated]
        for on in order_nums:
            if on not in commandes_dict:
                commandes_dict[on] = []
                commandes_texts[on] = txt
            commandes_dict[on].extend(recs)

    # DataFrame par commande
    for on, recs in commandes_dict.items():
        if recs:
            df = pd.DataFrame(recs)
            df = df.groupby(["ref", "code_article"], as_index=False).agg({"qte_commande": "sum"})
        else:
            df = pd.DataFrame(columns=["ref", "code_article", "qte_commande"])
        commandes_dict[on] = df

    # BL
    bls_dict = {}
    fallback_counter = 0
    bl_records_no_order = []
    for f in bl_files:
        res = extract_records_from_bl_pdf(f)
        recs = res.get("records", [])
        txt = res.get("full_text", "")
        order_nums = res.get("order_numbers", [])
        if not order_nums:
            fallback_counter += 1
            generated = f"NO_BL_{Path(f.name).stem}_{fallback_counter}"
            order_nums = [generated]
            bl_records_no_order.append({"file": f.name, "generated_key": generated, "records": recs})
        for on in order_nums:
            if on not in bls_dict:
                bls_dict[on] = []
            bls_dict[on].extend(recs)

    for on, recs in bls_dict.items():
        if recs:
            df = pd.DataFrame(recs)
            df = df.groupby("ref", as_index=False).agg({"qte_bl": "sum"})
        else:
            df = pd.DataFrame(columns=["ref", "qte_bl"])
        bls_dict[on] = df

    # Matching
    results_per_order = {}
    unmatched_commands = []
    unmatched_bls = set(bls_dict.keys())

    for order_num, df_cmd in commandes_dict.items():
        df_bl = bls_dict.get(order_num, pd.DataFrame(columns=["ref", "qte_bl"]))
        unmatched_bls.discard(order_num)
        merged = pd.merge(df_cmd, df_bl, on="ref", how="left")
        merged["qte_commande"] = pd.to_numeric(merged["qte_commande"], errors="coerce").fillna(0).astype(float)
        merged["qte_bl"] = pd.to_numeric(merged.get("qte_bl", pd.Series()), errors="coerce")

        # Forcer texte pour éviter notation scientifique
        merged["ref"] = merged["ref"].astype(str)
        merged["code_article"] = merged["code_article"].astype(str)

        merged["status"] = merged.apply(
            lambda r: "MISSING_IN_BL" if pd.isna(r["qte_bl"]) else ("OK" if int(r["qte_commande"]) == int(r["qte_bl"]) else "QTY_DIFF"),
            axis=1
        )

        results_per_order[order_num] = {
            "merged": merged,
            "n_missing": (merged["status"]=="MISSING_IN_BL").sum(),
            "n_qtydiff": (merged["status"]=="QTY_DIFF").sum(),
            "n_ok": (merged["status"]=="OK").sum(),
            "bl_exists": not df_bl.empty
        }
        if df_bl.empty:
            unmatched_commands.append(order_num)

    bls_without_matching_command = [k for k in bls_dict.keys() if k not in commandes_dict.keys()]

    # -------------------------
    # Output
    # -------------------------
    st.subheader("📊 Résumé")
    st.write(f"- Commandes détectées : **{len(commandes_dict)}**")
    st.write(f"- BL détectés : **{len(bls_dict)}**")
    st.write(f"- Commandes sans BL : **{len(unmatched_commands)}**")
    st.write(f"- BL sans commande : **{len(bls_without_matching_command)}**")

    st.subheader("🔎 Détails par commande")
    for order_num, info in results_per_order.items():
        merged = info["merged"]
        with st.expander(f"Commande {order_num} — OK:{info['n_ok']} | QTY_DIFF:{info['n_qtydiff']} | MISSING:{info['n_missing']}"):
            st.write(f"**BL trouvé :** {'Oui' if info['bl_exists'] else 'Non'}")
            st.dataframe(merged[["ref", "code_article", "qte_commande", "qte_bl", "status"]].sort_values(by="status"))

    # Excel
    output = io.BytesIO()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"Differences_all_commands_{timestamp}.xlsx"
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        # Summary
        summary_rows = []
        for order_num, info in results_per_order.items():
            summary_rows.append({
                "order_num": order_num,
                "bl_found": info["bl_exists"],
                "n_ok": info["n_ok"],
                "n_qtydiff": info["n_qtydiff"],
                "n_missing": info["n_missing"]
            })
        pd.DataFrame(summary_rows).to_excel(writer, sheet_name="Summary", index=False)

        # Sheets par commande
        for order_num, info in results_per_order.items():
            df = info["merged"].copy()
            sheet_name = f"C_{order_num}"[:31]
            df.to_excel(writer, sheet_name=sheet_name, index=False)

    st.success("✅ Comparaison terminée — Télécharge le fichier Excel ci-dessous")
    st.download_button(
        label="📥 Télécharger le fichier Excel",
        data=output.getvalue(),
        file_name=filename,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
