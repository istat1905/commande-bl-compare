import streamlit as st
import pdfplumber
import pandas as pd
import io
import re
from collections import defaultdict
from datetime import datetime, timedelta
import time
import base64
import hashlib

# --- CONFIGURATION DES UTILISATEURS (PERSISTANCE SIMPLE) ---
if 'users_db' not in st.session_state:
    st.session_state.users_db = {
        "admin": {"pass": "admin123", "role": "admin"},
        "user": {"pass": "user123", "role": "user"}
    }

if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False
if 'current_user' not in st.session_state:
    st.session_state.current_user = None
if 'user_role' not in st.session_state:
    st.session_state.user_role = None

# --- CONFIGURATION PAGE ---
st.set_page_config(
    page_title="DESATHOR",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- FONCTIONS D'AUTHENTIFICATION ---
def login_system():
    st.markdown("<br><br><h1 style='text-align: center; color: #1f77b4;'>🔒 DESATHOR - Connexion</h1>", unsafe_allow_html=True)
    c1, c2, c3 = st.columns([1, 2, 1])
    with c2:
        with st.form("login"):
            username = st.text_input("Utilisateur")
            password = st.text_input("Mot de passe", type="password")
            submit = st.form_submit_button("Se connecter", use_container_width=True)
            
            if submit:
                if username in st.session_state.users_db and st.session_state.users_db[username]["pass"] == password:
                    st.session_state.logged_in = True
                    st.session_state.current_user = username
                    st.session_state.user_role = st.session_state.users_db[username]["role"]
                    st.rerun()
                else:
                    st.error("Identifiant ou mot de passe incorrect.")

def admin_interface():
    st.markdown("---")
    st.subheader("🛠️ Gestion Utilisateurs (Admin)")
    
    tab1, tab2 = st.tabs(["Ajouter/Modifier", "Supprimer"])
    
    with tab1:
        with st.form("user_management"):
            new_user = st.text_input("Nom d'utilisateur")
            new_pass = st.text_input("Mot de passe", type="password")
            new_role = st.selectbox("Rôle", ["user", "admin"])
            submitted = st.form_submit_button("Enregistrer")
            
            if submitted and new_user and new_pass:
                st.session_state.users_db[new_user] = {"pass": new_pass, "role": new_role}
                st.success(f"Utilisateur {new_user} mis à jour/ajouté.")
    
    with tab2:
        user_to_del = st.selectbox("Utilisateur à supprimer", list(st.session_state.users_db.keys()))
        if st.button("🗑️ Supprimer"):
            if user_to_del == "admin":
                st.error("Impossible de supprimer l'admin principal.")
            else:
                del st.session_state.users_db[user_to_del]
                st.success(f"{user_to_del} supprimé.")
                st.rerun()

# --- FONCTIONS DE PARSING PDF & LOGIQUE METIER (INTACT) ---
try:
    import plotly.express as px
    import plotly.graph_objects as go
    PLOTLY_AVAILABLE = True
except ImportError:
    PLOTLY_AVAILABLE = False

if 'historique' not in st.session_state:
    st.session_state.historique = []
if "key_cmd" not in st.session_state:
    st.session_state.key_cmd = "cmd_1"
if "key_bl" not in st.session_state:
    st.session_state.key_bl = "bl_1"

def find_order_numbers_in_text(text):
    if not text: return []
    patterns = [r"Commande\s*n[°º]?\s*[:\s-]*?(\d{5,10})", r"N[°º]?\s*commande\s*[:\s-]*?(\d{5,10})", r"Bon\s+de\s+Livraison\s+Nr\.?\s*[:\s-]*?(\d{5,10})"]
    found = []
    for pat in patterns:
        for m in re.finditer(pat, text, flags=re.IGNORECASE):
            if m.group(1) not in found: found.append(m.group(1))
    return found

def is_valid_ean13(code):
    if not code or len(code) != 13: return False
    if code.startswith(('302', '376')): return False
    return True

def extract_records_from_command_pdf(pdf_file):
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
                for ligne in lines:
                    order_nums = find_order_numbers_in_text(ligne)
                    if order_nums: current_order = order_nums[0]
                    if re.search(r"^L\s+Réf\.\s*frn\s+Code\s+ean", ligne, re.IGNORECASE): in_data_section = True; continue
                    if re.search(r"^Récapitulatif|^Page\s+\d+", ligne, re.IGNORECASE): in_data_section = False; continue
                    if not in_data_section: continue
                    ean_matches = re.findall(r"\b(\d{13})\b", ligne)
                    valid_eans = [ean for ean in ean_matches if is_valid_ean13(ean)]
                    if not valid_eans: continue
                    ean = valid_eans[0]
                    qty_match = re.search(r"Conditionnement\s*:\s*\d+\s+\d+(\d+)\s+(\d+)", ligne)
                    qte = int(qty_match.group(1)) if qty_match else 0
                    if qte == 0:
                         nums = re.findall(r"\b(\d+)\b", ligne)
                         nums = [int(n) for n in nums if n != ean and len(str(n)) < 6]
                         if nums: qte = nums[-2] if len(nums) >= 2 else nums[-1]
                    records.append({"ref": ean, "code_article": "", "qte_commande": qte, "order_num": current_order or "__NO_ORDER__"})
    except: pass
    return {"records": records, "order_numbers": find_order_numbers_in_text(full_text), "full_text": full_text}

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
                    order_nums = find_order_numbers_in_text(ligne)
                    if order_nums: current_order = order_nums[0]
                    ean_matches = re.findall(r"\b(\d{13})\b", ligne)
                    valid_eans = [ean for ean in ean_matches if is_valid_ean13(ean)]
                    if not valid_eans: continue
                    ean = valid_eans[0]
                    nums = re.findall(r"[\d,.]+", ligne)
                    if nums:
                        candidate = nums[-2] if len(nums) >= 2 else nums[-1]
                        try: qte = float(candidate.replace(",", "."))
                        except: continue
                        records.append({"ref": ean, "qte_bl": qte, "order_num": current_order or "__NO_ORDER__"})
    except: pass
    return {"records": records, "order_numbers": find_order_numbers_in_text(full_text), "full_text": full_text}

def calculate_service_rate(qte_cmd, qte_bl):
    if pd.isna(qte_bl) or qte_cmd == 0: return 0
    return min((qte_bl / qte_cmd) * 100, 100)

# --- SIMULATION WEB (AUCHAN + EDI1) ---
def fetch_web_simulation(site_name, date_selected):
    # Simulation de données pour AUCHAN et EDI1
    date_str = date_selected.strftime("%d/%m/%Y")
    data = []
    
    if site_name == "EDI1":
        data = [
            {"Numéro": "46961161", "Client": "INTERMARCHÉ", "Livrer à": "ETABLISSEMENT DOLE", "Date": f"{date_str} 09:17", "Montant": 4085.29, "Statut": "Integré"},
            {"Numéro": "46962231", "Client": "INTERMARCHÉ", "Livrer à": "ITM LUXEMONT-ET-VILLOTTE", "Date": f"{date_str} 09:17", "Montant": 1229.78, "Statut": "Integré"},
            {"Numéro": "03879534", "Client": "DEPOT CSD ALBY", "Livrer à": "ENTREPOT CSD produits frais", "Date": f"{date_str} 09:21", "Montant": 0.02, "Statut": "Integré"},
            {"Numéro": "99999999", "Client": "AUTRE CLIENT", "Livrer à": "MAGASIN TEST", "Date": f"{date_str} 08:30", "Montant": 150.50, "Statut": "En erreur"}
        ]
    elif site_name == "AUCHAN":
        data = [
            {"Numéro": "AU-1001", "Client": "AUCHAN RETAIL", "Livrer à": "AUCHAN VELIZY", "Date": f"{date_str} 10:00", "Montant": 5430.00, "Statut": "Integré"},
            {"Numéro": "AU-1002", "Client": "AUCHAN RETAIL", "Livrer à": "AUCHAN LEERS", "Date": f"{date_str} 10:15", "Montant": 2100.50, "Statut": "En attente"},
            {"Numéro": "AU-1003", "Client": "AUCHAN RETAIL", "Livrer à": "AUCHAN RONCQ", "Date": f"{date_str} 11:00", "Montant": 890.00, "Statut": "Integré"}
        ]
        
    return pd.DataFrame(data)

# --- MAIN APPLICATION ---
def main_app():
    # HEADER & LOGO
    try:
        with open("Desathor.png", "rb") as f:
            encoded = base64.b64encode(f.read()).decode()
        st.markdown(f"""<div style="display: flex; flex-direction: column; align-items: center; margin-top: 10px;"><img src="data:image/png;base64,{encoded}" style="width:200px;"></div>""", unsafe_allow_html=True)
    except:
        st.markdown("# DESATHOR")

    st.markdown("""
    <style>
        .main-header { font-size: 2.5rem; font-weight: 700; color: #1f77b4; margin-bottom: 0.5rem; }
        .kpi-card { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 1.5rem; border-radius: 10px; color: white; text-align: center; }
        .kpi-value { font-size: 2.5rem; font-weight: bold; }
        .success-card { background: linear-gradient(135deg, #11998e 0%, #38ef7d 100%); }
        .warning-card { background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%); }
        .info-card { background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%); }
    </style>
    """, unsafe_allow_html=True)

    # --- SIDEBAR ---
    with st.sidebar:
        st.markdown(f"👤 **{st.session_state.current_user}** ({st.session_state.user_role})")
        if st.button("🚪 Déconnexion", use_container_width=True):
            st.session_state.logged_in = False
            st.rerun()
        st.markdown("---")
        
        if st.session_state.user_role == 'admin':
            admin_interface()
            st.markdown("---")

        st.header("📁 Fichiers")
        if st.button("🔄 Nouveau", use_container_width=True, type="primary"):
            st.session_state.key_cmd = f"cmd_{time.time()}"
            st.session_state.key_bl = f"bl_{time.time()}"
            st.session_state.historique = []
            st.rerun()
        commande_files = st.file_uploader("📦 PDF(s) Commande", type="pdf", accept_multiple_files=True, key=st.session_state.key_cmd)
        bl_files = st.file_uploader("📋 PDF(s) BL", type="pdf", accept_multiple_files=True, key=st.session_state.key_bl)
        
        st.markdown("---")
        st.header("⚙️ Options")
        hide_unmatched = st.checkbox("👁️‍🗨️ Masquer sans correspondance", value=True)
        
        st.markdown("---")
        st.header("📊 Historique")
        if st.session_state.historique:
            st.write(f"**{len(st.session_state.historique)}** comparaison(s)")
            if st.button("🗑️ Vider", use_container_width=True):
                st.session_state.historique = []
                st.rerun()
        else:
            st.info("Historique vide")

    # --- ONGLETS ---
    tab_compare, tab_edi = st.tabs(["🧾 Comparateur PDF", "🌐 Vérification DESADV"])

    # === ONGLET 1 : COMPARATEUR (CODE ORIGINAL) ===
    with tab_compare:
        st.markdown('<h1 class="main-header">🧾 Comparateur PDF</h1>', unsafe_allow_html=True)
        col_btn1, col_btn2 = st.columns([3, 1])
        with col_btn1:
            run_comparison = st.button("🔍 Lancer la comparaison", use_container_width=True, type="primary")
        with col_btn2:
            st.button("❓ Aide", use_container_width=True)

        if run_comparison:
            if not commande_files or not bl_files:
                st.error("⚠️ Veuillez téléverser les fichiers.")
            else:
                with st.spinner("🔄 Analyse..."):
                    commandes_dict = defaultdict(list)
                    for f in commande_files:
                        res = extract_records_from_command_pdf(f)
                        for rec in res["records"]: commandes_dict[rec["order_num"]].append(rec)
                    for k in commandes_dict.keys():
                        df = pd.DataFrame(commandes_dict[k])
                        df = df.groupby(["ref", "code_article"], as_index=False).agg({"qte_commande": "sum"})
                        commandes_dict[k] = df
                    bls_dict = defaultdict(list)
                    for f in bl_files:
                        res = extract_records_from_bl_pdf(f)
                        for rec in res["records"]: bls_dict[rec["order_num"]].append(rec)
                    for k in bls_dict.keys():
                        df = pd.DataFrame(bls_dict[k])
                        df = df.groupby("ref", as_index=False).agg({"qte_bl": "sum"})
                        bls_dict[k] = df
                    results = {}
                    for order_num, df_cmd in commandes_dict.items():
                        df_bl = bls_dict.get(order_num, pd.DataFrame(columns=["ref", "qte_bl"]))
                        merged = pd.merge(df_cmd, df_bl, on="ref", how="left")
                        merged["qte_commande"] = pd.to_numeric(merged["qte_commande"], errors="coerce").fillna(0)
                        merged["qte_bl"] = pd.to_numeric(merged.get("qte_bl", pd.Series()), errors="coerce").fillna(0)
                        merged["status"] = merged.apply(lambda r: "MISSING_IN_BL" if r["qte_bl"] == 0 else ("OK" if r["qte_commande"] == r["qte_bl"] else "QTY_DIFF"), axis=1)
                        merged["taux_service"] = merged.apply(lambda r: calculate_service_rate(r["qte_commande"], r["qte_bl"]), axis=1)
                        results[order_num] = merged
                    st.session_state.historique.append({"results": results, "hide_unmatched": hide_unmatched})

        # RESULTATS
        if st.session_state.historique:
            latest = st.session_state.historique[-1]
            results = latest["results"]
            
            total_cmd = total_livre = 0
            for df in results.values():
                total_cmd += df["qte_commande"].sum()
                total_livre += df["qte_bl"].sum()
            
            st.markdown("### 📊 Vue d'ensemble")
            c1, c2, c3 = st.columns(3)
            with c1: st.metric("Total Commandé", int(total_cmd))
            with c2: st.metric("Total Livré", int(total_livre))
            with c3: 
                taux = (total_livre / total_cmd * 100) if total_cmd > 0 else 0
                st.metric("Taux Service", f"{taux:.1f}%")

            st.markdown("### 📋 Détails par commande")
            for order, df in results.items():
                with st.expander(f"Commande {order}"):
                    def color(val):
                        return "background-color: #d4edda" if val == "OK" else ("background-color: #f8d7da" if val == "MISSING_IN_BL" else "background-color: #fff3cd")
                    st.dataframe(df.style.applymap(color, subset=["status"]), use_container_width=True)

            # EXPORT EXCEL
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
                for order, df in results.items():
                    df.to_excel(writer, sheet_name=f"C_{order}"[:31], index=False)
            st.download_button("📥 Télécharger Excel", data=output.getvalue(), file_name="Rapport.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    # === ONGLET 2 : VERIFICATION DESADV (CORRIGÉ) ===
    with tab_edi:
        st.markdown("## 🌐 Vérification DESADV")
        
        CLIENTS_VIP_EDI1 = [
            "ENTREPOT CSD produits frais",
            "ITM LUXEMONT-ET-VILLOTTE",
            "ETABLISSEMENT DOLE"
        ]
        
        col1, col2, col3 = st.columns(3)
        with col1:
            # SELECTION DU SITE (AUCHAN ET EDI1 DISPONIBLES)
            site_select = st.selectbox("Site Client", ["EDI1", "AUCHAN"])
        with col2:
            date_check = st.date_input("Date", datetime.now() - timedelta(days=1))
        with col3:
            st.write(""); st.write("")
            launch_edi = st.button("📥 Vérifier", type="primary", use_container_width=True)
            
        if launch_edi:
            df_edi = fetch_web_simulation(site_select, date_check)
            st.markdown(f"### Résultats pour **{site_select}** ({date_check.strftime('%d/%m/%Y')})")
            
            if site_select == "EDI1":
                # Logique spécifique pour EDI1 (Surlignage vert pour les VIP)
                st.info("ℹ️ EDI1 : Les clients surlignés en vert n'ont **pas de restriction de montant**.")
                def highlight_vip(row):
                    if row["Livrer à"] in CLIENTS_VIP_EDI1:
                        return ['background-color: #d4edda'] * len(row)
                    return [''] * len(row)
                st.dataframe(df_edi.style.apply(highlight_vip, axis=1), use_container_width=True)
            
            elif site_select == "AUCHAN":
                # Logique standard pour Auchan
                st.info("ℹ️ AUCHAN : Affichage standard des commandes.")
                st.dataframe(df_edi, use_container_width=True)

if __name__ == "__main__":
    if not st.session_state.logged_in:
        login_system()
    else:
        main_app()
