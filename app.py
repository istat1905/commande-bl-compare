import streamlit as st
import pdfplumber
import pandas as pd
import io
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

st.set_page_config(
    page_title="Comparateur Commande vs BL",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.title("🧾 Comparateur Commande vs Bon de livraison")
st.markdown("""
Téléverse plusieurs PDF de **commandes** et plusieurs PDF de **bons de livraison**.  
L'outil détecte automatiquement les numéros de commande, fait le matching et produit un **Excel** avec 1 onglet par commande.
""")

# --------------------------
# Helpers
# --------------------------
def find_order_numbers_in_text(text):
    if not text:
        return []
    patterns = [
        r"Commande\s*n[°º]?\s*[:\s-]*?(\d{5,10})",
        r"N[°º]?\s*commande\s*[:\s-]*?(\d{5,10})",
        r"Bon\s+de\s+Livraison\s+Nr\.?\s*[:\s-]*?(\d{5,10})",
    ]
    found = []
    for pat in patterns:
        for m in re.finditer(pat, text, flags=re.IGNORECASE):
            num = m.group(1)
            if num and num not in found:
                found.append(num)
    return found

def is_address_or_metadata_line(ligne):
    """Détecte si une ligne contient une adresse ou des métadonnées à ignorer"""
    ligne_lower = ligne.lower()
    
    # Mots-clés d'adresse
    address_keywords = [
        r'\bavenue\b', r'\brue\b', r'\bquartier\b', r'\bchemin\b',
        r'\bboulevard\b', r'\bplace\b', r'\bvoie\b', r'\broute\b',
        r'\ballée\b', r'\bimpasse\b', r'\bcours\b'
    ]
    
    # Mots-clés de métadonnées
    metadata_keywords = [
        r'commandé par', r'fournisseur', r'livrer à', r'livraison',
        r'facturation', r'adresse de', r'transport', r'commercial',
        r'code client', r'date', r'intégration'
    ]
    
    all_keywords = address_keywords + metadata_keywords
    
    for pattern in all_keywords:
        if re.search(pattern, ligne_lower):
            return True
    
    return False

def is_likely_gln_or_postal(ligne, ean):
    """Détecte si l'EAN est probablement un GLN ou code postal mal détecté"""
    # Si la ligne contient des mots-clés de lieu/adresse autour de l'EAN
    if is_address_or_metadata_line(ligne):
        return True
    
    # Si c'est un code postal (5 chiffres isolé)
    if len(ean) == 5:
        return True
    
    # Si la ligne ne contient pas assez de nombres (pas de quantité)
    nums = re.findall(r'\b\d+\b', ligne)
    if len(nums) < 2:  # Il faut au moins une ref et une quantité en plus de l'EAN
        return True
    
    return False

def extract_records_from_command_pdf(pdf_file):
    records = []
    full_text = ""
    try:
        with pdfplumber.open(pdf_file) as pdf:
            current_order = None
            for page in pdf.pages:
                txt = page.extract_text() or ""
                full_text += "\n" + txt
                for ligne in txt.split("\n"):
                    # Ignorer les lignes d'adresse/métadonnées
                    if is_address_or_metadata_line(ligne):
                        continue

                    # Chercher les numéros de commande dans la ligne
                    order_nums = find_order_numbers_in_text(ligne)
                    if order_nums:
                        current_order = order_nums[0]

                    # Chercher l'EAN 13 chiffres
                    ean_match = re.search(r"\b(\d{13})\b", ligne)
                    if not ean_match:
                        continue

                    # Exclure les lignes dont la première "réf fournisseur" commence par 30201
                    parts = ligne.split()
                    if parts and re.match(r"^\d{2,10}$", parts[0]) and parts[0].startswith("30201"):
                        continue  # Ignorer cette ligne

                    ean = ean_match.group(1)

                    # Vérifier si c'est un GLN ou code postal
                    if is_likely_gln_or_postal(ligne, ean):
                        continue

                    # Extraire quantité
                    nums = re.findall(r"\b\d+\b", ligne)
                    if len(nums) < 2:
                        continue

                    qte = int(nums[-2]) if len(nums) >= 2 else int(nums[-1])

                    records.append({
                        "ref": ean,
                        "code_article": parts[1] if len(parts) > 1 else "",
                        "qte_commande": qte,
                        "order_num": current_order if current_order else "__NO_ORDER__"
                    })
    except Exception as e:
        st.error(f"Erreur lecture PDF commande: {e}")
        return {"records": [], "order_numbers": [], "full_text": ""}

    order_numbers = find_order_numbers_in_text(full_text)
    return {"records": records, "order_numbers": order_numbers, "full_text": full_text}


def extract_records_from_bl_pdf(pdf_file):
    records = []
    full_text = ""
    try:
        with pdfplumber.open(pdf_file) as pdf:
            current_order = None
            for page in pdf.pages:
                txt = page.extract_text() or ""
                full_text += "\n" + txt
                for ligne in txt.split("\n"):
                    # Ignorer les lignes d'adresse/métadonnées
                    if is_address_or_metadata_line(ligne):
                        continue
                    
                    order_nums = find_order_numbers_in_text(ligne)
                    if order_nums:
                        current_order = order_nums[0]

                    m = re.search(r"\b(\d{13})\b", ligne)
                    if not m:
                        continue
                    ean = m.group(1)
                    
                    # Vérifier si c'est un GLN ou code postal
                    if is_likely_gln_or_postal(ligne, ean):
                        continue
                    
                    nums = re.findall(r"[\d,.]+", ligne)
                    qte = None
                    if nums:
                        candidate = nums[-2] if len(nums) >= 2 else nums[-1]
                        try:
                            qte = float(candidate.replace(",", "."))
                        except:
                            continue
                    if qte is None:
                        continue
                    records.append({
                        "ref": ean,
                        "qte_bl": qte,
                        "order_num": current_order if current_order else "__NO_ORDER__"
                    })
    except Exception as e:
        st.error(f"Erreur lecture PDF BL: {e}")
        return {"records": [], "order_numbers": [], "full_text": ""}
    order_numbers = find_order_numbers_in_text(full_text)
    return {"records": records, "order_numbers": order_numbers, "full_text": full_text}

# --------------------------
# Upload
# --------------------------
with st.sidebar:
    st.header("📁 Téléversement")
    commande_files = st.file_uploader("PDF(s) Commande client", type="pdf", accept_multiple_files=True)
    bl_files = st.file_uploader("PDF(s) Bon de livraison", type="pdf", accept_multiple_files=True)
    st.markdown("---")
    opt_show_missing = st.checkbox("Afficher BL ou commande manquante", value=True)

# --------------------------
# Traitement
# --------------------------
if st.button("🔍 Lancer la comparaison"):
    if not commande_files or not bl_files:
        st.error("Veuillez téléverser à la fois des commandes et des BL.")
        st.stop()

    commandes_dict = defaultdict(list)
    for f in commande_files:
        res = extract_records_from_command_pdf(f)
        for rec in res["records"]:
            commandes_dict[rec["order_num"]].append(rec)
    for k in commandes_dict.keys():
        df = pd.DataFrame(commandes_dict[k])
        df = df.groupby(["ref","code_article"], as_index=False).agg({"qte_commande":"sum"})
        commandes_dict[k] = df

    bls_dict = defaultdict(list)
    for f in bl_files:
        res = extract_records_from_bl_pdf(f)
        for rec in res["records"]:
            bls_dict[rec["order_num"]].append(rec)
    for k in bls_dict.keys():
        df = pd.DataFrame(bls_dict[k])
        df = df.groupby("ref", as_index=False).agg({"qte_bl":"sum"})
        bls_dict[k] = df

    # --------------------------
    # Matching et statuts
    # --------------------------
    results = {}
    for order_num, df_cmd in commandes_dict.items():
        df_bl = bls_dict.get(order_num, pd.DataFrame(columns=["ref","qte_bl"]))
        merged = pd.merge(df_cmd, df_bl, on="ref", how="left")
        merged["qte_commande"] = pd.to_numeric(merged["qte_commande"], errors="coerce").fillna(0)
        merged["qte_bl"] = pd.to_numeric(merged.get("qte_bl", pd.Series()), errors="coerce")
        def status_row(r):
            if pd.isna(r["qte_bl"]): return "MISSING_IN_BL"
            return "OK" if r["qte_commande"]==r["qte_bl"] else "QTY_DIFF"
        merged["status"] = merged.apply(status_row, axis=1)
        results[order_num] = merged

    # --------------------------
    # Interface moderne: tabs et expander
    # --------------------------
    tabs = st.tabs(["Résumé", "Détails commandes", "BL sans commandes"])
    with tabs[0]:
        st.subheader("📊 Résumé")
        st.write(f"- Commandes détectées : {len(commandes_dict)}")
        st.write(f"- BL détectés : {len(bls_dict)}")
        missing = [k for k in commandes_dict.keys() if k not in bls_dict]
        if missing:
            st.warning(f"Commandes sans BL : {len(missing)}")
            for m in missing: st.write(f"- {m}")

    with tabs[1]:
        st.subheader("🔎 Détails par commande")
        for order_num, df in results.items():
            n_ok = (df["status"]=="OK").sum()
            n_diff = (df["status"]=="QTY_DIFF").sum()
            n_miss = (df["status"]=="MISSING_IN_BL").sum()
            with st.expander(f"Commande {order_num} — OK:{n_ok} | QTY_DIFF:{n_diff} | MISSING:{n_miss}"):
                # Colorer le status
                def color_status(val):
                    if val=="OK": return "background-color: #d4edda"
                    if val=="QTY_DIFF": return "background-color: #fff3cd"
                    if val=="MISSING_IN_BL": return "background-color: #f8d7da"
                    return ""
                st.dataframe(df.style.applymap(color_status, subset=["status"]))

    with tabs[2]:
        st.subheader("📦 BL sans commande")
        unmatched_bls = [k for k in bls_dict.keys() if k not in commandes_dict]
        for bl_id in unmatched_bls:
            df = bls_dict[bl_id]
            st.write(f"- BL id: {bl_id} — lignes: {len(df)}")
            st.dataframe(df)

    # --------------------------
    # Export Excel
    # --------------------------
    output = io.BytesIO()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"Differences_all_commands_{timestamp}.xlsx"
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        for order_num, df in results.items():
            sheet_name = f"C_{order_num}"[:31]
            df.to_excel(writer, sheet_name=sheet_name, index=False)
    st.success("✅ Comparaison terminée")
    st.download_button("📥 Télécharger Excel", data=output.getvalue(), file_name=filename,
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
