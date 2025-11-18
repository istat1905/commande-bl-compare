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
st.write("Téléverse plusieurs PDF commandes et BL. L'outil va détecter les numéros de commande, matcher automatiquement et produire un Excel avec 1 onglet par commande.")

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
    orders = []
    full_text = ""
    try:
        with pdfplumber.open(pdf_file) as pdf:
            for page in pdf.pages:
                txt = page.extract_text() or ""
                full_text += "\n" + txt
                for ligne in txt.split("\n"):
                    if not ligne.strip():
                        continue
                    # EAN 13 pour ref
                    ean_match = re.search(r"\b(\d{13})\b", ligne)
                    ref = ean_match.group(1) if ean_match else None

                    # code_article : deuxième colonne si première colonne numérique (Réf frn)
                    parts = ligne.split()
                    code_article = None
                    if parts and re.match(r"^\d+$", parts[0]) and len(parts) > 1:
                        candidate = parts[1]
                        if re.match(r"^\d{1,6}$", candidate):
                            code_article = candidate

                    # qte_commande : avant-dernier nombre de la ligne
                    qte = None
                    if ref and parts:
                        nums = [int(n) for n in re.findall(r"\b\d+\b", ligne)]
                        if len(nums) >= 2:
                            qte = nums[-2]
                        elif nums:
                            qte = nums[-1]

                    if ref and code_article and qte is not None:
                        orders.append({
                            "ref": ref,
                            "code_article": code_article,
                            "qte_commande": qte
                        })
        order_nums = find_order_numbers_in_text(full_text)
    except Exception as e:
        st.error(f"Erreur lecture PDF commande: {e}")
        return {"records": [], "full_text": "", "order_numbers": []}
    return {"records": orders, "full_text": full_text, "order_numbers": order_nums}

def extract_records_from_bl_pdf(pdf_file):
    records = []
    full_text = ""
    try:
        with pdfplumber.open(pdf_file) as pdf:
            for page in pdf.pages:
                txt = page.extract_text() or ""
                full_text += "\n" + txt
                for ligne in txt.split("\n"):
                    if not ligne.strip():
                        continue
                    m = re.search(r"\b(\d{13})\b", ligne)
                    ean = m.group(1) if m else None
                    if not ean:
                        continue
                    # quantité avant le EAN ou avant-dernier nombre
                    nums = [n for n in re.findall(r"[\d,.]+", ligne)]
                    qte = None
                    if nums:
                        nums_clean = [n for n in nums if ean not in re.sub(r"[,.]", "", n)]
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
        return {"records": [], "full_text": "", "order_numbers": []}
    return {"records": records, "full_text": full_text, "order_numbers": order_nums}

# --------------------------
# UI: Upload multiple files
# --------------------------
commande_files = st.file_uploader("📥 PDF(s) Commande client", type=["pdf"], accept_multiple_files=True)
bl_files = st.file_uploader("📥 PDF(s) Bon de livraison", type=["pdf"], accept_multiple_files=True)

if st.button("🔍 Lancer la comparaison"):
    if not commande_files:
        st.error("📁 Aucune commande uploadée.")
        st.stop()
    if not bl_files:
        st.error("📁 Aucun BL uploadé.")
        st.stop()

    # --- Extraire toutes les commandes ---
    commandes_dict = {}
    fallback_counter = 0
    for f in commande_files:
        res = extract_records_from_command_pdf(f)
        recs = res.get("records", [])
        order_nums = res.get("order_numbers", [])
        if not order_nums:
            fallback_counter += 1
            order_nums = [f"NO_CMD_{Path(f.name).stem}_{fallback_counter}"]
        for on in order_nums:
            if on not in commandes_dict:
                commandes_dict[on] = []
            commandes_dict[on].extend(recs)

    # Agréger par ref/code_article
    for on, recs in list(commandes_dict.items()):
        if recs:
            df = pd.DataFrame(recs)
            df = df.groupby(["ref", "code_article"], as_index=False).agg({"qte_commande": "sum"})
        else:
            df = pd.DataFrame(columns=["ref", "code_article", "qte_commande"])
        commandes_dict[on] = df

    # --- Extraire tous les BL ---
    bls_dict = {}
    fallback_counter = 0
    bl_records_no_order = []
    for f in bl_files:
        res = extract_records_from_bl_pdf(f)
        recs = res.get("records", [])
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

    # Agréger BL par ref
    for on, recs in list(bls_dict.items()):
        if recs:
            df = pd.DataFrame(recs)
            df = df.groupby("ref", as_index=False).agg({"qte_bl": "sum"})
        else:
            df = pd.DataFrame(columns=["ref", "qte_bl"])
        bls_dict[on] = df

    # -------------------------
    # Matching
    # -------------------------
    results_per_order = {}
    unmatched_commands = []
    unmatched_bls = set(bls_dict.keys())

    for order_num, df_cmd in commandes_dict.items():
        df_bl = bls_dict.get(order_num, pd.DataFrame(columns=["ref", "qte_bl"]))
        if order_num in bls_dict:
            unmatched_bls.discard(order_num)

        merged = pd.merge(df_cmd, df_bl, on="ref", how="left")
        merged["qte_commande"] = pd.to_numeric(merged["qte_commande"], errors="coerce").fillna(0).astype(float)
        merged["qte_bl"] = pd.to_numeric(merged["qte_bl"], errors="coerce").fillna(float("nan"))

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

        n_missing = (merged["status"] == "MISSING_IN_BL").sum()
        n_qtydiff = (merged["status"] == "QTY_DIFF").sum()
        n_ok = (merged["status"] == "OK").sum()

        results_per_order[order_num] = {
            "merged": merged,
            "n_missing": int(n_missing),
            "n_qtydiff": int(n_qtydiff),
            "n_ok": int(n_ok),
            "bl_exists": not df_bl.empty
        }

        if df_bl.empty:
            unmatched_commands.append(order_num)

    bls_without_matching_command = [k for k in bls_dict.keys() if k not in commandes_dict.keys()]

    # -------------------------
    # Affichage
    # -------------------------
    st.subheader("📊 Résumé")
    st.write(f"- Commandes détectées : **{len(commandes_dict)}**")
    st.write(f"- BL détectés : **{len(bls_dict)}**")
    st.write(f"- Commandes sans BL : **{len(unmatched_commands)}**")
    st.write(f"- BL sans commande : **{len(bls_without_matching_command)}**")

    if unmatched_commands:
        st.warning("⚠️ Commandes sans BL trouvé :")
        for oc in unmatched_commands:
            st.write(f"- Commande **{oc}**")

    if bls_without_matching_command:
        st.warning("⚠️ BL sans commande correspondante :")
        for ob in bls_without_matching_command:
            st.write(f"- BL identifié par **{ob}**")

    st.markdown("---")
    st.subheader("🔎 Détails par commande")
    for order_num, info in results_per_order.items():
        merged = info["merged"]
        with st.expander(f"Commande {order_num} — OK:{info['n_ok']} | QTY_DIFF:{info['n_qtydiff']} | MISSING:{info['n_missing']}"):
            st.write(f"**BL trouvé :** {'Oui' if info['bl_exists'] else 'Non'}")
            st.dataframe(merged[["ref", "code_article", "qte_commande", "qte_bl", "status"]].sort_values(by="status"))

    # -------------------------
    # Générer Excel
    # -------------------------
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

        # Sheet par commande
        for order_num, info in results_per_order.items():
            merged = info["merged"][["ref", "code_article", "qte_commande", "qte_bl", "status"]]
            sheet_name = f"C_{order_num}"[:31]
            merged.to_excel(writer, sheet_name=sheet_name, index=False)

        # BL sans commande
        if bls_without_matching_command:
            all_unmatched = []
            for ob in bls_without_matching_command:
                df = bls_dict.get(ob, pd.DataFrame(columns=["ref", "qte_bl"]))
                if not df.empty:
                    df2 = df.copy()
                    df2["bl_id"] = ob
                    all_unmatched.append(df2)
            if all_unmatched:
                pd.concat(all_unmatched, ignore_index=True).to_excel(writer, sheet_name="BL_without_cmd", index=False)

    st.success("✅ Comparaison terminée — Télécharge le fichier Excel ci-dessous")
    st.download_button(
        label="📥 Télécharger le fichier Excel (1 onglet par commande)",
        data=output.getvalue(),
        file_name=filename,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    st.markdown("---")
    st.write("Astuce : seules les lignes avec EAN 13 et code_article (4-6 chiffres) sont prises en compte pour éviter de confondre adresses ou autres nombres.")
