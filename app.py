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
    """
    Retourne les numéros de commande détectés.
    On ne prend que les nombres associés à 'commande' ou 'Bon de Livraison'.
    """
    if not text:
        return []

    order_nums = set()
    for line in text.splitlines():
        line_lower = line.lower()
        # ne détecte que si "commande" ou "bon de livraison" dans la ligne
        if "commande" in line_lower or "bon de livraison" in line_lower:
            m = re.search(r"\b(\d{5,10})\b", line)
            if m:
                order_nums.add(m.group(1))
    return list(order_nums)

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
                    # récupérer "réf frn" et code_article
                    reffrn = parts[0] if re.match(r"^\d+$", parts[0]) else None
                    code_article = parts[1] if len(parts) > 1 else ""
                    # quantité: avant-dernier nombre si possible
                    nums = re.findall(r"\b\d+\b", ligne)
                    qte = int(nums[-2]) if len(nums) >= 2 else int(nums[-1]) if nums else None
                    # ref = EAN si trouvé sinon reffrn
                    ean_match = re.search(r"\b(\d{13})\b", ligne)
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
                    ean = None
                    m = re.search(r"\b(\d{13})\b", ligne)
                    if m:
                        ean = m.group(1)
                    nums = re.findall(r"[\d,.]+", ligne)
                    qte = None
                    if nums:
                        if m and len(nums) >= 2:
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

    for on, recs in list(commandes_dict.items()):
        if recs:
            df = pd.DataFrame(recs)
            df = df.groupby(["ref", "code_article"], as_index=False).agg({"qte_commande": "sum"})
        else:
            df = pd.DataFrame(columns=["ref", "code_article", "qte_commande"])
        commandes_dict[on] = df

    bls_dict = {}
    bl_texts = {}
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
                bl_texts[on] = txt
            bls_dict[on].extend(recs)

    for on, recs in list(bls_dict.items()):
        if recs:
            df = pd.DataFrame(recs)
            df = df.groupby("ref", as_index=False).agg({"qte_bl": "sum"})
        else:
            df = pd.DataFrame(columns=["ref", "qte_bl"])
        bls_dict[on] = df

    # -------------------------
    results_per_order = {}
    unmatched_commands = []
    unmatched_bls = set(bls_dict.keys())

    for order_num, df_cmd in commandes_dict.items():
        df_bl = bls_dict.get(order_num, pd.DataFrame(columns=["ref", "qte_bl"]))
        unmatched_bls.discard(order_num)
        merged = pd.merge(df_cmd, df_bl, on="ref", how="left")
        merged["qte_commande"] = pd.to_numeric(merged.get("qte_commande", 0), errors="coerce").fillna(0).astype(float)
        merged["qte_bl"] = pd.to_numeric(merged.get("qte_bl", float("nan")), errors="coerce")

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

    if bls_without_matching_command:
        st.markdown("---")
        st.subheader("📦 BL sans commande (aperçu)")
        for ob in bls_without_matching_command:
            df = bls_dict.get(ob, pd.DataFrame(columns=["ref", "qte_bl"]))
            st.write(f"- BL id: **{ob}** — lignes: {len(df)}")
            st.dataframe(df)

    # -------------------------
    output = io.BytesIO()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"Differences_all_commands_{timestamp}.xlsx"
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
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
        for order_num, info in results_per_order.items():
            merged = info["merged"].copy()
            sheet_name = f"C_{order_num}"[:31]
            cols = ["ref", "code_article", "qte_commande", "qte_bl", "status"]
            for c in cols:
                if c not in merged.columns:
                    merged[c] = ""
            merged = merged[cols]
            merged.to_excel(writer, sheet_name=sheet_name, index=False)

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
                df_unmatched_bls.to_excel(writer, sheet_name="BL_without_cmd", index=False)

    st.success("✅ Comparaison terminée — Télécharge le fichier Excel ci-dessous")
    st.download_button(
        label="📥 Télécharger le fichier Excel (1 onglet par commande)",
        data=output.getvalue(),
        file_name=filename,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    st.markdown("---")
    st.write("Astuce : si certaines références n'apparaissent pas, vérifie que les PDFs contiennent bien les EAN 13 ou des 'Réf' numériques ; l'outil privilégie l'EAN (13 chiffres) pour faire le matching.")
