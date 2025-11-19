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

# --- 1. CONFIGURATION & AUTHENTIFICATION ---

st.set_page_config(page_title="DESATHOR", layout="wide", initial_sidebar_state="expanded")

# CSS Personnalisé (Vos styles originaux + ajustements boutons)
st.markdown("""
<style>
    .main-header { font-size: 2.5rem; font-weight: 700; color: #1f77b4; margin-bottom: 0.5rem; }
    .subtitle { font-size: 1.1rem; color: #666; margin-bottom: 2rem; }
    .kpi-card { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 1.5rem; border-radius: 10px; color: white; text-align: center; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
    .kpi-value { font-size: 2.5rem; font-weight: bold; margin: 0.5rem 0; }
    .kpi-label { font-size: 0.9rem; opacity: 0.9; }
    .success-card { background: linear-gradient(135deg, #11998e 0%, #38ef7d 100%); }
    .warning-card { background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%); }
    .info-card { background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%); }
    /* Style des boutons pour les aligner */
    .stButton>button { width: 100%; border-radius: 8px; height: 3em; }
</style>
""", unsafe_allow_html=True)

# Gestion des utilisateurs (Simulation BDD)
if 'users_db' not in st.session_state:
    st.session_state.users_db = {
        "admin": {"pass": "admin123", "role": "admin"},
        "user": {"pass": "user123", "role": "user"}
    }

if 'logged_in' not in st.session_state: st.session_state.logged_in = False
if 'current_user' not in st.session_state: st.session_state.current_user = None
if 'user_role' not in st.session_state: st.session_state.user_role = None

def login_page():
    st.markdown("<br><br><h1 style='text-align: center; color: #1f77b4;'>🔒 DESATHOR - Connexion</h1>", unsafe_allow_html=True)
    c1, c2, c3 = st.columns([1, 2, 1])
    with c2:
        with st.form("login_form"):
            username = st.text_input("Utilisateur")
            password = st.text_input("Mot de passe", type="password")
            if st.form_submit_button("Se connecter"):
                if username in st.session_state.users_db and st.session_state.users_db[username]["pass"] == password:
                    st.session_state.logged_in = True
                    st.session_state.current_user = username
                    st.session_state.user_role = st.session_state.users_db[username]["role"]
                    st.rerun()
                else:
                    st.error("Identifiants incorrects")

def admin_panel():
    st.markdown("---")
    st.subheader("🛠️ Admin : Gestion Utilisateurs")
    tab1, tab2 = st.tabs(["Ajouter/Modifier", "Supprimer"])
    with tab1:
        with st.form("add_user"):
            new_u = st.text_input("Identifiant")
            new_p = st.text_input("Mot de passe", type="password")
            new_r = st.selectbox("Rôle", ["user", "admin"])
            if st.form_submit_button("Enregistrer"):
                st.session_state.users_db[new_u] = {"pass": new_p, "role": new_r}
                st.success(f"Utilisateur {new_u} mis à jour.")
    with tab2:
        u_del = st.selectbox("Supprimer", list(st.session_state.users_db.keys()))
        if st.button("🗑️ Confirmer la suppression"):
            if u_del != "admin":
                del st.session_state.users_db[u_del]
                st.success("Supprimé.")
                st.rerun()
            else: st.error("Impossible de supprimer l'admin principal.")

# --- 2. MOTEUR DE COMPARAISON PDF (VOTRE CODE EXACT) ---

try:
    import plotly.express as px
    import plotly.graph_objects as go
    PLOTLY_AVAILABLE = True
except ImportError:
    PLOTLY_AVAILABLE = False

if 'historique' not in st.session_state: st.session_state.historique = []
if "key_cmd" not in st.session_state: st.session_state.key_cmd = "cmd_1"
if "key_bl" not in st.session_state: st.session_state.key_bl = "bl_1"

def find_order_numbers_in_text(text):
    if not text: return []
    patterns = [r"Commande\s*n[°º]?\s*[:\s-]*?(\d{5,10})", r"N[°º]?\s*commande\s*[:\s-]*?(\d{5,10})", r"Bon\s+de\s+Livraison\s+Nr\.?\s*[:\s-]*?(\d{5,10})"]
    found = []
    for pat in patterns:
        for m in re.finditer(pat, text, flags=re.IGNORECASE):
            num = m.group(1)
            if num and num not in found: found.append(num)
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
                    parts = ligne.split()
                    ean_pos = None
                    for idx, part in enumerate(parts):
                        if ean in part: ean_pos = idx; break
                    code_article = ""
                    if ean_pos and ean_pos > 1:
                        candidate = parts[ean_pos - 1]
                        if re.match(r"^\d{3,6}$", candidate): code_article = candidate
                    qty_match = re.search(r"Conditionnement\s*:\s*\d+\s+\d+(\d+)\s+(\d+)", ligne)
                    if qty_match: qte = int(qty_match.group(1))
                    else:
                        nums = re.findall(r"\b(\d+)\b", ligne)
                        nums = [int(n) for n in nums if n != ean and len(str(n)) < 6]
                        if nums: qte = nums[-2] if len(nums) >= 2 else nums[-1]
                        else: continue
                    records.append({"ref": ean, "code_article": code_article, "qte_commande": qte, "order_num": current_order or "__NO_ORDER__"})
    except Exception: pass
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
                    qte = None
                    if nums:
                        candidate = nums[-2] if len(nums) >= 2 else nums[-1]
                        try: qte = float(candidate.replace(",", "."))
                        except: continue
                    if qte is None: continue
                    records.append({"ref": ean, "qte_bl": qte, "order_num": current_order or "__NO_ORDER__"})
    except Exception: pass
    return {"records": records, "order_numbers": find_order_numbers_in_text(full_text), "full_text": full_text}

def calculate_service_rate(qte_cmd, qte_bl):
    if pd.isna(qte_bl) or qte_cmd == 0: return 0
    return min((qte_bl / qte_cmd) * 100, 100)

# --- 3. NOUVELLE FONCTIONNALITÉ WEB (EDI1) ---
def fetch_web_simulation(site, date_val):
    # Simulation des données retournées par le site (Scraping simulé)
    date_str = date_val.strftime("%d/%m/%Y")
    data = []
    
    if site == "EDI1":
        # Données basées sur votre capture d'écran
        data = [
            {"Numéro": "46961161", "Client": "INTERMARCHÉ", "Livrer à": "ETABLISSEMENT DOLE", "Date": f"{date_str} 09:17", "Montant": 4085.29, "Statut": "Integré"},
            {"Numéro": "46962231", "Client": "INTERMARCHÉ", "Livrer à": "ITM LUXEMONT-ET-VILLOTTE", "Date": f"{date_str} 09:17", "Montant": 1229.78, "Statut": "Integré"},
            {"Numéro": "03879534", "Client": "DEPOT CSD ALBY", "Livrer à": "ENTREPOT CSD produits frais", "Date": f"{date_str} 09:21", "Montant": 0.02, "Statut": "Integré"},
            # Client non VIP pour tester
            {"Numéro": "99999999", "Client": "CARREFOUR", "Livrer à": "MAGASIN TEST", "Date": f"{date_str} 08:00", "Montant": 150.00, "Statut": "En attente"}
        ]
    elif site == "AUCHAN":
        data = [
            {"Numéro": "AU-001", "Client": "AUCHAN RETAIL", "Livrer à": "AUCHAN VELIZY", "Date": f"{date_str} 10:00", "Montant": 5000.00, "Statut": "Integré"}
        ]
    return pd.DataFrame(data)

# --- 4. APPLICATION PRINCIPALE ---

def main_app():
    # Affichage Logo
    try:
        with open("Desathor.png", "rb") as f:
            encoded = base64.b64encode(f.read()).decode()
        st.markdown(f"""<div style="display: flex; flex-direction: column; align-items: center; margin-top: 10px;"><img src="data:image/png;base64,{encoded}" style="width:200px;"></div>""", unsafe_allow_html=True)
    except: st.markdown("# DESATHOR")

    # SIDEBAR
    with st.sidebar:
        st.write(f"👤 **{st.session_state.current_user}**")
        if st.button("🚪 Déconnexion"):
            st.session_state.logged_in = False
            st.rerun()
        
        st.markdown("---")
        # Admin Panel (Visible seulement par admin)
        if st.session_state.user_role == 'admin':
            admin_panel()
            st.markdown("---")

        # Fichiers (Déplacés en haut)
        st.header("📁 Fichiers (PDF)")
        if st.button("🔄 Nouveau Dossier", type="primary"):
            st.session_state.key_cmd = f"cmd_{time.time()}"
            st.session_state.key_bl = f"bl_{time.time()}"
            st.session_state.historique = []
            st.rerun()
            
        commande_files = st.file_uploader("📦 Commandes", type="pdf", accept_multiple_files=True, key=st.session_state.key_cmd)
        bl_files = st.file_uploader("📋 BLs", type="pdf", accept_multiple_files=True, key=st.session_state.key_bl)
        
        st.markdown("---")
        st.header("⚙️ Options")
        hide_unmatched = st.checkbox("👁️‍🗨️ Masquer sans correspondance", value=True)
        
        if st.session_state.historique:
            st.markdown("---")
            st.write(f"📊 **{len(st.session_state.historique)}** analyses")
            if st.button("🗑️ Vider historique"):
                st.session_state.historique = []
                st.rerun()

    # ONGLETS PRINCIPAUX
    tab_pdf, tab_web = st.tabs(["📄 Comparateur PDF", "🌐 Vérification Web (EDI)"])

    # === TAB 1 : LE COMPARATEUR ORIGINAL (AUCUNE MODIFICATION LOGIQUE) ===
    with tab_pdf:
        st.markdown('<h1 class="main-header">🧾 Comparateur PDF</h1>', unsafe_allow_html=True)
        
        # Boutons côte à côte (Format demandé: Grand bouton action + Petit bouton aide)
        c1, c2 = st.columns([3, 1])
        with c1:
            run_btn = st.button("🔍 Lancer la comparaison", type="primary")
        with c2:
            help_btn = st.button("❓ Aide")
            
        if help_btn: st.info("Chargez vos fichiers PDF dans le menu de gauche.")

        if run_btn:
            if not commande_files or not bl_files:
                st.error("⚠️ Veuillez charger les fichiers Commandes ET BL.")
            else:
                with st.spinner("🔄 Analyse en cours..."):
                    # VOTRE LOGIQUE ORIGINALE RECOPIÉE ICI
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
                    st.session_state.historique.append({"results": results})

        # AFFICHAGE RÉSULTATS PDF
        if st.session_state.historique:
            latest = st.session_state.historique[-1]
            results = latest["results"]
            
            # KPIs globaux
            total_cmd = sum([df["qte_commande"].sum() for df in results.values()])
            total_bl = sum([df["qte_bl"].sum() for df in results.values()])
            taux_global = (total_bl/total_cmd*100) if total_cmd > 0 else 0
            
            kc1, kc2, kc3 = st.columns(3)
            with kc1: st.metric("Total Commandé", int(total_cmd))
            with kc2: st.metric("Total Livré", int(total_bl))
            with kc3: st.metric("Taux Global", f"{taux_global:.1f}%")
            
            st.markdown("### Détails")
            for order, df in results.items():
                # Filtre visuel
                if hide_unmatched and df["qte_bl"].sum() == 0: continue
                
                with st.expander(f"Commande {order}"):
                    def color_row(row):
                        if row["status"] == "OK": return ['background-color: #d4edda']*len(row)
                        if row["status"] == "MISSING_IN_BL": return ['background-color: #f8d7da']*len(row)
                        return ['background-color: #fff3cd']*len(row)
                    st.dataframe(df.style.apply(color_row, axis=1), use_container_width=True)
            
            # EXPORT EXCEL
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
                for order, df in results.items():
                    df.to_excel(writer, sheet_name=f"C_{order}"[:31], index=False)
            st.download_button("📥 Télécharger Rapport Excel", data=output.getvalue(), file_name="Rapport_PDF.xlsx")

    # === TAB 2 : NOUVELLE VÉRIFICATION WEB (SÉPARÉE POUR NE RIEN CASSER) ===
    with tab_web:
        st.markdown("## 🌐 Vérification DESADV (EDI)")
        
        # CLIENTS SPECIFIQUES EDI1 (SANS LIMITE DE MONTANT)
        CLIENTS_VIP = ["ENTREPOT CSD produits frais", "ITM LUXEMONT-ET-VILLOTTE", "ETABLISSEMENT DOLE"]
        
        wc1, wc2, wc3 = st.columns(3)
        with wc1:
            site_choice = st.selectbox("Choisir le site", ["EDI1", "AUCHAN"])
        with wc2:
            date_choice = st.date_input("Date", datetime.now())
        with wc3:
            st.write(""); st.write("") # Espacement
            web_btn = st.button("📥 Vérifier DESADV", type="primary")
            
        if web_btn:
            df_web = fetch_web_simulation(site_choice, date_choice)
            
            st.markdown(f"### Résultats : {site_choice} ({date_choice.strftime('%d/%m/%Y')})")
            
            if site_choice == "EDI1":
                st.info("ℹ️ Les clients en **Vert** sont les clients spéciaux (CSD, ITM Luxemont, Dole) sans restriction de montant.")
                
                def highlight_vip(row):
                    if row["Livrer à"] in CLIENTS_VIP:
                        return ['background-color: #d4edda; color: black; font-weight: bold'] * len(row)
                    return [''] * len(row)
                
                st.dataframe(df_web.style.apply(highlight_vip, axis=1), use_container_width=True)
                
            else:
                # Affichage Standard pour Auchan (ou autre)
                st.dataframe(df_web, use_container_width=True)

# Point d'entrée
if __name__ == "__main__":
    if not st.session_state.logged_in:
        login_page()
    else:
        main_app()
