import streamlit as st
import pdfplumber
import pandas as pd
import io
import re
from collections import defaultdict
from datetime import datetime

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
    """Extraction améliorée des numéros de commande"""
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

def is_valid_ean13(code):
    """Vérifie si un code est un EAN13 valide"""
    if not code or len(code) != 13:
        return False
    # Éviter les codes GLN (commencent souvent par 302, 376, etc.)
    if code.startswith(('302', '376')):
        return False
    return True

def extract_records_from_command_pdf(pdf_file):
    """Extraction améliorée des données de commande"""
    records = []
    full_text = ""
    
    try:
        with pdfplumber.open(pdf_file) as pdf:
            current_order = None
            in_data_section = False
            
            for page in pdf.pages:
                txt = page.extract_text() or ""
                full_text += "\n" + txt
                
                lines = txt.split("\n")
                
                for i, ligne in enumerate(lines):
                    # Détection du numéro de commande
                    order_nums = find_order_numbers_in_text(ligne)
                    if order_nums:
                        current_order = order_nums[0]
                    
                    # Détection du début de la section de données
                    if re.search(r"^L\s+Réf\.\s*frn\s+Code\s+ean", ligne, re.IGNORECASE):
                        in_data_section = True
                        continue
                    
                    # Fin de la section de données
                    if re.search(r"^Récapitulatif|^Page\s+\d+", ligne, re.IGNORECASE):
                        in_data_section = False
                        continue
                    
                    # Traiter uniquement les lignes dans la section de données
                    if not in_data_section:
                        continue
                    
                    # Extraction des EAN13 (en excluant les GLN)
                    ean_matches = re.findall(r"\b(\d{13})\b", ligne)
                    valid_eans = [ean for ean in ean_matches if is_valid_ean13(ean)]
                    
                    if not valid_eans:
                        continue
                    
                    # Utiliser le premier EAN valide trouvé
                    ean = valid_eans[0]
                    
                    # Extraction des autres informations
                    parts = ligne.split()
                    
                    # Trouver la position de l'EAN dans la ligne
                    ean_pos = None
                    for idx, part in enumerate(parts):
                        if ean in part:
                            ean_pos = idx
                            break
                    
                    # Ref fournisseur : c'est le nombre AVANT l'EAN (pas le premier qui est la ligne)
                    ref_frn = None
                    code_article = ""
                    
                    if ean_pos and ean_pos > 1:
                        # Le code article est juste avant l'EAN
                        candidate = parts[ean_pos - 1]
                        # Vérifier que c'est bien un code article (4-6 chiffres, pas un numéro de ligne 1-2 chiffres)
                        if re.match(r"^\d{3,6}$", candidate):
                            code_article = candidate
                            ref_frn = candidate
                    
                    # Extraction de la quantité commandée
                    # Chercher "Conditionnement : X" suivi de la quantité
                    qty_match = re.search(r"Conditionnement\s*:\s*\d+\s+\d+(\d+)\s+(\d+)", ligne)
                    if qty_match:
                        qte = int(qty_match.group(1))
                    else:
                        # Fallback: prendre les nombres et essayer de trouver la quantité
                        nums = re.findall(r"\b(\d+)\b", ligne)
                        nums = [int(n) for n in nums if n != ean and len(n) < 6]
                        if nums:
                            # La quantité est souvent l'avant-dernier nombre
                            qte = nums[-2] if len(nums) >= 2 else nums[-1]
                        else:
                            continue
                    
                    records.append({
                        "ref": ean,
                        "code_article": code_article,
                        "qte_commande": qte,
                        "order_num": current_order if current_order else "__NO_ORDER__"
                    })
                    
    except Exception as e:
        st.error(f"Erreur lecture PDF commande: {e}")
        return {"records": [], "order_numbers": [], "full_text": ""}
    
    order_numbers = find_order_numbers_in_text(full_text)
    return {"records": records, "order_numbers": order_numbers, "full_text": full_text}

def extract_records_from_bl_pdf(pdf_file):
    """Extraction des données de bon de livraison"""
    records = []
    full_text = ""
    
    try:
        with pdfplumber.open(pdf_file) as pdf:
            current_order = None
            
            for page in pdf.pages:
                txt = page.extract_text() or ""
                full_text += "\n" + txt
                
                for ligne in txt.split("\n"):
                    # Détection du numéro de commande
                    order_nums = find_order_numbers_in_text(ligne)
                    if order_nums:
                        current_order = order_nums[0]
                    
                    # Extraction EAN13 valides uniquement
                    ean_matches = re.findall(r"\b(\d{13})\b", ligne)
                    valid_eans = [ean for ean in ean_matches if is_valid_ean13(ean)]
                    
                    if not valid_eans:
                        continue
                    
                    ean = valid_eans[0]
                    
                    # Extraction quantité
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
    opt_show_debug = st.checkbox("Afficher les données extraites (debug)", value=False)

# --------------------------
# Traitement
# --------------------------
if st.button("🔍 Lancer la comparaison"):
    if not commande_files or not bl_files:
        st.error("Veuillez téléverser à la fois des commandes et des BL.")
        st.stop()

    # Extraction des commandes
    commandes_dict = defaultdict(list)
    all_command_records = []
    
    with st.spinner("Extraction des commandes..."):
        for f in commande_files:
            res = extract_records_from_command_pdf(f)
            all_command_records.extend(res["records"])
            for rec in res["records"]:
                commandes_dict[rec["order_num"]].append(rec)
    
    # Debug: afficher les données extraites
    if opt_show_debug and all_command_records:
        st.subheader("🔍 Debug - Données extraites des commandes")
        st.dataframe(pd.DataFrame(all_command_records))
    
    # Agrégation des commandes
    for k in commandes_dict.keys():
        df = pd.DataFrame(commandes_dict[k])
        df = df.groupby(["ref", "code_article"], as_index=False).agg({"qte_commande": "sum"})
        commandes_dict[k] = df

    # Extraction des BL
    bls_dict = defaultdict(list)
    all_bl_records = []
    
    with st.spinner("Extraction des bons de livraison..."):
        for f in bl_files:
            res = extract_records_from_bl_pdf(f)
            all_bl_records.extend(res["records"])
            for rec in res["records"]:
                bls_dict[rec["order_num"]].append(rec)
    
    # Debug: afficher les données extraites
    if opt_show_debug and all_bl_records:
        st.subheader("🔍 Debug - Données extraites des BL")
        st.dataframe(pd.DataFrame(all_bl_records))
    
    # Agrégation des BL
    for k in bls_dict.keys():
        df = pd.DataFrame(bls_dict[k])
        df = df.groupby("ref", as_index=False).agg({"qte_bl": "sum"})
        bls_dict[k] = df

    # --------------------------
    # Matching et statuts
    # --------------------------
    results = {}
    for order_num, df_cmd in commandes_dict.items():
        df_bl = bls_dict.get(order_num, pd.DataFrame(columns=["ref", "qte_bl"]))
        merged = pd.merge(df_cmd, df_bl, on="ref", how="left")
        
        merged["qte_commande"] = pd.to_numeric(merged["qte_commande"], errors="coerce").fillna(0)
        merged["qte_bl"] = pd.to_numeric(merged.get("qte_bl", pd.Series()), errors="coerce")
        
        def status_row(r):
            if pd.isna(r["qte_bl"]):
                return "MISSING_IN_BL"
            return "OK" if r["qte_commande"] == r["qte_bl"] else "QTY_DIFF"
        
        merged["status"] = merged.apply(status_row, axis=1)
        results[order_num] = merged

    # --------------------------
    # Interface
    # --------------------------
    tabs = st.tabs(["Résumé", "Détails commandes", "BL sans commandes"])
    
    with tabs[0]:
        st.subheader("📊 Résumé")
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Commandes détectées", len(commandes_dict))
        with col2:
            st.metric("BL détectés", len(bls_dict))
        with col3:
            missing = [k for k in commandes_dict.keys() if k not in bls_dict]
            st.metric("Commandes sans BL", len(missing))
        
        if missing:
            st.warning("Commandes sans BL correspondant :")
            for m in missing:
                st.write(f"- {m}")

    with tabs[1]:
        st.subheader("🔎 Détails par commande")
        for order_num, df in results.items():
            n_ok = (df["status"] == "OK").sum()
            n_diff = (df["status"] == "QTY_DIFF").sum()
            n_miss = (df["status"] == "MISSING_IN_BL").sum()
            
            with st.expander(f"Commande {order_num} — ✅ OK:{n_ok} | ⚠️ QTY_DIFF:{n_diff} | ❌ MISSING:{n_miss}"):
                def color_status(val):
                    if val == "OK":
                        return "background-color: #d4edda"
                    if val == "QTY_DIFF":
                        return "background-color: #fff3cd"
                    if val == "MISSING_IN_BL":
                        return "background-color: #f8d7da"
                    return ""
                
                st.dataframe(
                    df.style.applymap(color_status, subset=["status"]),
                    use_container_width=True
                )

    with tabs[2]:
        st.subheader("📦 BL sans commande")
        unmatched_bls = [k for k in bls_dict.keys() if k not in commandes_dict]
        
        if unmatched_bls:
            for bl_id in unmatched_bls:
                df = bls_dict[bl_id]
                with st.expander(f"BL {bl_id} — {len(df)} lignes"):
                    st.dataframe(df, use_container_width=True)
        else:
            st.success("Tous les BL ont une commande correspondante.")

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
            
            # Formatage Excel
            workbook = writer.book
            worksheet = writer.sheets[sheet_name]
            
            # Formats
            ok_format = workbook.add_format({'bg_color': '#d4edda'})
            diff_format = workbook.add_format({'bg_color': '#fff3cd'})
            miss_format = workbook.add_format({'bg_color': '#f8d7da'})
            
            # Appliquer les formats
            for idx, row in df.iterrows():
                if row['status'] == 'OK':
                    worksheet.set_row(idx + 1, None, ok_format)
                elif row['status'] == 'QTY_DIFF':
                    worksheet.set_row(idx + 1, None, diff_format)
                elif row['status'] == 'MISSING_IN_BL':
                    worksheet.set_row(idx + 1, None, miss_format)
    
    st.success("✅ Comparaison terminée")
    st.download_button(
        "📥 Télécharger Excel",
        data=output.getvalue(),
        file_name=filename,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
