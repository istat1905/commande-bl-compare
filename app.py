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
# Dans une vraie app, utiliser st.secrets ou une BDD.
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
    st.markdown("<h1 style='text-align: center; color: #1f77b4;'>🔒 DESATHOR - Connexion</h1>", unsafe_allow_html=True)
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

# --- VOTRE CODE ORIGINAL (FONCTIONS DE PARSING & LOGIQUE) ---
# JE NE TOUCHE PAS A CA POUR GARDER LES STATS ET LA COMPARAISON
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
    if not code or len(code) != 13:
        return False
    if code.startswith(('302', '376')):
        return False
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
                for i, ligne in enumerate(lines):
                    order_nums = find_order_numbers_in_text(ligne)
                    if order_nums:
                        current_order = order_nums[0]
                    if re.search(r"^L\s+Réf\.\s*frn\s+Code\s+ean", ligne, re.IGNORECASE):
                        in_data_section = True
                        continue
                    if re.search(r"^Récapitulatif|^Page\s+\d+", ligne, re.IGNORECASE):
                        in_data_section = False
                        continue
                    if not in_data_section:
                        continue
                    ean_matches = re.findall(r"\b(\d{13})\b", ligne)
                    valid_eans = [ean for ean in ean_matches if is_valid_ean13(ean)]
                    if not valid_eans:
                        continue
                    ean = valid_eans[0]
                    parts = ligne.split()
                    ean_pos = None
                    for idx, part in enumerate(parts):
                        if ean in part:
                            ean_pos = idx
                            break
                    ref_frn = None
                    code_article = ""
                    if ean_pos and ean_pos > 1:
                        candidate = parts[ean_pos - 1]
                        if re.match(r"^\d{3,6}$", candidate):
                            code_article = candidate
                            ref_frn = candidate
                    qty_match = re.search(r"Conditionnement\s*:\s*\d+\s+\d+(\d+)\s+(\d+)", ligne)
                    if qty_match:
                        qte = int(qty_match.group(1))
                    else:
                        nums = re.findall(r"\b(\d+)\b", ligne)
                        nums = [int(n) for n in nums if n != ean and len(str(n)) < 6]
                        if nums:
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
                    if order_nums:
                        current_order = order_nums[0]
                    ean_matches = re.findall(r"\b(\d{13})\b", ligne)
                    valid_eans = [ean for ean in ean_matches if is_valid_ean13(ean)]
                    if not valid_eans:
                        continue
                    ean = valid_eans[0]
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

def calculate_service_rate(qte_cmd, qte_bl):
    if pd.isna(qte_bl) or qte_cmd == 0:
        return 0
    return min((qte_bl / qte_cmd) * 100, 100)

# --- NOUVELLE FONCTION POUR SIMULATION EDI1 ---
def fetch_edi1_simulation(date_selected):
    # Simulation des données EDI1 pour les clients spécifiques demandés
    data = [
        {"Numéro": "46961161", "Client": "INTERMARCHÉ", "Livrer à": "ETABLISSEMENT DOLE", "Date": f"{date_selected} 09:17", "Montant": 4085.29, "Statut": "Integré"},
        {"Numéro": "46962231", "Client": "INTERMARCHÉ", "Livrer à": "ITM LUXEMONT-ET-VILLOTTE", "Date": f"{date_selected} 09:17", "Montant": 1229.78, "Statut": "Integré"},
        {"Numéro": "03879534", "Client": "DEPOT CSD ALBY", "Livrer à": "ENTREPOT CSD produits frais", "Date": f"{date_selected} 09:21", "Montant": 0.02, "Statut": "Integré"},
        # Un client qui N'EST PAS dans la liste des 3 pour voir la différence
        {"Numéro": "99999999", "Client": "CARREFOUR", "Livrer à": "PARIS", "Date": f"{date_selected} 08:30", "Montant": 150.50, "Statut": "En erreur"}
    ]
    return pd.DataFrame(data)

# --- MAIN APPLICATION ---
def main_app():
    # LOGO
    try:
        with open("Desathor.png", "rb") as f:
            data = f.read()
        encoded = base64.b64encode(data).decode()
        st.markdown(
            f"""
            <div style="display: flex; flex-direction: column; align-items: center; margin-top: 10px;">
                <img src="data:image/png;base64,{encoded}" style="width:200px; max-width:80%; height:auto;">
            </div>
            """,
            unsafe_allow_html=True
        )
    except:
        st.markdown("# DESATHOR")

    # STYLES
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
    </style>
    """, unsafe_allow_html=True)

    # --- SIDEBAR MODIFIEE (Point 2 & 3) ---
    with st.sidebar:
        # Nom utilisateur + Déconnexion
        st.markdown(f"👤 **{st.session_state.current_user}** ({st.session_state.user_role})")
        if st.button("🚪 Déconnexion", use_container_width=True):
            st.session_state.logged_in = False
            st.rerun()
        
        st.markdown("---")
        
        # Menu Admin (Point 3: Seul admin a accès)
        if st.session_state.user_role == 'admin':
            admin_interface()
            st.markdown("---")

        # Fichiers au dessus (Point 2)
        st.header("📁 Fichiers")
        if st.button("🔄 Nouveau", use_container_width=True, type="primary"):
            st.session_state.key_cmd = f"cmd_{time.time()}"
            st.session_state.key_bl = f"bl_{time.time()}"
            st.session_state.historique = []
            st.rerun()
            
        commande_files = st.file_uploader(
            "📦 PDF(s) Commande client", 
            type="pdf", 
            accept_multiple_files=True,
            key=st.session_state.key_cmd
        )
        bl_files = st.file_uploader(
            "📋 PDF(s) Bon de livraison", 
            type="pdf", 
            accept_multiple_files=True,
            key=st.session_state.key_bl
        )
        
        st.markdown("---")
        st.header("⚙️ Options")
        hide_unmatched = st.checkbox(
            "👁️‍🗨️ Masquer les commandes sans correspondance",
            value=True,
            help="Exclut les articles MISSING_IN_BL de l'export Excel"
        )
        st.markdown("---")
        st.header("📊 Historique")
        if st.session_state.historique:
            st.write(f"**{len(st.session_state.historique)}** comparaison(s)")
            if st.button("🗑️ Supprimer tout l'historique", use_container_width=True):
                st.session_state.historique = []
                st.success("Historique supprimé")
                st.rerun()
        else:
            st.info("Aucune comparaison enregistrée")

    # --- TABS POUR SEPARER COMPARATEUR ET NOUVELLE FONCTIONNALITE ---
    tab_compare, tab_edi = st.tabs(["🧾 Comparateur PDF", "🌐 Vérification EDI1"])

    # === ONGLET 1 : LE COMPARATEUR (VOTRE CODE INTACT) ===
    with tab_compare:
        st.markdown('<h1 class="main-header">🧾 Comparateur PDF</h1>', unsafe_allow_html=True)
        
        # Point 1 : Boutons côte à côte
        col_btn1, col_btn2 = st.columns([3, 1])
        with col_btn1:
            run_comparison = st.button("🔍 Lancer la comparaison", use_container_width=True, type="primary")
        with col_btn2:
            help_btn = st.button("❓ Aide", use_container_width=True)

        if help_btn:
            st.info("Téléversez vos fichiers dans le menu à gauche.")

        if run_comparison:
            if not commande_files or not bl_files:
                st.error("⚠️ Veuillez téléverser des commandes ET des bons de livraison.")
            else:
                # LE CŒUR DU TRAITEMENT (VOTRE LOGIQUE EXACTE)
                with st.spinner("🔄 Analyse en cours..."):
                    commandes_dict = defaultdict(list)
                    all_command_records = []
                    for f in commande_files:
                        res = extract_records_from_command_pdf(f)
                        all_command_records.extend(res["records"])
                        for rec in res["records"]:
                            commandes_dict[rec["order_num"]].append(rec)
                    for k in commandes_dict.keys():
                        df = pd.DataFrame(commandes_dict[k])
                        df = df.groupby(["ref", "code_article"], as_index=False).agg({"qte_commande": "sum"})
                        commandes_dict[k] = df
                    bls_dict = defaultdict(list)
                    all_bl_records = []
                    for f in bl_files:
                        res = extract_records_from_bl_pdf(f)
                        all_bl_records.extend(res["records"])
                        for rec in res["records"]:
                            bls_dict[rec["order_num"]].append(rec)
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
                        def status_row(r):
                            if r["qte_bl"] == 0:
                                return "MISSING_IN_BL"
                            return "OK" if r["qte_commande"] == r["qte_bl"] else "QTY_DIFF"
                        merged["status"] = merged.apply(status_row, axis=1)
                        merged["diff"] = merged["qte_bl"] - merged["qte_commande"]
                        merged["taux_service"] = merged.apply(
                            lambda r: calculate_service_rate(r["qte_commande"], r["qte_bl"]), axis=1
                        )
                        results[order_num] = merged
                    comparison_data = {
                        "timestamp": datetime.now(),
                        "results": results,
                        "commandes_dict": commandes_dict,
                        "bls_dict": bls_dict,
                        "hide_unmatched": hide_unmatched
                    }
                    st.session_state.historique.append(comparison_data)

        # AFFICHAGE DES RESULTATS (VOTRE AFFICHAGE EXACT)
        if st.session_state.historique:
            latest = st.session_state.historique[-1]
            results = latest["results"]
            commandes_dict = latest["commandes_dict"]
            bls_dict = latest["bls_dict"]
            # hide_unmatched peut changer dynamiquement, on prend celui de la sidebar
            
            def order_included(df):
                total_bl = df["qte_bl"].sum() if "qte_bl" in df.columns else 0
                if hide_unmatched and total_bl == 0:
                    return False
                return True
            
            total_commande = sum([df["qte_commande"].sum() for df in results.values() if order_included(df)])
            total_livre = sum([df["qte_bl"].sum() for df in results.values() if order_included(df)])
            total_manquant = total_commande - total_livre
            taux_service_global = (total_livre / total_commande * 100) if total_commande > 0 else 0
            total_articles_ok = sum([(df["status"] == "OK").sum() for df in results.values() if order_included(df)])
            total_articles_diff = sum([(df["status"] == "QTY_DIFF").sum() for df in results.values() if order_included(df)])
            total_articles_missing = sum([(df["status"] == "MISSING_IN_BL").sum() for df in results.values() if order_included(df)])
            
            st.markdown("### 📋 Détails par commande")
            for order_num, df in results.items():
                if not order_included(df):
                    continue
                n_ok = (df["status"] == "OK").sum()
                n_diff = (df["status"] == "QTY_DIFF").sum()
                n_miss = (df["status"] == "MISSING_IN_BL").sum()
                total_cmd = df["qte_commande"].sum()
                total_bl = df["qte_bl"].sum()
                taux = (total_bl / total_cmd * 100) if total_cmd > 0 else 0
                with st.expander(
                    f"📦 Commande **{order_num}** — Taux de service: **{taux:.1f}%** | "
                    f"✅ {n_ok} | ⚠️ {n_diff} | ❌ {n_miss}"
                ):
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.metric("Commandé", int(total_cmd))
                    with col2:
                        st.metric("Livré", int(total_bl))
                    with col3:
                        st.metric("Manquant", int(total_cmd - total_bl))
                    def color_status(val):
                        if val == "OK": return "background-color: #d4edda"
                        if val == "QTY_DIFF": return "background-color: #fff3cd"
                        if val == "MISSING_IN_BL": return "background-color: #f8d7da"
                        return ""
                    st.dataframe(
                        df.style.applymap(color_status, subset=["status"]),
                        use_container_width=True,
                        height=400
                    )
            
            st.markdown("---")
            st.markdown("### 📥 Export")
            output = io.BytesIO()
            timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"Comparaison_{timestamp_str}.xlsx"
            with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
                for order_num, df in results.items():
                    total_bl = df["qte_bl"].sum() if "qte_bl" in df.columns else 0
                    if hide_unmatched and total_bl == 0:
                        continue
                    df_export = df.copy()
                    sheet_name = f"C_{order_num}"[:31]
                    df_export.to_excel(writer, sheet_name=sheet_name, index=False)
                    workbook = writer.book
                    worksheet = writer.sheets[sheet_name]
                    ok_format = workbook.add_format({'bg_color': '#d4edda'})
                    diff_format = workbook.add_format({'bg_color': '#fff3cd'})
                    miss_format = workbook.add_format({'bg_color': '#f8d7da'})
                    for idx, row in df_export.iterrows():
                        excel_row = idx + 1
                        if row.get('status') == 'OK': worksheet.set_row(excel_row, None, ok_format)
                        elif row.get('status') == 'QTY_DIFF': worksheet.set_row(excel_row, None, diff_format)
                        elif row.get('status') == 'MISSING_IN_BL': worksheet.set_row(excel_row, None, miss_format)
                # Recap sheet
                summary_data = {
                    'Commande': [], 'Taux de service (%)': [], 'Qté commandée': [], 'Qté livrée': [],
                    'Qté manquante': [], 'Articles OK': [], 'Articles différence': [], 'Articles manquants': []
                }
                for order_num, df in results.items():
                    total_bl = df["qte_bl"].sum() if "qte_bl" in df.columns else 0
                    if hide_unmatched and total_bl == 0: continue
                    total_cmd = df["qte_commande"].sum()
                    total_bl = df["qte_bl"].sum()
                    taux = (total_bl / total_cmd * 100) if total_cmd > 0 else 0
                    summary_data['Commande'].append(order_num)
                    summary_data['Taux de service (%)'].append(round(taux, 2))
                    summary_data['Qté commandée'].append(int(total_cmd))
                    summary_data['Qté livrée'].append(int(total_bl))
                    summary_data['Qté manquante'].append(int(total_cmd - total_bl))
                    summary_data['Articles OK'].append((df["status"] == "OK").sum())
                    summary_data['Articles différence'].append((df["status"] == "QTY_DIFF").sum())
                    summary_data['Articles manquants'].append((df["status"] == "MISSING_IN_BL").sum())
                df_summary = pd.DataFrame(summary_data)
                df_summary.to_excel(writer, sheet_name="Récapitulatif", index=False)
            
            col1, col2 = st.columns([3, 1])
            with col1:
                st.download_button(
                    "📥 Télécharger le rapport Excel",
                    data=output.getvalue(),
                    file_name=filename,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True
                )
            with col2:
                if st.button("🗑️ Supprimer ce résultat", use_container_width=True):
                    st.session_state.historique.pop()
                    st.rerun()
            
            # VISUALISATIONS KPI ET GRAPHIQUES (VOTRE CODE INTACT)
            st.markdown("---")
            st.markdown("### 📊 Vue d'ensemble")
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.markdown(f"""<div class="kpi-card success-card"><div class="kpi-label">Taux de service global</div><div class="kpi-value">{taux_service_global:.1f}%</div></div>""", unsafe_allow_html=True)
            with col2:
                st.markdown(f"""<div class="kpi-card info-card"><div class="kpi-label">Total commandé</div><div class="kpi-value">{int(total_commande)}</div></div>""", unsafe_allow_html=True)
            with col3:
                st.markdown(f"""<div class="kpi-card"><div class="kpi-label">Total livré</div><div class="kpi-value">{int(total_livre)}</div></div>""", unsafe_allow_html=True)
            with col4:
                st.markdown(f"""<div class="kpi-card warning-card"><div class="kpi-label">Total manquant</div><div class="kpi-value">{int(total_manquant)}</div></div>""", unsafe_allow_html=True)
            st.markdown("<br>", unsafe_allow_html=True)
            
            col1, col2 = st.columns(2)
            if PLOTLY_AVAILABLE:
                with col1:
                    status_data = pd.DataFrame({
                        'Statut': ['✅ OK', '⚠️ Différence', '❌ Manquant'],
                        'Nombre': [total_articles_ok, total_articles_diff, total_articles_missing]
                    })
                    fig_status = px.pie(status_data, values='Nombre', names='Statut', title='Répartition des articles', color_discrete_sequence=['#38ef7d', '#f5576c', '#ff6b6b'])
                    fig_status.update_traces(textposition='inside', textinfo='percent+label')
                    st.plotly_chart(fig_status, use_container_width=True)
                with col2:
                    service_rates = []
                    for order_num, df in results.items():
                        if not order_included(df): continue
                        total_cmd = df["qte_commande"].sum()
                        total_bl = df["qte_bl"].sum()
                        rate = (total_bl / total_cmd * 100) if total_cmd > 0 else 0
                        service_rates.append({'Commande': str(order_num), 'Taux de service': rate})
                    df_service = pd.DataFrame(service_rates)
                    if not df_service.empty:
                        fig_service = go.Figure(data=[go.Bar(x=df_service['Commande'], y=df_service['Taux de service'], marker=dict(color=df_service['Taux de service'], colorscale=[[0, '#ff6b6b'], [0.5, '#ffd93d'], [1, '#38ef7d']], cmin=0, cmax=100, showscale=False), text=[f"{v:.1f}%" for v in df_service['Taux de service']], textposition='outside')])
                        fig_service.update_layout(title='Taux de service par commande', yaxis_range=[0, 110])
                        st.plotly_chart(fig_service, use_container_width=True)
            
            tabs_stat = st.tabs(["📈 Statistiques", "🏆 Top produits"])
            with tabs_stat[0]:
                st.markdown("### 📈 Articles manquants par code article")
                missing_by_code = {}
                for order_num, df in results.items():
                    if not order_included(df): continue
                    missing = df[df["status"] == "MISSING_IN_BL"]
                    for _, row in missing.iterrows():
                        code = row["code_article"]
                        if code not in missing_by_code: missing_by_code[code] = {"Code article": code, "Qté totale manquante": 0}
                        missing_by_code[code]["Qté totale manquante"] += int(row["qte_commande"])
                if missing_by_code:
                    df_missing = pd.DataFrame(list(missing_by_code.values())).sort_values("Qté totale manquante", ascending=False).head(10)
                    st.dataframe(df_missing, use_container_width=True, hide_index=True)
                else:
                    st.success("✅ Aucun article manquant !")
            with tabs_stat[1]:
                st.markdown("### 🏆 Classement des produits")
                all_products = []
                for order_num, df in results.items():
                    if not order_included(df): continue
                    for _, row in df.iterrows():
                        all_products.append({"Code article": row["code_article"], "EAN": row["ref"], "Qté commandée": int(row["qte_commande"]), "Qté livrée": int(row["qte_bl"])})
                if all_products:
                    df_products = pd.DataFrame(all_products)
                    c1, c2 = st.columns(2)
                    with c1:
                        st.markdown("#### 📦 Top 10 commandés")
                        st.dataframe(df_products.groupby("Code article")["Qté commandée"].sum().sort_values(ascending=False).head(10).reset_index(), use_container_width=True, hide_index=True)
                    with c2:
                        st.markdown("#### 📋 Top 10 livrés")
                        st.dataframe(df_products.groupby("Code article")["Qté livrée"].sum().sort_values(ascending=False).head(10).reset_index(), use_container_width=True, hide_index=True)

    # === ONGLET 2 : VERIFICATION EDI1 (NOUVEAUTE) ===
    with tab_edi:
        st.markdown("## 🌐 Vérification DESADV - EDI1")
        st.markdown("Vérification des clients spécifiques sur `edi1.atgpedi.net`")
        
        # LISTE DES CLIENTS SPECIFIQUES DEMANDÉS (Point 4)
        CLIENTS_VIP = [
            "ENTREPOT CSD produits frais",
            "ITM LUXEMONT-ET-VILLOTTE",
            "ETABLISSEMENT DOLE"
        ]
        
        col_edi1, col_edi2, col_edi3 = st.columns(3)
        
        with col_edi1:
            # Point 4: Séparation logic Auchan et EDI1
            site_select = st.selectbox("Site", ["EDI1", "AUCHAN"], index=0)
        
        with col_edi2:
            # Point 5: Choix du jour
            date_check = st.date_input("Date à vérifier", datetime.now() - timedelta(days=1))
            
        with col_edi3:
            st.write("")
            st.write("")
            launch_edi = st.button("📥 Récupérer liste EDI", type="primary", use_container_width=True)
            
        if launch_edi:
            # Simulation des résultats (Point 5)
            if site_select == "EDI1":
                df_edi = fetch_edi1_simulation(date_check.strftime("%d/%m/%Y"))
                st.markdown(f"### Résultats pour EDI1 (Date: {date_check})")
                
                # Logique spécifique : Mettre en valeur les 3 clients VIP
                def highlight_clients(row):
                    # Si le client est dans la liste des 3 VIP -> Vert (Pas de restriction)
                    # Sinon -> Standard
                    if row["Livrer à"] in CLIENTS_VIP:
                        return ['background-color: #d4edda'] * len(row)
                    return [''] * len(row)
                
                st.info("ℹ️ Les lignes en vert correspondent aux 3 clients sans restriction de montant.")
                st.dataframe(df_edi.style.apply(highlight_clients, axis=1), use_container_width=True)
            else:
                st.warning("Module AUCHAN non configuré pour le moment (Focus sur EDI1 demandé).")

if __name__ == "__main__":
    if not st.session_state.logged_in:
        login_system()
    else:
        main_app()
