import streamlit as st
import pdfplumber
import pandas as pd
import io
import re
from collections import defaultdict
from datetime import datetime, timedelta
import time

try:
    import plotly.express as px
    import plotly.graph_objects as go
    PLOTLY_AVAILABLE = True
except ImportError:
    PLOTLY_AVAILABLE = False
    st.warning("⚠️ Plotly non installé. Les graphiques ne seront pas affichés.")

import base64

st.set_page_config(
    page_title="DESATHOR",
    layout="wide",
    initial_sidebar_state="expanded"
)

# CSS Styles
st.markdown("""
<style>
    .logo-container {
        display: flex;
        justify-content: center;
        margin-top: 10px;
        margin-bottom: 20px;
    }
    .main-header {
        font-size: 2.5rem;
        font-weight: 700;
        color: #1f77b4;
        margin-bottom: 0.5rem;
    }
    .subtitle {
        font-size: 1.1rem;
        color: #666;
        margin-bottom: 2rem;
    }
    .kpi-card {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 1.5rem;
        border-radius: 10px;
        color: white;
        text-align: center;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
    }
    .kpi-value {
        font-size: 2.5rem;
        font-weight: bold;
        margin: 0.5rem 0;
    }
    .kpi-label {
        font-size: 0.9rem;
        opacity: 0.9;
    }
    .success-card {
        background: linear-gradient(135deg, #11998e 0%, #38ef7d 100%);
    }
    .warning-card {
        background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
    }
    .info-card {
        background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%);
    }
</style>
""", unsafe_allow_html=True)

# Logo
with open("Desathor.png", "rb") as f:
    data = f.read()
encoded = base64.b64encode(data).decode()

st.markdown(
    f"""
    <div class="logo-container">
        <img src="data:image/png;base64,{encoded}" style="width:250px; max-width:80%; height:auto;">
    </div>
    """,
    unsafe_allow_html=True
)

# Session state initialization
if 'historique' not in st.session_state:
    st.session_state.historique = []
if "key_cmd" not in st.session_state:
    st.session_state.key_cmd = "cmd_1"
if "key_bl" not in st.session_state:
    st.session_state.key_bl = "bl_1"
if "show_help" not in st.session_state:
    st.session_state.show_help = False
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "user_role" not in st.session_state:
    st.session_state.user_role = None
if "show_desadv_details" not in st.session_state:
    st.session_state.show_desadv_details = False

# ============================================
# BASE DE DONNÉES UTILISATEURS
# ============================================
# Pour ajouter un nouvel utilisateur :
# 1. Ajoutez une ligne avec : "identifiant": {"password": "mot_de_passe", "role": "admin ou user", "web_access": True ou False}
# 2. role: "admin" = accès complet, "user" = accès limité
# 3. web_access: True = peut vérifier DESADV, False = pas d'accès web
USERS_DB = {
    "admin": {"password": "admin123", "role": "admin", "web_access": True},
    "user1": {"password": "user123", "role": "user", "web_access": False},
    "logistic": {"password": "log2025", "role": "admin", "web_access": True},
}

def check_password(username, password):
    """Vérifie les identifiants utilisateur"""
    if username in USERS_DB and USERS_DB[username]["password"] == password:
        return True, USERS_DB[username]["role"], USERS_DB[username]["web_access"]
    return False, None, False

# Page de connexion
if not st.session_state.authenticated:
    st.markdown("---")
    st.markdown("### 🔐 Connexion requise")
    
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        with st.form("login_form"):
            username = st.text_input("👤 Identifiant")
            password = st.text_input("🔒 Mot de passe", type="password")
            submit = st.form_submit_button("Se connecter", use_container_width=True, type="primary")
            
            if submit:
                is_valid, role, web_access = check_password(username, password)
                if is_valid:
                    st.session_state.authenticated = True
                    st.session_state.user_role = role
                    st.session_state.user_web_access = web_access
                    st.session_state.username = username
                    st.success(f"✅ Bienvenue {username} !")
                    st.rerun()
                else:
                    st.error("❌ Identifiant ou mot de passe incorrect")
        
        st.info("💡 **Demo**: admin / admin123")
    st.stop()

st.markdown('<h1 class="main-header">🧾 Comparateur pour DESADV</h1>', unsafe_allow_html=True)
st.markdown(f'<p class="subtitle">Bienvenue {st.session_state.username} ({st.session_state.user_role}) | Analysez vos commandes et bons de livraison en quelques clics</p>', unsafe_allow_html=True)

# ============================================
# FONCTIONS UTILITAIRES
# ============================================

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
                        nums = [int(n) for n in nums if n != ean and len(n) < 6]
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

# ============================================
# FONCTIONS DESADV
# ============================================

def fetch_desadv_from_auchan_real():
    """
    Connexion RÉELLE au site Auchan ATGPED avec debugging
    """
    try:
        import requests
        from bs4 import BeautifulSoup
        import os
        
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%d/%m/%Y")
        session = requests.Session()
        
        # Récupérer les credentials
        try:
            username = st.secrets.get("EDI_USERNAME", "")
            password = st.secrets.get("EDI_PASSWORD", "")
        except:
            username = os.getenv("EDI_USERNAME", "")
            password = os.getenv("EDI_PASSWORD", "")
        
        if not username or not password:
            return [], tomorrow, "❌ Identifiants non configurés"
        
        # ÉTAPE 1: Page de connexion
        login_url = "https://auchan.atgped.net/gui.php"
        
        # D'abord récupérer la page de login pour voir le formulaire
        try:
            initial_response = session.get(login_url, timeout=10)
            st.info(f"🔍 Status initial: {initial_response.status_code}")
        except Exception as e:
            return [], tomorrow, f"❌ Impossible d'accéder au site: {str(e)}"
        
        # Tentative de connexion
        login_data = {
            "username": username,
            "password": password,
            "submit": "Connexion"  # Souvent nécessaire
        }
        
        try:
            login_response = session.post(login_url, data=login_data, timeout=15)
            st.info(f"🔍 Status après login: {login_response.status_code}")
            
            # Vérifier si on est connecté
            if "Liste des commandes" in login_response.text:
                st.success("✅ Connexion réussie (détecté 'Liste des commandes')")
            elif "Documents" in login_response.text:
                st.success("✅ Connexion réussie (détecté 'Documents')")
            elif "Deconnexion" in login_response.text or "Déconnexion" in login_response.text:
                st.success("✅ Connexion réussie (détecté 'Déconnexion')")
            else:
                # Afficher un extrait de la page pour debug
                st.warning("⚠️ Connexion incertaine, analyse de la réponse...")
                soup = BeautifulSoup(login_response.content, 'html.parser')
                title = soup.find('title')
                st.info(f"Titre de la page: {title.text if title else 'Aucun titre'}")
                
                # Chercher des indices de connexion réussie/échouée
                if "erreur" in login_response.text.lower() or "incorrect" in login_response.text.lower():
                    return [], tomorrow, "❌ Identifiants incorrects"
                
        except Exception as e:
            return [], tomorrow, f"❌ Erreur lors de la connexion: {str(e)}"
        
        # ÉTAPE 2: Accéder à la liste des commandes
        commandes_url = "https://auchan.atgped.net/gui.php"
        params = {
            "query": "documents_commandes_liste",
            "page": "documents_commandes_liste",
        }
        
        try:
            response = session.get(commandes_url, params=params, timeout=15)
            st.info(f"🔍 Status liste commandes: {response.status_code}")
        except Exception as e:
            return [], tomorrow, f"❌ Erreur accès liste: {str(e)}"
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # ÉTAPE 3: Trouver le tableau
        table = soup.find('table', {'class': 'datalist'})  # Souvent avec cette classe
        if not table:
            table = soup.find('table')  # Sinon chercher n'importe quel tableau
        
        if not table:
            st.warning("⚠️ Aucun tableau trouvé")
            # Sauvegarder le HTML pour debug
            with st.expander("🔍 Debug: Voir le HTML de la page"):
                st.code(response.text[:2000], language='html')
            return [], tomorrow, "❌ Tableau introuvable"
        
        st.success(f"✅ Tableau trouvé avec {len(table.find_all('tr'))} lignes")
        
        # ÉTAPE 4: Parser le tableau
        commandes_brutes = []
        rows = table.find_all('tr')
        
        # Afficher l'en-tête pour comprendre la structure
        if rows:
            header = rows[0]
            headers = [th.text.strip() for th in header.find_all(['th', 'td'])]
            st.info(f"📋 Colonnes détectées: {headers}")
        
        for idx, row in enumerate(rows[1:], 1):  # Skip header
            cols = row.find_all('td')
            if len(cols) < 4:  # Au minimum il faut quelques colonnes
                continue
            
            try:
                # Extraire les données (à ajuster selon la structure réelle)
                numero = cols[0].text.strip()
                
                # Trouver la colonne de la date (chercher format JJ/MM/AAAA)
                date_livraison = None
                for col in cols:
                    text = col.text.strip()
                    if re.match(r'\d{2}/\d{2}/\d{4}', text):
                        date_livraison = text
                        break
                
                # Trouver le montant (chercher des nombres avec décimales)
                montant = None
                for col in cols:
                    text = col.text.strip().replace(" ", "").replace(",", ".")
                    if re.match(r'^\d+\.\d{2}', some_variable):

def fetch_desadv_from_edi1_real():
    """
    Connexion au site EDI1 avec debugging
    """
    try:
        import requests
        from bs4 import BeautifulSoup
        import os
        
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%d/%m/%Y")
        session = requests.Session()
        
        try:
            username = st.secrets.get("EDI_USERNAME", "")
            password = st.secrets.get("EDI_PASSWORD", "")
        except:
            username = os.getenv("EDI_USERNAME", "")
            password = os.getenv("EDI_PASSWORD", "")
        
        if not username or not password:
            return [], tomorrow, "❌ Identifiants non configurés"
        
        login_url = "https://ed1.atgped.net/gui.php"
        
        try:
            initial_response = session.get(login_url, timeout=10)
            st.info(f"🔍 EDI1 - Status initial: {initial_response.status_code}")
        except Exception as e:
            return [], tomorrow, f"❌ Impossible d'accéder à EDI1: {str(e)}"
        
        login_data = {
            "username": username,
            "password": password,
            "submit": "Connexion"
        }
        
        try:
            login_response = session.post(login_url, data=login_data, timeout=15)
            st.info(f"🔍 EDI1 - Status après login: {login_response.status_code}")
            
            if "Liste des commandes" in login_response.text or "Documents" in login_response.text or "Deconnexion" in login_response.text:
                st.success("✅ EDI1 - Connexion réussie")
            else:
                st.warning("⚠️ EDI1 - Connexion incertaine")
                
        except Exception as e:
            return [], tomorrow, f"❌ Erreur connexion EDI1: {str(e)}"
        
        commandes_url = "https://ed1.atgped.net/gui.php"
        params = {
            "query": "documents_commandes_liste",
            "page": "documents_commandes_liste",
        }
        
        try:
            response = session.get(commandes_url, params=params, timeout=15)
            st.info(f"🔍 EDI1 - Status liste: {response.status_code}")
        except Exception as e:
            return [], tomorrow, f"❌ Erreur accès liste EDI1: {str(e)}"
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        table = soup.find('table', {'class': 'datalist'})
        if not table:
            table = soup.find('table')
        
        if not table:
            st.warning("⚠️ EDI1 - Aucun tableau trouvé")
            return [], tomorrow, "❌ Tableau EDI1 introuvable"
        
        st.success(f"✅ EDI1 - Tableau trouvé avec {len(table.find_all('tr'))} lignes")
        
        clients_autorises = [
            "INTERMARCHE",
            "DEPOT CSD ALBY SUR CHERAN",
            "ITM LUXEMONT-ET-VILLOTTE",
            "CSD",
            "ITM"
        ]
        
        commandes_brutes = []
        rows = table.find_all('tr')
        
        if rows:
            header = rows[0]
            headers = [th.text.strip() for th in header.find_all(['th', 'td'])]
            st.info(f"📋 EDI1 - Colonnes: {headers}")
        
        for idx, row in enumerate(rows[1:], 1):
            cols = row.find_all('td')
            if len(cols) < 4:
                continue
            
            try:
                numero = cols[0].text.strip()
                client = cols[1].text.strip() if len(cols) > 1 else ""
                
                # Vérifier si client autorisé
                client_autorise = any(ca in client.upper() for ca in clients_autorises)
                
                if not client_autorise:
                    continue
                
                # Trouver date
                date_livraison = None
                for col in cols:
                    text = col.text.strip()
                    if re.match(r'\d{2}/\d{2}/\d{4}', text):
                        date_livraison = text
                        break
                
                # Trouver montant
                montant = None
                for col in cols:
                    text = col.text.strip().replace(" ", "").replace(",", ".")
                    if re.match(r'^\d+\.\d{2}

def fetch_desadv_from_auchan():
    """Version avec fallback pour Auchan"""
    result, date, status = fetch_desadv_from_auchan_real()
    
    if status != "success":
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%d/%m/%Y")
        commandes_brutes = [
            {"numero": "03385063", "entrepot": "PFI VENDENHEIM", "montant": 5432.70, "date_livraison": tomorrow},
            {"numero": "03311038", "entrepot": "APPRO PFI LE COUDRAY", "montant": 3406.81, "date_livraison": tomorrow},
            {"numero": "03401873", "entrepot": "PFI CARVIN", "montant": 3226.07, "date_livraison": tomorrow},
        ]
        
        entrepots = {}
        for cmd in commandes_brutes:
            entrepot = cmd["entrepot"]
            if entrepot not in entrepots:
                entrepots[entrepot] = {"montant_total": 0, "commandes": []}
            entrepots[entrepot]["montant_total"] += cmd["montant"]
            entrepots[entrepot]["commandes"].append(cmd["numero"])
        
        desadv_a_faire = []
        for entrepot, data in entrepots.items():
            if data["montant_total"] >= 850:
                desadv_a_faire.append({
                    "entrepot": entrepot,
                    "montant_total": data["montant_total"],
                    "nb_commandes": len(data["commandes"]),
                    "commandes": data["commandes"]
                })
        
        desadv_a_faire.sort(key=lambda x: x["montant_total"], reverse=True)
        return desadv_a_faire, tomorrow, "simulation"
    
    return result, date, status

def fetch_desadv_from_edi1():
    """Version avec fallback pour EDI1"""
    result, date, status = fetch_desadv_from_edi1_real()
    
    if status != "success":
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%d/%m/%Y")
        commandes_brutes = [
            {"numero": "46961161", "client": "INTERMARCHE", "montant": 4085.29, "date_livraison": tomorrow},
            {"numero": "46962231", "client": "ITM LUXEMONT-ET-VILLOTTE", "montant": 1293.78, "date_livraison": tomorrow},
        ]
        
        clients = {}
        for cmd in commandes_brutes:
            client = cmd["client"]
            if client not in clients:
                clients[client] = {"montant_total": 0, "commandes": []}
            clients[client]["montant_total"] += cmd["montant"]
            clients[client]["commandes"].append(cmd["numero"])
        
        desadv_a_faire = []
        for client, data in clients.items():
            if data["montant_total"] >= 850:
                desadv_a_faire.append({
                    "entrepot": client,
                    "montant_total": data["montant_total"],
                    "nb_commandes": len(data["commandes"]),
                    "commandes": data["commandes"]
                })
        
        desadv_a_faire.sort(key=lambda x: x["montant_total"], reverse=True)
        return desadv_a_faire, tomorrow, "simulation"
    
    return result, date, status

# ============================================
# SIDEBAR
# ============================================

with st.sidebar:
    st.header("👤 Utilisateur")
    st.markdown(f"**{st.session_state.username}**")
    st.caption(f"Rôle: {st.session_state.user_role}")
    
    if st.button("🚪 Déconnexion", use_container_width=True):
        st.session_state.authenticated = False
        st.session_state.user_role = None
        st.session_state.user_web_access = False
        st.session_state.username = None
        st.rerun()
    
    st.markdown("---")
    st.header("📁 Fichiers")
    
    if st.button("🔄 Nouveau", use_container_width=True, type="primary"):
        st.session_state.key_cmd = f"cmd_{time.time()}"
        st.session_state.key_bl = f"bl_{time.time()}"
        st.session_state.historique = []
        st.rerun()
    
    st.markdown("---")
    
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
    
    # Section DESADV
    if st.session_state.user_web_access:
        st.markdown("---")
        st.header("🌐 Vérification DESADV")
        
        if st.button("🔍 Vérifier les DESADV", use_container_width=True, type="secondary"):
            with st.spinner("🔄 Connexion aux plateformes EDI..."):
                auchan_data, auchan_date, auchan_status = fetch_desadv_from_auchan()
                edi1_data, edi1_date, edi1_status = fetch_desadv_from_edi1()
                
                st.session_state.desadv_auchan = {
                    "data": auchan_data,
                    "date": auchan_date,
                    "status": auchan_status
                }
                st.session_state.desadv_edi1 = {
                    "data": edi1_data,
                    "date": edi1_date,
                    "status": edi1_status
                }
            st.rerun()
        
        if hasattr(st.session_state, 'desadv_auchan') or hasattr(st.session_state, 'desadv_edi1'):
            total_desadv = 0
            total_montant = 0
            
            if hasattr(st.session_state, 'desadv_auchan'):
                auchan = st.session_state.desadv_auchan
                if auchan["data"]:
                    total_desadv += len(auchan["data"])
                    total_montant += sum([d["montant_total"] for d in auchan["data"]])
            
            if hasattr(st.session_state, 'desadv_edi1'):
                edi1 = st.session_state.desadv_edi1
                if edi1["data"]:
                    total_desadv += len(edi1["data"])
                    total_montant += sum([d["montant_total"] for d in edi1["data"]])
            
            if total_desadv > 0:
                st.success(f"✅ **{total_desadv} DESADV** à faire")
                st.metric("Montant total", f"{total_montant:,.2f} €")
                
                if st.button("📋 Voir les détails", use_container_width=True):
                    st.session_state.show_desadv_details = True
                    st.rerun()
                
                if st.button("🗑️ Effacer", use_container_width=True):
                    if hasattr(st.session_state, 'desadv_auchan'):
                        delattr(st.session_state, 'desadv_auchan')
                    if hasattr(st.session_state, 'desadv_edi1'):
                        delattr(st.session_state, 'desadv_edi1')
                    st.session_state.show_desadv_details = False
                    st.rerun()
            else:
                st.info("Aucun DESADV à traiter")
    else:
        st.markdown("---")
        st.info("🔒 Vérification DESADV\nAccès non autorisé pour votre compte")

# ============================================
# MAIN CONTENT
# ============================================

# Affichage détails DESADV
if st.session_state.show_desadv_details:
    st.markdown("---")
    st.markdown("## 🌐 Détails des DESADV à traiter")
    
    col1, col2 = st.columns(2)
    
    # AUCHAN
    with col1:
        st.markdown("### 🔵 AUCHAN ATGPED")
        if hasattr(st.session_state, 'desadv_auchan'):
            auchan = st.session_state.desadv_auchan
            
            if auchan["status"] == "simulation":
                st.warning("⚠️ Données de simulation (connexion impossible)")
            elif auchan["status"] != "success":
                st.error(f"❌ {auchan['status']}")
            
            if auchan["data"]:
                st.success(f"📅 Livraison: **{auchan['date']}**")
                st.metric("Nombre de DESADV", len(auchan["data"]))
                st.metric("Montant total", f"{sum([d['montant_total'] for d in auchan['data']]):,.2f} €")
                
                st.markdown("---")
                for idx, desadv in enumerate(auchan["data"], 1):
                    with st.expander(f"📦 {idx}. {desadv['entrepot']}", expanded=False):
                        st.metric("Montant", f"{desadv['montant_total']:,.2f} €")
                        st.write(f"**{desadv['nb_commandes']} commande(s):**")
                        st.write(", ".join(desadv['commandes']))
            else:
                st.info("✅ Aucun DESADV Auchan à traiter")
        else:
            st.info("Aucune donnée disponible")
    
    # EDI1
    with col2:
        st.markdown("### 🟢 EDI1 (ITM, CSD, etc.)")
        if hasattr(st.session_state, 'desadv_edi1'):
            edi1 = st.session_state.desadv_edi1
            
            if edi1["status"] == "simulation":
                st.warning("⚠️ Données de simulation (connexion impossible)")
            elif edi1["status"] != "success":
                st.error(f"❌ {edi1['status']}")
            
            if edi1["data"]:
                st.success(f"📅 Livraison: **{edi1['date']}**")
                st.metric("Nombre de DESADV", len(edi1["data"]))
                st.metric("Montant total", f"{sum([d['montant_total'] for d in edi1['data']]):,.2f} €")
                
                st.markdown("---")
                for idx, desadv in enumerate(edi1["data"], 1):
                    with st.expander(f"📦 {idx}. {desadv['entrepot']}", expanded=False):
                        st.metric("Montant", f"{desadv['montant_total']:,.2f} €")
                        st.write(f"**{desadv['nb_commandes']} commande(s):**")
                        st.write(", ".join(desadv['commandes']))
            else:
                st.info("✅ Aucun DESADV EDI1 à traiter")
        else:
            st.info("Aucune donnée disponible")
    
    if st.button("❌ Fermer les détails", type="secondary"):
        st.session_state.show_desadv_details = False
        st.rerun()
    
    st.markdown("---")

# Boutons principaux
col_btn1, col_btn2 = st.columns([3, 1])

with col_btn1:
    comparison_btn = st.button("🔍 Lancer la comparaison", use_container_width=True, type="primary")

with col_btn2:
    if st.button("❓ Aide", use_container_width=True):
        st.session_state.show_help = "guide"
        st.rerun()

if comparison_btn:
    if not commande_files or not bl_files:
        st.error("⚠️ Veuillez téléverser des commandes ET des bons de livraison.")
        st.stop()
    
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

# Affichage des résultats
if st.session_state.historique:
    latest = st.session_state.historique[-1]
    results = latest["results"]
    commandes_dict = latest["commandes_dict"]
    bls_dict = latest["bls_dict"]
    hide_unmatched = latest["hide_unmatched"]
    
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
                if val == "OK":
                    return "background-color: #d4edda"
                if val == "QTY_DIFF":
                    return "background-color: #fff3cd"
                if val == "MISSING_IN_BL":
                    return "background-color: #f8d7da"
                return ""
            
            st.dataframe(
                df.style.applymap(color_status, subset=["status"]),
                use_container_width=True,
                height=400
            )
    
    st.markdown("---")
    st.markdown("### 📥 Export")
    
    output = io.BytesIO()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"Comparaison_{timestamp}.xlsx"
    
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
                if row.get('status') == 'OK':
                    worksheet.set_row(excel_row, None, ok_format)
                elif row.get('status') == 'QTY_DIFF':
                    worksheet.set_row(excel_row, None, diff_format)
                elif row.get('status') == 'MISSING_IN_BL':
                    worksheet.set_row(excel_row, None, miss_format)
        
        summary_data = {
            'Commande': [],
            'Taux de service (%)': [],
            'Qté commandée': [],
            'Qté livrée': [],
            'Qté manquante': [],
            'Articles OK': [],
            'Articles différence': [],
            'Articles manquants': []
        }
        
        for order_num, df in results.items():
            total_bl = df["qte_bl"].sum() if "qte_bl" in df.columns else 0
            if hide_unmatched and total_bl == 0:
                continue
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
    
    st.markdown("---")
    st.markdown("### 📊 Vue d'ensemble")
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.markdown(f"""
        <div class="kpi-card success-card">
            <div class="kpi-label">Taux de service global</div>
            <div class="kpi-value">{taux_service_global:.1f}%</div>
        </div>
        """, unsafe_allow_html=True)
    with col2:
        st.markdown(f"""
        <div class="kpi-card info-card">
            <div class="kpi-label">Total commandé</div>
            <div class="kpi-value">{int(total_commande)}</div>
        </div>
        """, unsafe_allow_html=True)
    with col3:
        st.markdown(f"""
        <div class="kpi-card">
            <div class="kpi-label">Total livré</div>
            <div class="kpi-value">{int(total_livre)}</div>
        </div>
        """, unsafe_allow_html=True)
    with col4:
        st.markdown(f"""
        <div class="kpi-card warning-card">
            <div class="kpi-label">Total manquant</div>
            <div class="kpi-value">{int(total_manquant)}</div>
        </div>
        """, unsafe_allow_html=True)
    
    st.markdown("<br>", unsafe_allow_html=True)
    
    col1, col2 = st.columns(2)
    if PLOTLY_AVAILABLE:
        with col1:
            status_data = pd.DataFrame({
                'Statut': ['✅ OK', '⚠️ Différence', '❌ Manquant'],
                'Nombre': [total_articles_ok, total_articles_diff, total_articles_missing]
            })
            fig_status = px.pie(
                status_data, 
                values='Nombre', 
                names='Statut',
                title='Répartition des articles',
                color_discrete_sequence=['#38ef7d', '#f5576c', '#ff6b6b']
            )
            fig_status.update_traces(textposition='inside', textinfo='percent+label')
            st.plotly_chart(fig_status, use_container_width=True)
        
        with col2:
            service_rates = []
            for order_num, df in results.items():
                if not order_included(df):
                    continue
                total_cmd = df["qte_commande"].sum()
                total_bl = df["qte_bl"].sum()
                rate = (total_bl / total_cmd * 100) if total_cmd > 0 else 0
                service_rates.append({
                    'Commande': str(order_num),
                    'Taux de service': rate
                })
            df_service = pd.DataFrame(service_rates)
            if not df_service.empty:
                fig_service = go.Figure(data=[
                    go.Bar(
                        x=df_service['Commande'],
                        y=df_service['Taux de service'],
                        marker=dict(
                            color=df_service['Taux de service'],
                            colorscale=[[0, '#ff6b6b'], [0.5, '#ffd93d'], [1, '#38ef7d']],
                            cmin=0,
                            cmax=100,
                            showscale=False
                        ),
                        text=[f"{v:.1f}%" for v in df_service['Taux de service']],
                        textposition='outside'
                    )
                ])
                fig_service.update_layout(
                    title='Taux de service par commande',
                    xaxis_title='N° Commande',
                    yaxis_title='Taux de service (%)',
                    yaxis_range=[0, 110],
                    showlegend=False,
                    xaxis=dict(type='category')
                )
                st.plotly_chart(fig_service, use_container_width=True)
            else:
                st.info("Aucune commande à afficher.")
    else:
        with col1:
            st.metric("Articles OK", total_articles_ok)
            st.metric("Articles avec différence", total_articles_diff)
            st.metric("Articles manquants", total_articles_missing)
        with col2:
            for order_num, df in results.items():
                if not order_included(df):
                    continue
                total_cmd = df["qte_commande"].sum()
                total_bl = df["qte_bl"].sum()
                rate = (total_bl / total_cmd * 100) if total_cmd > 0 else 0
                st.metric(f"Commande {order_num}", f"{rate:.1f}%")
    
    tabs = st.tabs(["📈 Statistiques", "🏆 Top produits"])
    
    with tabs[0]:
        st.markdown("### 📈 Articles manquants par code article")
        missing_by_code = {}
        for order_num, df in results.items():
            if not order_included(df):
                continue
            missing = df[df["status"] == "MISSING_IN_BL"]
            for _, row in missing.iterrows():
                code = row["code_article"]
                if code not in missing_by_code:
                    missing_by_code[code] = {"Code article": code, "Qté totale manquante": 0}
                missing_by_code[code]["Qté totale manquante"] += int(row["qte_commande"])
        
        if missing_by_code:
            df_missing = pd.DataFrame(list(missing_by_code.values()))
            df_missing = df_missing.sort_values("Qté totale manquante", ascending=False).head(10)
            st.markdown("#### Top 10 des codes articles manquants")
            st.dataframe(df_missing, use_container_width=True, hide_index=True)
        else:
            st.success("✅ Aucun article manquant !")
    
    with tabs[1]:
        st.markdown("### 🏆 Classement des produits")
        all_products = []
        for order_num, df in results.items():
            if not order_included(df):
                continue
            for _, row in df.iterrows():
                all_products.append({
                    "Code article": row["code_article"],
                    "EAN": row["ref"],
                    "Qté commandée": int(row["qte_commande"]),
                    "Qté livrée": int(row["qte_bl"])
                })
        
        if all_products:
            df_products = pd.DataFrame(all_products)
        else:
            df_products = pd.DataFrame(columns=["Code article", "EAN", "Qté commandée", "Qté livrée"])
        
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("#### 📦 Top 10 commandés")
            if not df_products.empty:
                top_cmd = df_products.groupby("Code article")["Qté commandée"].sum().sort_values(ascending=False).head(10)
                st.dataframe(top_cmd.reset_index(), use_container_width=True, hide_index=True)
            else:
                st.info("Aucun produit à afficher.")
        with col2:
            st.markdown("#### 📋 Top 10 livrés")
            if not df_products.empty:
                top_livre = df_products.groupby("Code article")["Qté livrée"].sum().sort_values(ascending=False).head(10)
                st.dataframe(top_livre.reset_index(), use_container_width=True, hide_index=True)
            else:
                st.info("Aucun produit à afficher.")
else:
    st.info("👆 Téléversez vos fichiers et lancez la comparaison pour commencer")

# Modal d'aide
if st.session_state.show_help == "guide":
    st.markdown("---")
    st.markdown("## 📖 Guide d'utilisation")
    
    with st.expander("🚀 Démarrage rapide", expanded=True):
        st.markdown("""
        ### Étapes principales :
        1. **Téléversez vos PDF** dans la barre latérale gauche
           - 📦 Commandes client (un ou plusieurs)
           - 📋 Bons de livraison (un ou plusieurs)
        
        2. **Cliquez sur "🔍 Lancer la comparaison"**
        
        3. **Consultez les résultats** :
           - Détails par commande
           - Rapport Excel téléchargeable
           - Statistiques et KPIs
        """)
    
    with st.expander("📊 Comprendre les résultats"):
        st.markdown("""
        ### Codes couleur :
        - 🟢 **OK** : Quantité commandée = Quantité livrée
        - 🟡 **QTY_DIFF** : Différence de quantité
        - 🔴 **MISSING_IN_BL** : Article non trouvé dans le BL
        
        ### KPIs :
        - **Taux de service** : (Qté livrée / Qté commandée) × 100
        - **Total manquant** : Somme des articles non livrés
        """)
    
    with st.expander("⚙️ Options avancées"):
        st.markdown("""
        ### Masquer les commandes sans correspondance
        Exclut de l'export Excel les commandes qui n'ont pas de BL correspondant.
        
        ### Historique
        Toutes vos comparaisons sont sauvegardées temporairement dans la session.
        
        ### Vérification DESADV (Admin uniquement)
        Connecte automatiquement aux sites EDI pour récupérer les commandes à traiter.
        """)
    
    with st.expander("👥 Gestion des utilisateurs (Admin)"):
        st.markdown("""
        ### Ajouter un nouvel utilisateur :
        
        Dans le code, section `USERS_DB`, ajoutez :
        ```python
        "nom_utilisateur": {
            "password": "mot_de_passe",
            "role": "admin",  # ou "user"
            "web_access": True  # ou False
        }
        ```
        
        **Paramètres :**
        - `role: "admin"` → Accès complet
        - `role: "user"` → Accès limité
        - `web_access: True` → Peut vérifier les DESADV
        - `web_access: False` → Pas d'accès aux plateformes EDI
        """)
    
    if st.button("✅ Compris, retour à l'outil", type="primary"):
        st.session_state.show_help = False
        st.rerun()

st.markdown("""
<div style='text-align: center; margin-top: 40px; font-size: 18px; color: #888;'>
    ⭐⭐⭐⭐⭐<br>
    <strong>Powered by IC - 2025</strong>
</div>
""", unsafe_allow_html=True), text):
                        try:
                            montant = float(text)
                            break
                        except:
                            pass
                
                # Entrepôt (généralement colonne 2 ou 3)
                entrepot = cols[2].text.strip() if len(cols) > 2 else ""
                
                if date_livraison == tomorrow and montant:
                    commandes_brutes.append({
                        "numero": numero,
                        "entrepot": entrepot,
                        "montant": montant,
                        "date_livraison": date_livraison
                    })
                    
            except Exception as e:
                st.warning(f"⚠️ Erreur ligne {idx}: {str(e)}")
                continue
        
        st.info(f"📊 {len(commandes_brutes)} commandes trouvées pour {tomorrow}")
        
        if not commandes_brutes:
            return [], tomorrow, "ℹ️ Aucune commande pour demain"
        
        # Regrouper par entrepôt
        entrepots = {}
        for cmd in commandes_brutes:
            entrepot = cmd["entrepot"]
            if entrepot not in entrepots:
                entrepots[entrepot] = {"montant_total": 0, "commandes": []}
            entrepots[entrepot]["montant_total"] += cmd["montant"]
            entrepots[entrepot]["commandes"].append(cmd["numero"])
        
        # Filtrer >= 850€
        desadv_a_faire = []
        for entrepot, data in entrepots.items():
            if data["montant_total"] >= 850:
                desadv_a_faire.append({
                    "entrepot": entrepot,
                    "montant_total": data["montant_total"],
                    "nb_commandes": len(data["commandes"]),
                    "commandes": data["commandes"]
                })
        
        desadv_a_faire.sort(key=lambda x: x["montant_total"], reverse=True)
        return desadv_a_faire, tomorrow, "success"
        
    except Exception as e:
        import traceback
        error_detail = traceback.format_exc()
        st.error(f"❌ Erreur générale: {str(e)}")
        with st.expander("🔍 Détails de l'erreur"):
            st.code(error_detail)
        return [], tomorrow, f"❌ Erreur: {str(e)}"

def fetch_desadv_from_edi1_real():
    """
    Connexion au site EDI1 (ed1.atgped.net)
    Clients filtrés: INTERMARCHE, DEPOT CSD ALBY SUR CHERAN, ITM LUXEMONT-ET-VILLOTTE
    """
    try:
        import requests
        from bs4 import BeautifulSoup
        import os
        
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%d/%m/%Y")
        session = requests.Session()
        login_url = "https://ed1.atgped.net/gui.php"
        
        try:
            username = st.secrets.get("EDI_USERNAME", "")
            password = st.secrets.get("EDI_PASSWORD", "")
        except:
            username = os.getenv("EDI_USERNAME", "")
            password = os.getenv("EDI_PASSWORD", "")
        
        if not username or not password:
            return [], tomorrow, "Identifiants non configurés"
        
        login_data = {
            "username": username,
            "password": password,
            "action": "login"
        }
        
        login_response = session.post(login_url, data=login_data, timeout=15)
        
        if "Liste des commandes" not in login_response.text and "Documents" not in login_response.text:
            return [], tomorrow, "Échec de connexion"
        
        commandes_url = "https://ed1.atgped.net/gui.php"
        params = {
            "query": "documents_commandes_liste",
            "page": "documents_commandes_liste",
            "pos": "0",
            "acces_page": "1",
            "lines_per_page": "1000",
            "doNumero": "",
            "RaisonSocialeSiegeSoc": "",
            "livrerA": "",
        }
        
        response = session.get(commandes_url, params=params, timeout=15)
        soup = BeautifulSoup(response.content, 'html.parser')
        
        clients_autorises = [
            "INTERMARCHE",
            "DEPOT CSD ALBY SUR CHERAN",
            "ITM LUXEMONT-ET-VILLOTTE"
        ]
        
        commandes_brutes = []
        table = soup.find('table')
        if not table:
            return [], tomorrow, "Aucune commande trouvée"
        
        rows = table.find_all('tr')[1:]
        
        for row in rows:
            cols = row.find_all('td')
            if len(cols) < 7:
                continue
            
            try:
                numero = cols[0].text.strip()
                client = cols[1].text.strip()
                entrepot = cols[2].text.strip()
                date_livraison = cols[4].text.strip()
                montant_text = cols[6].text.strip()
                montant = float(montant_text.replace(" ", "").replace(",", "."))
                
                if any(client_autorise in client.upper() for client_autorise in clients_autorises):
                    if date_livraison == tomorrow:
                        commandes_brutes.append({
                            "numero": numero,
                            "client": client,
                            "entrepot": entrepot,
                            "montant": montant,
                            "date_livraison": date_livraison
                        })
            except:
                continue
        
        clients = {}
        for cmd in commandes_brutes:
            client = cmd["client"]
            if client not in clients:
                clients[client] = {"montant_total": 0, "commandes": []}
            clients[client]["montant_total"] += cmd["montant"]
            clients[client]["commandes"].append(cmd["numero"])
        
        desadv_a_faire = []
        for client, data in clients.items():
            if data["montant_total"] >= 850:
                desadv_a_faire.append({
                    "entrepot": client,
                    "montant_total": data["montant_total"],
                    "nb_commandes": len(data["commandes"]),
                    "commandes": data["commandes"]
                })
        
        desadv_a_faire.sort(key=lambda x: x["montant_total"], reverse=True)
        return desadv_a_faire, tomorrow, "success"
        
    except Exception as e:
        return [], tomorrow, f"Erreur: {str(e)}"

def fetch_desadv_from_auchan():
    """Version avec fallback pour Auchan"""
    result, date, status = fetch_desadv_from_auchan_real()
    
    if status != "success":
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%d/%m/%Y")
        commandes_brutes = [
            {"numero": "03385063", "entrepot": "PFI VENDENHEIM", "montant": 5432.70, "date_livraison": tomorrow},
            {"numero": "03311038", "entrepot": "APPRO PFI LE COUDRAY", "montant": 3406.81, "date_livraison": tomorrow},
            {"numero": "03401873", "entrepot": "PFI CARVIN", "montant": 3226.07, "date_livraison": tomorrow},
        ]
        
        entrepots = {}
        for cmd in commandes_brutes:
            entrepot = cmd["entrepot"]
            if entrepot not in entrepots:
                entrepots[entrepot] = {"montant_total": 0, "commandes": []}
            entrepots[entrepot]["montant_total"] += cmd["montant"]
            entrepots[entrepot]["commandes"].append(cmd["numero"])
        
        desadv_a_faire = []
        for entrepot, data in entrepots.items():
            if data["montant_total"] >= 850:
                desadv_a_faire.append({
                    "entrepot": entrepot,
                    "montant_total": data["montant_total"],
                    "nb_commandes": len(data["commandes"]),
                    "commandes": data["commandes"]
                })
        
        desadv_a_faire.sort(key=lambda x: x["montant_total"], reverse=True)
        return desadv_a_faire, tomorrow, "simulation"
    
    return result, date, status

def fetch_desadv_from_edi1():
    """Version avec fallback pour EDI1"""
    result, date, status = fetch_desadv_from_edi1_real()
    
    if status != "success":
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%d/%m/%Y")
        commandes_brutes = [
            {"numero": "46961161", "client": "INTERMARCHE", "montant": 4085.29, "date_livraison": tomorrow},
            {"numero": "46962231", "client": "ITM LUXEMONT-ET-VILLOTTE", "montant": 1293.78, "date_livraison": tomorrow},
        ]
        
        clients = {}
        for cmd in commandes_brutes:
            client = cmd["client"]
            if client not in clients:
                clients[client] = {"montant_total": 0, "commandes": []}
            clients[client]["montant_total"] += cmd["montant"]
            clients[client]["commandes"].append(cmd["numero"])
        
        desadv_a_faire = []
        for client, data in clients.items():
            if data["montant_total"] >= 850:
                desadv_a_faire.append({
                    "entrepot": client,
                    "montant_total": data["montant_total"],
                    "nb_commandes": len(data["commandes"]),
                    "commandes": data["commandes"]
                })
        
        desadv_a_faire.sort(key=lambda x: x["montant_total"], reverse=True)
        return desadv_a_faire, tomorrow, "simulation"
    
    return result, date, status

# ============================================
# SIDEBAR
# ============================================

with st.sidebar:
    st.header("👤 Utilisateur")
    st.markdown(f"**{st.session_state.username}**")
    st.caption(f"Rôle: {st.session_state.user_role}")
    
    if st.button("🚪 Déconnexion", use_container_width=True):
        st.session_state.authenticated = False
        st.session_state.user_role = None
        st.session_state.user_web_access = False
        st.session_state.username = None
        st.rerun()
    
    st.markdown("---")
    st.header("📁 Fichiers")
    
    if st.button("🔄 Nouveau", use_container_width=True, type="primary"):
        st.session_state.key_cmd = f"cmd_{time.time()}"
        st.session_state.key_bl = f"bl_{time.time()}"
        st.session_state.historique = []
        st.rerun()
    
    st.markdown("---")
    
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
    
    # Section DESADV
    if st.session_state.user_web_access:
        st.markdown("---")
        st.header("🌐 Vérification DESADV")
        
        if st.button("🔍 Vérifier les DESADV", use_container_width=True, type="secondary"):
            with st.spinner("🔄 Connexion aux plateformes EDI..."):
                auchan_data, auchan_date, auchan_status = fetch_desadv_from_auchan()
                edi1_data, edi1_date, edi1_status = fetch_desadv_from_edi1()
                
                st.session_state.desadv_auchan = {
                    "data": auchan_data,
                    "date": auchan_date,
                    "status": auchan_status
                }
                st.session_state.desadv_edi1 = {
                    "data": edi1_data,
                    "date": edi1_date,
                    "status": edi1_status
                }
            st.rerun()
        
        if hasattr(st.session_state, 'desadv_auchan') or hasattr(st.session_state, 'desadv_edi1'):
            total_desadv = 0
            total_montant = 0
            
            if hasattr(st.session_state, 'desadv_auchan'):
                auchan = st.session_state.desadv_auchan
                if auchan["data"]:
                    total_desadv += len(auchan["data"])
                    total_montant += sum([d["montant_total"] for d in auchan["data"]])
            
            if hasattr(st.session_state, 'desadv_edi1'):
                edi1 = st.session_state.desadv_edi1
                if edi1["data"]:
                    total_desadv += len(edi1["data"])
                    total_montant += sum([d["montant_total"] for d in edi1["data"]])
            
            if total_desadv > 0:
                st.success(f"✅ **{total_desadv} DESADV** à faire")
                st.metric("Montant total", f"{total_montant:,.2f} €")
                
                if st.button("📋 Voir les détails", use_container_width=True):
                    st.session_state.show_desadv_details = True
                    st.rerun()
                
                if st.button("🗑️ Effacer", use_container_width=True):
                    if hasattr(st.session_state, 'desadv_auchan'):
                        delattr(st.session_state, 'desadv_auchan')
                    if hasattr(st.session_state, 'desadv_edi1'):
                        delattr(st.session_state, 'desadv_edi1')
                    st.session_state.show_desadv_details = False
                    st.rerun()
            else:
                st.info("Aucun DESADV à traiter")
    else:
        st.markdown("---")
        st.info("🔒 Vérification DESADV\nAccès non autorisé pour votre compte")

# ============================================
# MAIN CONTENT
# ============================================

# Affichage détails DESADV
if st.session_state.show_desadv_details:
    st.markdown("---")
    st.markdown("## 🌐 Détails des DESADV à traiter")
    
    col1, col2 = st.columns(2)
    
    # AUCHAN
    with col1:
        st.markdown("### 🔵 AUCHAN ATGPED")
        if hasattr(st.session_state, 'desadv_auchan'):
            auchan = st.session_state.desadv_auchan
            
            if auchan["status"] == "simulation":
                st.warning("⚠️ Données de simulation (connexion impossible)")
            elif auchan["status"] != "success":
                st.error(f"❌ {auchan['status']}")
            
            if auchan["data"]:
                st.success(f"📅 Livraison: **{auchan['date']}**")
                st.metric("Nombre de DESADV", len(auchan["data"]))
                st.metric("Montant total", f"{sum([d['montant_total'] for d in auchan['data']]):,.2f} €")
                
                st.markdown("---")
                for idx, desadv in enumerate(auchan["data"], 1):
                    with st.expander(f"📦 {idx}. {desadv['entrepot']}", expanded=False):
                        st.metric("Montant", f"{desadv['montant_total']:,.2f} €")
                        st.write(f"**{desadv['nb_commandes']} commande(s):**")
                        st.write(", ".join(desadv['commandes']))
            else:
                st.info("✅ Aucun DESADV Auchan à traiter")
        else:
            st.info("Aucune donnée disponible")
    
    # EDI1
    with col2:
        st.markdown("### 🟢 EDI1 (ITM, CSD, etc.)")
        if hasattr(st.session_state, 'desadv_edi1'):
            edi1 = st.session_state.desadv_edi1
            
            if edi1["status"] == "simulation":
                st.warning("⚠️ Données de simulation (connexion impossible)")
            elif edi1["status"] != "success":
                st.error(f"❌ {edi1['status']}")
            
            if edi1["data"]:
                st.success(f"📅 Livraison: **{edi1['date']}**")
                st.metric("Nombre de DESADV", len(edi1["data"]))
                st.metric("Montant total", f"{sum([d['montant_total'] for d in edi1['data']]):,.2f} €")
                
                st.markdown("---")
                for idx, desadv in enumerate(edi1["data"], 1):
                    with st.expander(f"📦 {idx}. {desadv['entrepot']}", expanded=False):
                        st.metric("Montant", f"{desadv['montant_total']:,.2f} €")
                        st.write(f"**{desadv['nb_commandes']} commande(s):**")
                        st.write(", ".join(desadv['commandes']))
            else:
                st.info("✅ Aucun DESADV EDI1 à traiter")
        else:
            st.info("Aucune donnée disponible")
    
    if st.button("❌ Fermer les détails", type="secondary"):
        st.session_state.show_desadv_details = False
        st.rerun()
    
    st.markdown("---")

# Boutons principaux
col_btn1, col_btn2 = st.columns([3, 1])

with col_btn1:
    comparison_btn = st.button("🔍 Lancer la comparaison", use_container_width=True, type="primary")

with col_btn2:
    if st.button("❓ Aide", use_container_width=True):
        st.session_state.show_help = "guide"
        st.rerun()

if comparison_btn:
    if not commande_files or not bl_files:
        st.error("⚠️ Veuillez téléverser des commandes ET des bons de livraison.")
        st.stop()
    
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

# Affichage des résultats
if st.session_state.historique:
    latest = st.session_state.historique[-1]
    results = latest["results"]
    commandes_dict = latest["commandes_dict"]
    bls_dict = latest["bls_dict"]
    hide_unmatched = latest["hide_unmatched"]
    
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
                if val == "OK":
                    return "background-color: #d4edda"
                if val == "QTY_DIFF":
                    return "background-color: #fff3cd"
                if val == "MISSING_IN_BL":
                    return "background-color: #f8d7da"
                return ""
            
            st.dataframe(
                df.style.applymap(color_status, subset=["status"]),
                use_container_width=True,
                height=400
            )
    
    st.markdown("---")
    st.markdown("### 📥 Export")
    
    output = io.BytesIO()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"Comparaison_{timestamp}.xlsx"
    
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
                if row.get('status') == 'OK':
                    worksheet.set_row(excel_row, None, ok_format)
                elif row.get('status') == 'QTY_DIFF':
                    worksheet.set_row(excel_row, None, diff_format)
                elif row.get('status') == 'MISSING_IN_BL':
                    worksheet.set_row(excel_row, None, miss_format)
        
        summary_data = {
            'Commande': [],
            'Taux de service (%)': [],
            'Qté commandée': [],
            'Qté livrée': [],
            'Qté manquante': [],
            'Articles OK': [],
            'Articles différence': [],
            'Articles manquants': []
        }
        
        for order_num, df in results.items():
            total_bl = df["qte_bl"].sum() if "qte_bl" in df.columns else 0
            if hide_unmatched and total_bl == 0:
                continue
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
    
    st.markdown("---")
    st.markdown("### 📊 Vue d'ensemble")
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.markdown(f"""
        <div class="kpi-card success-card">
            <div class="kpi-label">Taux de service global</div>
            <div class="kpi-value">{taux_service_global:.1f}%</div>
        </div>
        """, unsafe_allow_html=True)
    with col2:
        st.markdown(f"""
        <div class="kpi-card info-card">
            <div class="kpi-label">Total commandé</div>
            <div class="kpi-value">{int(total_commande)}</div>
        </div>
        """, unsafe_allow_html=True)
    with col3:
        st.markdown(f"""
        <div class="kpi-card">
            <div class="kpi-label">Total livré</div>
            <div class="kpi-value">{int(total_livre)}</div>
        </div>
        """, unsafe_allow_html=True)
    with col4:
        st.markdown(f"""
        <div class="kpi-card warning-card">
            <div class="kpi-label">Total manquant</div>
            <div class="kpi-value">{int(total_manquant)}</div>
        </div>
        """, unsafe_allow_html=True)
    
    st.markdown("<br>", unsafe_allow_html=True)
    
    col1, col2 = st.columns(2)
    if PLOTLY_AVAILABLE:
        with col1:
            status_data = pd.DataFrame({
                'Statut': ['✅ OK', '⚠️ Différence', '❌ Manquant'],
                'Nombre': [total_articles_ok, total_articles_diff, total_articles_missing]
            })
            fig_status = px.pie(
                status_data, 
                values='Nombre', 
                names='Statut',
                title='Répartition des articles',
                color_discrete_sequence=['#38ef7d', '#f5576c', '#ff6b6b']
            )
            fig_status.update_traces(textposition='inside', textinfo='percent+label')
            st.plotly_chart(fig_status, use_container_width=True)
        
        with col2:
            service_rates = []
            for order_num, df in results.items():
                if not order_included(df):
                    continue
                total_cmd = df["qte_commande"].sum()
                total_bl = df["qte_bl"].sum()
                rate = (total_bl / total_cmd * 100) if total_cmd > 0 else 0
                service_rates.append({
                    'Commande': str(order_num),
                    'Taux de service': rate
                })
            df_service = pd.DataFrame(service_rates)
            if not df_service.empty:
                fig_service = go.Figure(data=[
                    go.Bar(
                        x=df_service['Commande'],
                        y=df_service['Taux de service'],
                        marker=dict(
                            color=df_service['Taux de service'],
                            colorscale=[[0, '#ff6b6b'], [0.5, '#ffd93d'], [1, '#38ef7d']],
                            cmin=0,
                            cmax=100,
                            showscale=False
                        ),
                        text=[f"{v:.1f}%" for v in df_service['Taux de service']],
                        textposition='outside'
                    )
                ])
                fig_service.update_layout(
                    title='Taux de service par commande',
                    xaxis_title='N° Commande',
                    yaxis_title='Taux de service (%)',
                    yaxis_range=[0, 110],
                    showlegend=False,
                    xaxis=dict(type='category')
                )
                st.plotly_chart(fig_service, use_container_width=True)
            else:
                st.info("Aucune commande à afficher.")
    else:
        with col1:
            st.metric("Articles OK", total_articles_ok)
            st.metric("Articles avec différence", total_articles_diff)
            st.metric("Articles manquants", total_articles_missing)
        with col2:
            for order_num, df in results.items():
                if not order_included(df):
                    continue
                total_cmd = df["qte_commande"].sum()
                total_bl = df["qte_bl"].sum()
                rate = (total_bl / total_cmd * 100) if total_cmd > 0 else 0
                st.metric(f"Commande {order_num}", f"{rate:.1f}%")
    
    tabs = st.tabs(["📈 Statistiques", "🏆 Top produits"])
    
    with tabs[0]:
        st.markdown("### 📈 Articles manquants par code article")
        missing_by_code = {}
        for order_num, df in results.items():
            if not order_included(df):
                continue
            missing = df[df["status"] == "MISSING_IN_BL"]
            for _, row in missing.iterrows():
                code = row["code_article"]
                if code not in missing_by_code:
                    missing_by_code[code] = {"Code article": code, "Qté totale manquante": 0}
                missing_by_code[code]["Qté totale manquante"] += int(row["qte_commande"])
        
        if missing_by_code:
            df_missing = pd.DataFrame(list(missing_by_code.values()))
            df_missing = df_missing.sort_values("Qté totale manquante", ascending=False).head(10)
            st.markdown("#### Top 10 des codes articles manquants")
            st.dataframe(df_missing, use_container_width=True, hide_index=True)
        else:
            st.success("✅ Aucun article manquant !")
    
    with tabs[1]:
        st.markdown("### 🏆 Classement des produits")
        all_products = []
        for order_num, df in results.items():
            if not order_included(df):
                continue
            for _, row in df.iterrows():
                all_products.append({
                    "Code article": row["code_article"],
                    "EAN": row["ref"],
                    "Qté commandée": int(row["qte_commande"]),
                    "Qté livrée": int(row["qte_bl"])
                })
        
        if all_products:
            df_products = pd.DataFrame(all_products)
        else:
            df_products = pd.DataFrame(columns=["Code article", "EAN", "Qté commandée", "Qté livrée"])
        
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("#### 📦 Top 10 commandés")
            if not df_products.empty:
                top_cmd = df_products.groupby("Code article")["Qté commandée"].sum().sort_values(ascending=False).head(10)
                st.dataframe(top_cmd.reset_index(), use_container_width=True, hide_index=True)
            else:
                st.info("Aucun produit à afficher.")
        with col2:
            st.markdown("#### 📋 Top 10 livrés")
            if not df_products.empty:
                top_livre = df_products.groupby("Code article")["Qté livrée"].sum().sort_values(ascending=False).head(10)
                st.dataframe(top_livre.reset_index(), use_container_width=True, hide_index=True)
            else:
                st.info("Aucun produit à afficher.")
else:
    st.info("👆 Téléversez vos fichiers et lancez la comparaison pour commencer")

# Modal d'aide
if st.session_state.show_help == "guide":
    st.markdown("---")
    st.markdown("## 📖 Guide d'utilisation")
    
    with st.expander("🚀 Démarrage rapide", expanded=True):
        st.markdown("""
        ### Étapes principales :
        1. **Téléversez vos PDF** dans la barre latérale gauche
           - 📦 Commandes client (un ou plusieurs)
           - 📋 Bons de livraison (un ou plusieurs)
        
        2. **Cliquez sur "🔍 Lancer la comparaison"**
        
        3. **Consultez les résultats** :
           - Détails par commande
           - Rapport Excel téléchargeable
           - Statistiques et KPIs
        """)
    
    with st.expander("📊 Comprendre les résultats"):
        st.markdown("""
        ### Codes couleur :
        - 🟢 **OK** : Quantité commandée = Quantité livrée
        - 🟡 **QTY_DIFF** : Différence de quantité
        - 🔴 **MISSING_IN_BL** : Article non trouvé dans le BL
        
        ### KPIs :
        - **Taux de service** : (Qté livrée / Qté commandée) × 100
        - **Total manquant** : Somme des articles non livrés
        """)
    
    with st.expander("⚙️ Options avancées"):
        st.markdown("""
        ### Masquer les commandes sans correspondance
        Exclut de l'export Excel les commandes qui n'ont pas de BL correspondant.
        
        ### Historique
        Toutes vos comparaisons sont sauvegardées temporairement dans la session.
        
        ### Vérification DESADV (Admin uniquement)
        Connecte automatiquement aux sites EDI pour récupérer les commandes à traiter.
        """)
    
    with st.expander("👥 Gestion des utilisateurs (Admin)"):
        st.markdown("""
        ### Ajouter un nouvel utilisateur :
        
        Dans le code, section `USERS_DB`, ajoutez :
        ```python
        "nom_utilisateur": {
            "password": "mot_de_passe",
            "role": "admin",  # ou "user"
            "web_access": True  # ou False
        }
        ```
        
        **Paramètres :**
        - `role: "admin"` → Accès complet
        - `role: "user"` → Accès limité
        - `web_access: True` → Peut vérifier les DESADV
        - `web_access: False` → Pas d'accès aux plateformes EDI
        """)
    
    if st.button("✅ Compris, retour à l'outil", type="primary"):
        st.session_state.show_help = False
        st.rerun()

st.markdown("""
<div style='text-align: center; margin-top: 40px; font-size: 18px; color: #888;'>
    ⭐⭐⭐⭐⭐<br>
    <strong>Powered by IC - 2025</strong>
</div>
""", unsafe_allow_html=True), text):
                        try:
                            montant = float(text)
                            break
                        except:
                            pass
                
                if date_livraison == tomorrow and montant:
                    commandes_brutes.append({
                        "numero": numero,
                        "client": client,
                        "montant": montant,
                        "date_livraison": date_livraison
                    })
                    
            except Exception as e:
                continue
        
        st.info(f"📊 EDI1 - {len(commandes_brutes)} commandes trouvées pour {tomorrow}")
        
        if not commandes_brutes:
            return [], tomorrow, "ℹ️ Aucune commande EDI1 pour demain"
        
        clients = {}
        for cmd in commandes_brutes:
            client = cmd["client"]
            if client not in clients:
                clients[client] = {"montant_total": 0, "commandes": []}
            clients[client]["montant_total"] += cmd["montant"]
            clients[client]["commandes"].append(cmd["numero"])
        
        desadv_a_faire = []
        for client, data in clients.items():
            if data["montant_total"] >= 850:
                desadv_a_faire.append({
                    "entrepot": client,
                    "montant_total": data["montant_total"],
                    "nb_commandes": len(data["commandes"]),
                    "commandes": data["commandes"]
                })
        
        desadv_a_faire.sort(key=lambda x: x["montant_total"], reverse=True)
        return desadv_a_faire, tomorrow, "success"
        
    except Exception as e:
        import traceback
        error_detail = traceback.format_exc()
        st.error(f"❌ EDI1 - Erreur: {str(e)}")
        with st.expander("🔍 Détails erreur EDI1"):
            st.code(error_detail)
        return [], tomorrow, f"❌ Erreur: {str(e)}"

def fetch_desadv_from_auchan():
    """Version avec fallback pour Auchan"""
    result, date, status = fetch_desadv_from_auchan_real()
    
    if status != "success":
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%d/%m/%Y")
        commandes_brutes = [
            {"numero": "03385063", "entrepot": "PFI VENDENHEIM", "montant": 5432.70, "date_livraison": tomorrow},
            {"numero": "03311038", "entrepot": "APPRO PFI LE COUDRAY", "montant": 3406.81, "date_livraison": tomorrow},
            {"numero": "03401873", "entrepot": "PFI CARVIN", "montant": 3226.07, "date_livraison": tomorrow},
        ]
        
        entrepots = {}
        for cmd in commandes_brutes:
            entrepot = cmd["entrepot"]
            if entrepot not in entrepots:
                entrepots[entrepot] = {"montant_total": 0, "commandes": []}
            entrepots[entrepot]["montant_total"] += cmd["montant"]
            entrepots[entrepot]["commandes"].append(cmd["numero"])
        
        desadv_a_faire = []
        for entrepot, data in entrepots.items():
            if data["montant_total"] >= 850:
                desadv_a_faire.append({
                    "entrepot": entrepot,
                    "montant_total": data["montant_total"],
                    "nb_commandes": len(data["commandes"]),
                    "commandes": data["commandes"]
                })
        
        desadv_a_faire.sort(key=lambda x: x["montant_total"], reverse=True)
        return desadv_a_faire, tomorrow, "simulation"
    
    return result, date, status

def fetch_desadv_from_edi1():
    """Version avec fallback pour EDI1"""
    result, date, status = fetch_desadv_from_edi1_real()
    
    if status != "success":
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%d/%m/%Y")
        commandes_brutes = [
            {"numero": "46961161", "client": "INTERMARCHE", "montant": 4085.29, "date_livraison": tomorrow},
            {"numero": "46962231", "client": "ITM LUXEMONT-ET-VILLOTTE", "montant": 1293.78, "date_livraison": tomorrow},
        ]
        
        clients = {}
        for cmd in commandes_brutes:
            client = cmd["client"]
            if client not in clients:
                clients[client] = {"montant_total": 0, "commandes": []}
            clients[client]["montant_total"] += cmd["montant"]
            clients[client]["commandes"].append(cmd["numero"])
        
        desadv_a_faire = []
        for client, data in clients.items():
            if data["montant_total"] >= 850:
                desadv_a_faire.append({
                    "entrepot": client,
                    "montant_total": data["montant_total"],
                    "nb_commandes": len(data["commandes"]),
                    "commandes": data["commandes"]
                })
        
        desadv_a_faire.sort(key=lambda x: x["montant_total"], reverse=True)
        return desadv_a_faire, tomorrow, "simulation"
    
    return result, date, status

# ============================================
# SIDEBAR
# ============================================

with st.sidebar:
    st.header("👤 Utilisateur")
    st.markdown(f"**{st.session_state.username}**")
    st.caption(f"Rôle: {st.session_state.user_role}")
    
    if st.button("🚪 Déconnexion", use_container_width=True):
        st.session_state.authenticated = False
        st.session_state.user_role = None
        st.session_state.user_web_access = False
        st.session_state.username = None
        st.rerun()
    
    st.markdown("---")
    st.header("📁 Fichiers")
    
    if st.button("🔄 Nouveau", use_container_width=True, type="primary"):
        st.session_state.key_cmd = f"cmd_{time.time()}"
        st.session_state.key_bl = f"bl_{time.time()}"
        st.session_state.historique = []
        st.rerun()
    
    st.markdown("---")
    
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
    
    # Section DESADV
    if st.session_state.user_web_access:
        st.markdown("---")
        st.header("🌐 Vérification DESADV")
        
        if st.button("🔍 Vérifier les DESADV", use_container_width=True, type="secondary"):
            with st.spinner("🔄 Connexion aux plateformes EDI..."):
                auchan_data, auchan_date, auchan_status = fetch_desadv_from_auchan()
                edi1_data, edi1_date, edi1_status = fetch_desadv_from_edi1()
                
                st.session_state.desadv_auchan = {
                    "data": auchan_data,
                    "date": auchan_date,
                    "status": auchan_status
                }
                st.session_state.desadv_edi1 = {
                    "data": edi1_data,
                    "date": edi1_date,
                    "status": edi1_status
                }
            st.rerun()
        
        if hasattr(st.session_state, 'desadv_auchan') or hasattr(st.session_state, 'desadv_edi1'):
            total_desadv = 0
            total_montant = 0
            
            if hasattr(st.session_state, 'desadv_auchan'):
                auchan = st.session_state.desadv_auchan
                if auchan["data"]:
                    total_desadv += len(auchan["data"])
                    total_montant += sum([d["montant_total"] for d in auchan["data"]])
            
            if hasattr(st.session_state, 'desadv_edi1'):
                edi1 = st.session_state.desadv_edi1
                if edi1["data"]:
                    total_desadv += len(edi1["data"])
                    total_montant += sum([d["montant_total"] for d in edi1["data"]])
            
            if total_desadv > 0:
                st.success(f"✅ **{total_desadv} DESADV** à faire")
                st.metric("Montant total", f"{total_montant:,.2f} €")
                
                if st.button("📋 Voir les détails", use_container_width=True):
                    st.session_state.show_desadv_details = True
                    st.rerun()
                
                if st.button("🗑️ Effacer", use_container_width=True):
                    if hasattr(st.session_state, 'desadv_auchan'):
                        delattr(st.session_state, 'desadv_auchan')
                    if hasattr(st.session_state, 'desadv_edi1'):
                        delattr(st.session_state, 'desadv_edi1')
                    st.session_state.show_desadv_details = False
                    st.rerun()
            else:
                st.info("Aucun DESADV à traiter")
    else:
        st.markdown("---")
        st.info("🔒 Vérification DESADV\nAccès non autorisé pour votre compte")

# ============================================
# MAIN CONTENT
# ============================================

# Affichage détails DESADV
if st.session_state.show_desadv_details:
    st.markdown("---")
    st.markdown("## 🌐 Détails des DESADV à traiter")
    
    col1, col2 = st.columns(2)
    
    # AUCHAN
    with col1:
        st.markdown("### 🔵 AUCHAN ATGPED")
        if hasattr(st.session_state, 'desadv_auchan'):
            auchan = st.session_state.desadv_auchan
            
            if auchan["status"] == "simulation":
                st.warning("⚠️ Données de simulation (connexion impossible)")
            elif auchan["status"] != "success":
                st.error(f"❌ {auchan['status']}")
            
            if auchan["data"]:
                st.success(f"📅 Livraison: **{auchan['date']}**")
                st.metric("Nombre de DESADV", len(auchan["data"]))
                st.metric("Montant total", f"{sum([d['montant_total'] for d in auchan['data']]):,.2f} €")
                
                st.markdown("---")
                for idx, desadv in enumerate(auchan["data"], 1):
                    with st.expander(f"📦 {idx}. {desadv['entrepot']}", expanded=False):
                        st.metric("Montant", f"{desadv['montant_total']:,.2f} €")
                        st.write(f"**{desadv['nb_commandes']} commande(s):**")
                        st.write(", ".join(desadv['commandes']))
            else:
                st.info("✅ Aucun DESADV Auchan à traiter")
        else:
            st.info("Aucune donnée disponible")
    
    # EDI1
    with col2:
        st.markdown("### 🟢 EDI1 (ITM, CSD, etc.)")
        if hasattr(st.session_state, 'desadv_edi1'):
            edi1 = st.session_state.desadv_edi1
            
            if edi1["status"] == "simulation":
                st.warning("⚠️ Données de simulation (connexion impossible)")
            elif edi1["status"] != "success":
                st.error(f"❌ {edi1['status']}")
            
            if edi1["data"]:
                st.success(f"📅 Livraison: **{edi1['date']}**")
                st.metric("Nombre de DESADV", len(edi1["data"]))
                st.metric("Montant total", f"{sum([d['montant_total'] for d in edi1['data']]):,.2f} €")
                
                st.markdown("---")
                for idx, desadv in enumerate(edi1["data"], 1):
                    with st.expander(f"📦 {idx}. {desadv['entrepot']}", expanded=False):
                        st.metric("Montant", f"{desadv['montant_total']:,.2f} €")
                        st.write(f"**{desadv['nb_commandes']} commande(s):**")
                        st.write(", ".join(desadv['commandes']))
            else:
                st.info("✅ Aucun DESADV EDI1 à traiter")
        else:
            st.info("Aucune donnée disponible")
    
    if st.button("❌ Fermer les détails", type="secondary"):
        st.session_state.show_desadv_details = False
        st.rerun()
    
    st.markdown("---")

# Boutons principaux
col_btn1, col_btn2 = st.columns([3, 1])

with col_btn1:
    comparison_btn = st.button("🔍 Lancer la comparaison", use_container_width=True, type="primary")

with col_btn2:
    if st.button("❓ Aide", use_container_width=True):
        st.session_state.show_help = "guide"
        st.rerun()

if comparison_btn:
    if not commande_files or not bl_files:
        st.error("⚠️ Veuillez téléverser des commandes ET des bons de livraison.")
        st.stop()
    
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

# Affichage des résultats
if st.session_state.historique:
    latest = st.session_state.historique[-1]
    results = latest["results"]
    commandes_dict = latest["commandes_dict"]
    bls_dict = latest["bls_dict"]
    hide_unmatched = latest["hide_unmatched"]
    
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
                if val == "OK":
                    return "background-color: #d4edda"
                if val == "QTY_DIFF":
                    return "background-color: #fff3cd"
                if val == "MISSING_IN_BL":
                    return "background-color: #f8d7da"
                return ""
            
            st.dataframe(
                df.style.applymap(color_status, subset=["status"]),
                use_container_width=True,
                height=400
            )
    
    st.markdown("---")
    st.markdown("### 📥 Export")
    
    output = io.BytesIO()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"Comparaison_{timestamp}.xlsx"
    
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
                if row.get('status') == 'OK':
                    worksheet.set_row(excel_row, None, ok_format)
                elif row.get('status') == 'QTY_DIFF':
                    worksheet.set_row(excel_row, None, diff_format)
                elif row.get('status') == 'MISSING_IN_BL':
                    worksheet.set_row(excel_row, None, miss_format)
        
        summary_data = {
            'Commande': [],
            'Taux de service (%)': [],
            'Qté commandée': [],
            'Qté livrée': [],
            'Qté manquante': [],
            'Articles OK': [],
            'Articles différence': [],
            'Articles manquants': []
        }
        
        for order_num, df in results.items():
            total_bl = df["qte_bl"].sum() if "qte_bl" in df.columns else 0
            if hide_unmatched and total_bl == 0:
                continue
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
    
    st.markdown("---")
    st.markdown("### 📊 Vue d'ensemble")
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.markdown(f"""
        <div class="kpi-card success-card">
            <div class="kpi-label">Taux de service global</div>
            <div class="kpi-value">{taux_service_global:.1f}%</div>
        </div>
        """, unsafe_allow_html=True)
    with col2:
        st.markdown(f"""
        <div class="kpi-card info-card">
            <div class="kpi-label">Total commandé</div>
            <div class="kpi-value">{int(total_commande)}</div>
        </div>
        """, unsafe_allow_html=True)
    with col3:
        st.markdown(f"""
        <div class="kpi-card">
            <div class="kpi-label">Total livré</div>
            <div class="kpi-value">{int(total_livre)}</div>
        </div>
        """, unsafe_allow_html=True)
    with col4:
        st.markdown(f"""
        <div class="kpi-card warning-card">
            <div class="kpi-label">Total manquant</div>
            <div class="kpi-value">{int(total_manquant)}</div>
        </div>
        """, unsafe_allow_html=True)
    
    st.markdown("<br>", unsafe_allow_html=True)
    
    col1, col2 = st.columns(2)
    if PLOTLY_AVAILABLE:
        with col1:
            status_data = pd.DataFrame({
                'Statut': ['✅ OK', '⚠️ Différence', '❌ Manquant'],
                'Nombre': [total_articles_ok, total_articles_diff, total_articles_missing]
            })
            fig_status = px.pie(
                status_data, 
                values='Nombre', 
                names='Statut',
                title='Répartition des articles',
                color_discrete_sequence=['#38ef7d', '#f5576c', '#ff6b6b']
            )
            fig_status.update_traces(textposition='inside', textinfo='percent+label')
            st.plotly_chart(fig_status, use_container_width=True)
        
        with col2:
            service_rates = []
            for order_num, df in results.items():
                if not order_included(df):
                    continue
                total_cmd = df["qte_commande"].sum()
                total_bl = df["qte_bl"].sum()
                rate = (total_bl / total_cmd * 100) if total_cmd > 0 else 0
                service_rates.append({
                    'Commande': str(order_num),
                    'Taux de service': rate
                })
            df_service = pd.DataFrame(service_rates)
            if not df_service.empty:
                fig_service = go.Figure(data=[
                    go.Bar(
                        x=df_service['Commande'],
                        y=df_service['Taux de service'],
                        marker=dict(
                            color=df_service['Taux de service'],
                            colorscale=[[0, '#ff6b6b'], [0.5, '#ffd93d'], [1, '#38ef7d']],
                            cmin=0,
                            cmax=100,
                            showscale=False
                        ),
                        text=[f"{v:.1f}%" for v in df_service['Taux de service']],
                        textposition='outside'
                    )
                ])
                fig_service.update_layout(
                    title='Taux de service par commande',
                    xaxis_title='N° Commande',
                    yaxis_title='Taux de service (%)',
                    yaxis_range=[0, 110],
                    showlegend=False,
                    xaxis=dict(type='category')
                )
                st.plotly_chart(fig_service, use_container_width=True)
            else:
                st.info("Aucune commande à afficher.")
    else:
        with col1:
            st.metric("Articles OK", total_articles_ok)
            st.metric("Articles avec différence", total_articles_diff)
            st.metric("Articles manquants", total_articles_missing)
        with col2:
            for order_num, df in results.items():
                if not order_included(df):
                    continue
                total_cmd = df["qte_commande"].sum()
                total_bl = df["qte_bl"].sum()
                rate = (total_bl / total_cmd * 100) if total_cmd > 0 else 0
                st.metric(f"Commande {order_num}", f"{rate:.1f}%")
    
    tabs = st.tabs(["📈 Statistiques", "🏆 Top produits"])
    
    with tabs[0]:
        st.markdown("### 📈 Articles manquants par code article")
        missing_by_code = {}
        for order_num, df in results.items():
            if not order_included(df):
                continue
            missing = df[df["status"] == "MISSING_IN_BL"]
            for _, row in missing.iterrows():
                code = row["code_article"]
                if code not in missing_by_code:
                    missing_by_code[code] = {"Code article": code, "Qté totale manquante": 0}
                missing_by_code[code]["Qté totale manquante"] += int(row["qte_commande"])
        
        if missing_by_code:
            df_missing = pd.DataFrame(list(missing_by_code.values()))
            df_missing = df_missing.sort_values("Qté totale manquante", ascending=False).head(10)
            st.markdown("#### Top 10 des codes articles manquants")
            st.dataframe(df_missing, use_container_width=True, hide_index=True)
        else:
            st.success("✅ Aucun article manquant !")
    
    with tabs[1]:
        st.markdown("### 🏆 Classement des produits")
        all_products = []
        for order_num, df in results.items():
            if not order_included(df):
                continue
            for _, row in df.iterrows():
                all_products.append({
                    "Code article": row["code_article"],
                    "EAN": row["ref"],
                    "Qté commandée": int(row["qte_commande"]),
                    "Qté livrée": int(row["qte_bl"])
                })
        
        if all_products:
            df_products = pd.DataFrame(all_products)
        else:
            df_products = pd.DataFrame(columns=["Code article", "EAN", "Qté commandée", "Qté livrée"])
        
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("#### 📦 Top 10 commandés")
            if not df_products.empty:
                top_cmd = df_products.groupby("Code article")["Qté commandée"].sum().sort_values(ascending=False).head(10)
                st.dataframe(top_cmd.reset_index(), use_container_width=True, hide_index=True)
            else:
                st.info("Aucun produit à afficher.")
        with col2:
            st.markdown("#### 📋 Top 10 livrés")
            if not df_products.empty:
                top_livre = df_products.groupby("Code article")["Qté livrée"].sum().sort_values(ascending=False).head(10)
                st.dataframe(top_livre.reset_index(), use_container_width=True, hide_index=True)
            else:
                st.info("Aucun produit à afficher.")
else:
    st.info("👆 Téléversez vos fichiers et lancez la comparaison pour commencer")

# Modal d'aide
if st.session_state.show_help == "guide":
    st.markdown("---")
    st.markdown("## 📖 Guide d'utilisation")
    
    with st.expander("🚀 Démarrage rapide", expanded=True):
        st.markdown("""
        ### Étapes principales :
        1. **Téléversez vos PDF** dans la barre latérale gauche
           - 📦 Commandes client (un ou plusieurs)
           - 📋 Bons de livraison (un ou plusieurs)
        
        2. **Cliquez sur "🔍 Lancer la comparaison"**
        
        3. **Consultez les résultats** :
           - Détails par commande
           - Rapport Excel téléchargeable
           - Statistiques et KPIs
        """)
    
    with st.expander("📊 Comprendre les résultats"):
        st.markdown("""
        ### Codes couleur :
        - 🟢 **OK** : Quantité commandée = Quantité livrée
        - 🟡 **QTY_DIFF** : Différence de quantité
        - 🔴 **MISSING_IN_BL** : Article non trouvé dans le BL
        
        ### KPIs :
        - **Taux de service** : (Qté livrée / Qté commandée) × 100
        - **Total manquant** : Somme des articles non livrés
        """)
    
    with st.expander("⚙️ Options avancées"):
        st.markdown("""
        ### Masquer les commandes sans correspondance
        Exclut de l'export Excel les commandes qui n'ont pas de BL correspondant.
        
        ### Historique
        Toutes vos comparaisons sont sauvegardées temporairement dans la session.
        
        ### Vérification DESADV (Admin uniquement)
        Connecte automatiquement aux sites EDI pour récupérer les commandes à traiter.
        """)
    
    with st.expander("👥 Gestion des utilisateurs (Admin)"):
        st.markdown("""
        ### Ajouter un nouvel utilisateur :
        
        Dans le code, section `USERS_DB`, ajoutez :
        ```python
        "nom_utilisateur": {
            "password": "mot_de_passe",
            "role": "admin",  # ou "user"
            "web_access": True  # ou False
        }
        ```
        
        **Paramètres :**
        - `role: "admin"` → Accès complet
        - `role: "user"` → Accès limité
        - `web_access: True` → Peut vérifier les DESADV
        - `web_access: False` → Pas d'accès aux plateformes EDI
        """)
    
    if st.button("✅ Compris, retour à l'outil", type="primary"):
        st.session_state.show_help = False
        st.rerun()

st.markdown("""
<div style='text-align: center; margin-top: 40px; font-size: 18px; color: #888;'>
    ⭐⭐⭐⭐⭐<br>
    <strong>Powered by IC - 2025</strong>
</div>
""", unsafe_allow_html=True), text):
                        try:
                            montant = float(text)
                            break
                        except:
                            pass
                
                # Entrepôt (généralement colonne 2 ou 3)
                entrepot = cols[2].text.strip() if len(cols) > 2 else ""
                
                if date_livraison == tomorrow and montant:
                    commandes_brutes.append({
                        "numero": numero,
                        "entrepot": entrepot,
                        "montant": montant,
                        "date_livraison": date_livraison
                    })
                    
            except Exception as e:
                st.warning(f"⚠️ Erreur ligne {idx}: {str(e)}")
                continue
        
        st.info(f"📊 {len(commandes_brutes)} commandes trouvées pour {tomorrow}")
        
        if not commandes_brutes:
            return [], tomorrow, "ℹ️ Aucune commande pour demain"
        
        # Regrouper par entrepôt
        entrepots = {}
        for cmd in commandes_brutes:
            entrepot = cmd["entrepot"]
            if entrepot not in entrepots:
                entrepots[entrepot] = {"montant_total": 0, "commandes": []}
            entrepots[entrepot]["montant_total"] += cmd["montant"]
            entrepots[entrepot]["commandes"].append(cmd["numero"])
        
        # Filtrer >= 850€
        desadv_a_faire = []
        for entrepot, data in entrepots.items():
            if data["montant_total"] >= 850:
                desadv_a_faire.append({
                    "entrepot": entrepot,
                    "montant_total": data["montant_total"],
                    "nb_commandes": len(data["commandes"]),
                    "commandes": data["commandes"]
                })
        
        desadv_a_faire.sort(key=lambda x: x["montant_total"], reverse=True)
        return desadv_a_faire, tomorrow, "success"
        
    except Exception as e:
        import traceback
        error_detail = traceback.format_exc()
        st.error(f"❌ Erreur générale: {str(e)}")
        with st.expander("🔍 Détails de l'erreur"):
            st.code(error_detail)
        return [], tomorrow, f"❌ Erreur: {str(e)}"

def fetch_desadv_from_edi1_real():
    """
    Connexion au site EDI1 (ed1.atgped.net)
    Clients filtrés: INTERMARCHE, DEPOT CSD ALBY SUR CHERAN, ITM LUXEMONT-ET-VILLOTTE
    """
    try:
        import requests
        from bs4 import BeautifulSoup
        import os
        
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%d/%m/%Y")
        session = requests.Session()
        login_url = "https://ed1.atgped.net/gui.php"
        
        try:
            username = st.secrets.get("EDI_USERNAME", "")
            password = st.secrets.get("EDI_PASSWORD", "")
        except:
            username = os.getenv("EDI_USERNAME", "")
            password = os.getenv("EDI_PASSWORD", "")
        
        if not username or not password:
            return [], tomorrow, "Identifiants non configurés"
        
        login_data = {
            "username": username,
            "password": password,
            "action": "login"
        }
        
        login_response = session.post(login_url, data=login_data, timeout=15)
        
        if "Liste des commandes" not in login_response.text and "Documents" not in login_response.text:
            return [], tomorrow, "Échec de connexion"
        
        commandes_url = "https://ed1.atgped.net/gui.php"
        params = {
            "query": "documents_commandes_liste",
            "page": "documents_commandes_liste",
            "pos": "0",
            "acces_page": "1",
            "lines_per_page": "1000",
            "doNumero": "",
            "RaisonSocialeSiegeSoc": "",
            "livrerA": "",
        }
        
        response = session.get(commandes_url, params=params, timeout=15)
        soup = BeautifulSoup(response.content, 'html.parser')
        
        clients_autorises = [
            "INTERMARCHE",
            "DEPOT CSD ALBY SUR CHERAN",
            "ITM LUXEMONT-ET-VILLOTTE"
        ]
        
        commandes_brutes = []
        table = soup.find('table')
        if not table:
            return [], tomorrow, "Aucune commande trouvée"
        
        rows = table.find_all('tr')[1:]
        
        for row in rows:
            cols = row.find_all('td')
            if len(cols) < 7:
                continue
            
            try:
                numero = cols[0].text.strip()
                client = cols[1].text.strip()
                entrepot = cols[2].text.strip()
                date_livraison = cols[4].text.strip()
                montant_text = cols[6].text.strip()
                montant = float(montant_text.replace(" ", "").replace(",", "."))
                
                if any(client_autorise in client.upper() for client_autorise in clients_autorises):
                    if date_livraison == tomorrow:
                        commandes_brutes.append({
                            "numero": numero,
                            "client": client,
                            "entrepot": entrepot,
                            "montant": montant,
                            "date_livraison": date_livraison
                        })
            except:
                continue
        
        clients = {}
        for cmd in commandes_brutes:
            client = cmd["client"]
            if client not in clients:
                clients[client] = {"montant_total": 0, "commandes": []}
            clients[client]["montant_total"] += cmd["montant"]
            clients[client]["commandes"].append(cmd["numero"])
        
        desadv_a_faire = []
        for client, data in clients.items():
            if data["montant_total"] >= 850:
                desadv_a_faire.append({
                    "entrepot": client,
                    "montant_total": data["montant_total"],
                    "nb_commandes": len(data["commandes"]),
                    "commandes": data["commandes"]
                })
        
        desadv_a_faire.sort(key=lambda x: x["montant_total"], reverse=True)
        return desadv_a_faire, tomorrow, "success"
        
    except Exception as e:
        return [], tomorrow, f"Erreur: {str(e)}"

def fetch_desadv_from_auchan():
    """Version avec fallback pour Auchan"""
    result, date, status = fetch_desadv_from_auchan_real()
    
    if status != "success":
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%d/%m/%Y")
        commandes_brutes = [
            {"numero": "03385063", "entrepot": "PFI VENDENHEIM", "montant": 5432.70, "date_livraison": tomorrow},
            {"numero": "03311038", "entrepot": "APPRO PFI LE COUDRAY", "montant": 3406.81, "date_livraison": tomorrow},
            {"numero": "03401873", "entrepot": "PFI CARVIN", "montant": 3226.07, "date_livraison": tomorrow},
        ]
        
        entrepots = {}
        for cmd in commandes_brutes:
            entrepot = cmd["entrepot"]
            if entrepot not in entrepots:
                entrepots[entrepot] = {"montant_total": 0, "commandes": []}
            entrepots[entrepot]["montant_total"] += cmd["montant"]
            entrepots[entrepot]["commandes"].append(cmd["numero"])
        
        desadv_a_faire = []
        for entrepot, data in entrepots.items():
            if data["montant_total"] >= 850:
                desadv_a_faire.append({
                    "entrepot": entrepot,
                    "montant_total": data["montant_total"],
                    "nb_commandes": len(data["commandes"]),
                    "commandes": data["commandes"]
                })
        
        desadv_a_faire.sort(key=lambda x: x["montant_total"], reverse=True)
        return desadv_a_faire, tomorrow, "simulation"
    
    return result, date, status

def fetch_desadv_from_edi1():
    """Version avec fallback pour EDI1"""
    result, date, status = fetch_desadv_from_edi1_real()
    
    if status != "success":
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%d/%m/%Y")
        commandes_brutes = [
            {"numero": "46961161", "client": "INTERMARCHE", "montant": 4085.29, "date_livraison": tomorrow},
            {"numero": "46962231", "client": "ITM LUXEMONT-ET-VILLOTTE", "montant": 1293.78, "date_livraison": tomorrow},
        ]
        
        clients = {}
        for cmd in commandes_brutes:
            client = cmd["client"]
            if client not in clients:
                clients[client] = {"montant_total": 0, "commandes": []}
            clients[client]["montant_total"] += cmd["montant"]
            clients[client]["commandes"].append(cmd["numero"])
        
        desadv_a_faire = []
        for client, data in clients.items():
            if data["montant_total"] >= 850:
                desadv_a_faire.append({
                    "entrepot": client,
                    "montant_total": data["montant_total"],
                    "nb_commandes": len(data["commandes"]),
                    "commandes": data["commandes"]
                })
        
        desadv_a_faire.sort(key=lambda x: x["montant_total"], reverse=True)
        return desadv_a_faire, tomorrow, "simulation"
    
    return result, date, status

# ============================================
# SIDEBAR
# ============================================

with st.sidebar:
    st.header("👤 Utilisateur")
    st.markdown(f"**{st.session_state.username}**")
    st.caption(f"Rôle: {st.session_state.user_role}")
    
    if st.button("🚪 Déconnexion", use_container_width=True):
        st.session_state.authenticated = False
        st.session_state.user_role = None
        st.session_state.user_web_access = False
        st.session_state.username = None
        st.rerun()
    
    st.markdown("---")
    st.header("📁 Fichiers")
    
    if st.button("🔄 Nouveau", use_container_width=True, type="primary"):
        st.session_state.key_cmd = f"cmd_{time.time()}"
        st.session_state.key_bl = f"bl_{time.time()}"
        st.session_state.historique = []
        st.rerun()
    
    st.markdown("---")
    
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
    
    # Section DESADV
    if st.session_state.user_web_access:
        st.markdown("---")
        st.header("🌐 Vérification DESADV")
        
        if st.button("🔍 Vérifier les DESADV", use_container_width=True, type="secondary"):
            with st.spinner("🔄 Connexion aux plateformes EDI..."):
                auchan_data, auchan_date, auchan_status = fetch_desadv_from_auchan()
                edi1_data, edi1_date, edi1_status = fetch_desadv_from_edi1()
                
                st.session_state.desadv_auchan = {
                    "data": auchan_data,
                    "date": auchan_date,
                    "status": auchan_status
                }
                st.session_state.desadv_edi1 = {
                    "data": edi1_data,
                    "date": edi1_date,
                    "status": edi1_status
                }
            st.rerun()
        
        if hasattr(st.session_state, 'desadv_auchan') or hasattr(st.session_state, 'desadv_edi1'):
            total_desadv = 0
            total_montant = 0
            
            if hasattr(st.session_state, 'desadv_auchan'):
                auchan = st.session_state.desadv_auchan
                if auchan["data"]:
                    total_desadv += len(auchan["data"])
                    total_montant += sum([d["montant_total"] for d in auchan["data"]])
            
            if hasattr(st.session_state, 'desadv_edi1'):
                edi1 = st.session_state.desadv_edi1
                if edi1["data"]:
                    total_desadv += len(edi1["data"])
                    total_montant += sum([d["montant_total"] for d in edi1["data"]])
            
            if total_desadv > 0:
                st.success(f"✅ **{total_desadv} DESADV** à faire")
                st.metric("Montant total", f"{total_montant:,.2f} €")
                
                if st.button("📋 Voir les détails", use_container_width=True):
                    st.session_state.show_desadv_details = True
                    st.rerun()
                
                if st.button("🗑️ Effacer", use_container_width=True):
                    if hasattr(st.session_state, 'desadv_auchan'):
                        delattr(st.session_state, 'desadv_auchan')
                    if hasattr(st.session_state, 'desadv_edi1'):
                        delattr(st.session_state, 'desadv_edi1')
                    st.session_state.show_desadv_details = False
                    st.rerun()
            else:
                st.info("Aucun DESADV à traiter")
    else:
        st.markdown("---")
        st.info("🔒 Vérification DESADV\nAccès non autorisé pour votre compte")

# ============================================
# MAIN CONTENT
# ============================================

# Affichage détails DESADV
if st.session_state.show_desadv_details:
    st.markdown("---")
    st.markdown("## 🌐 Détails des DESADV à traiter")
    
    col1, col2 = st.columns(2)
    
    # AUCHAN
    with col1:
        st.markdown("### 🔵 AUCHAN ATGPED")
        if hasattr(st.session_state, 'desadv_auchan'):
            auchan = st.session_state.desadv_auchan
            
            if auchan["status"] == "simulation":
                st.warning("⚠️ Données de simulation (connexion impossible)")
            elif auchan["status"] != "success":
                st.error(f"❌ {auchan['status']}")
            
            if auchan["data"]:
                st.success(f"📅 Livraison: **{auchan['date']}**")
                st.metric("Nombre de DESADV", len(auchan["data"]))
                st.metric("Montant total", f"{sum([d['montant_total'] for d in auchan['data']]):,.2f} €")
                
                st.markdown("---")
                for idx, desadv in enumerate(auchan["data"], 1):
                    with st.expander(f"📦 {idx}. {desadv['entrepot']}", expanded=False):
                        st.metric("Montant", f"{desadv['montant_total']:,.2f} €")
                        st.write(f"**{desadv['nb_commandes']} commande(s):**")
                        st.write(", ".join(desadv['commandes']))
            else:
                st.info("✅ Aucun DESADV Auchan à traiter")
        else:
            st.info("Aucune donnée disponible")
    
    # EDI1
    with col2:
        st.markdown("### 🟢 EDI1 (ITM, CSD, etc.)")
        if hasattr(st.session_state, 'desadv_edi1'):
            edi1 = st.session_state.desadv_edi1
            
            if edi1["status"] == "simulation":
                st.warning("⚠️ Données de simulation (connexion impossible)")
            elif edi1["status"] != "success":
                st.error(f"❌ {edi1['status']}")
            
            if edi1["data"]:
                st.success(f"📅 Livraison: **{edi1['date']}**")
                st.metric("Nombre de DESADV", len(edi1["data"]))
                st.metric("Montant total", f"{sum([d['montant_total'] for d in edi1['data']]):,.2f} €")
                
                st.markdown("---")
                for idx, desadv in enumerate(edi1["data"], 1):
                    with st.expander(f"📦 {idx}. {desadv['entrepot']}", expanded=False):
                        st.metric("Montant", f"{desadv['montant_total']:,.2f} €")
                        st.write(f"**{desadv['nb_commandes']} commande(s):**")
                        st.write(", ".join(desadv['commandes']))
            else:
                st.info("✅ Aucun DESADV EDI1 à traiter")
        else:
            st.info("Aucune donnée disponible")
    
    if st.button("❌ Fermer les détails", type="secondary"):
        st.session_state.show_desadv_details = False
        st.rerun()
    
    st.markdown("---")

# Boutons principaux
col_btn1, col_btn2 = st.columns([3, 1])

with col_btn1:
    comparison_btn = st.button("🔍 Lancer la comparaison", use_container_width=True, type="primary")

with col_btn2:
    if st.button("❓ Aide", use_container_width=True):
        st.session_state.show_help = "guide"
        st.rerun()

if comparison_btn:
    if not commande_files or not bl_files:
        st.error("⚠️ Veuillez téléverser des commandes ET des bons de livraison.")
        st.stop()
    
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

# Affichage des résultats
if st.session_state.historique:
    latest = st.session_state.historique[-1]
    results = latest["results"]
    commandes_dict = latest["commandes_dict"]
    bls_dict = latest["bls_dict"]
    hide_unmatched = latest["hide_unmatched"]
    
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
                if val == "OK":
                    return "background-color: #d4edda"
                if val == "QTY_DIFF":
                    return "background-color: #fff3cd"
                if val == "MISSING_IN_BL":
                    return "background-color: #f8d7da"
                return ""
            
            st.dataframe(
                df.style.applymap(color_status, subset=["status"]),
                use_container_width=True,
                height=400
            )
    
    st.markdown("---")
    st.markdown("### 📥 Export")
    
    output = io.BytesIO()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"Comparaison_{timestamp}.xlsx"
    
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
                if row.get('status') == 'OK':
                    worksheet.set_row(excel_row, None, ok_format)
                elif row.get('status') == 'QTY_DIFF':
                    worksheet.set_row(excel_row, None, diff_format)
                elif row.get('status') == 'MISSING_IN_BL':
                    worksheet.set_row(excel_row, None, miss_format)
        
        summary_data = {
            'Commande': [],
            'Taux de service (%)': [],
            'Qté commandée': [],
            'Qté livrée': [],
            'Qté manquante': [],
            'Articles OK': [],
            'Articles différence': [],
            'Articles manquants': []
        }
        
        for order_num, df in results.items():
            total_bl = df["qte_bl"].sum() if "qte_bl" in df.columns else 0
            if hide_unmatched and total_bl == 0:
                continue
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
    
    st.markdown("---")
    st.markdown("### 📊 Vue d'ensemble")
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.markdown(f"""
        <div class="kpi-card success-card">
            <div class="kpi-label">Taux de service global</div>
            <div class="kpi-value">{taux_service_global:.1f}%</div>
        </div>
        """, unsafe_allow_html=True)
    with col2:
        st.markdown(f"""
        <div class="kpi-card info-card">
            <div class="kpi-label">Total commandé</div>
            <div class="kpi-value">{int(total_commande)}</div>
        </div>
        """, unsafe_allow_html=True)
    with col3:
        st.markdown(f"""
        <div class="kpi-card">
            <div class="kpi-label">Total livré</div>
            <div class="kpi-value">{int(total_livre)}</div>
        </div>
        """, unsafe_allow_html=True)
    with col4:
        st.markdown(f"""
        <div class="kpi-card warning-card">
            <div class="kpi-label">Total manquant</div>
            <div class="kpi-value">{int(total_manquant)}</div>
        </div>
        """, unsafe_allow_html=True)
    
    st.markdown("<br>", unsafe_allow_html=True)
    
    col1, col2 = st.columns(2)
    if PLOTLY_AVAILABLE:
        with col1:
            status_data = pd.DataFrame({
                'Statut': ['✅ OK', '⚠️ Différence', '❌ Manquant'],
                'Nombre': [total_articles_ok, total_articles_diff, total_articles_missing]
            })
            fig_status = px.pie(
                status_data, 
                values='Nombre', 
                names='Statut',
                title='Répartition des articles',
                color_discrete_sequence=['#38ef7d', '#f5576c', '#ff6b6b']
            )
            fig_status.update_traces(textposition='inside', textinfo='percent+label')
            st.plotly_chart(fig_status, use_container_width=True)
        
        with col2:
            service_rates = []
            for order_num, df in results.items():
                if not order_included(df):
                    continue
                total_cmd = df["qte_commande"].sum()
                total_bl = df["qte_bl"].sum()
                rate = (total_bl / total_cmd * 100) if total_cmd > 0 else 0
                service_rates.append({
                    'Commande': str(order_num),
                    'Taux de service': rate
                })
            df_service = pd.DataFrame(service_rates)
            if not df_service.empty:
                fig_service = go.Figure(data=[
                    go.Bar(
                        x=df_service['Commande'],
                        y=df_service['Taux de service'],
                        marker=dict(
                            color=df_service['Taux de service'],
                            colorscale=[[0, '#ff6b6b'], [0.5, '#ffd93d'], [1, '#38ef7d']],
                            cmin=0,
                            cmax=100,
                            showscale=False
                        ),
                        text=[f"{v:.1f}%" for v in df_service['Taux de service']],
                        textposition='outside'
                    )
                ])
                fig_service.update_layout(
                    title='Taux de service par commande',
                    xaxis_title='N° Commande',
                    yaxis_title='Taux de service (%)',
                    yaxis_range=[0, 110],
                    showlegend=False,
                    xaxis=dict(type='category')
                )
                st.plotly_chart(fig_service, use_container_width=True)
            else:
                st.info("Aucune commande à afficher.")
    else:
        with col1:
            st.metric("Articles OK", total_articles_ok)
            st.metric("Articles avec différence", total_articles_diff)
            st.metric("Articles manquants", total_articles_missing)
        with col2:
            for order_num, df in results.items():
                if not order_included(df):
                    continue
                total_cmd = df["qte_commande"].sum()
                total_bl = df["qte_bl"].sum()
                rate = (total_bl / total_cmd * 100) if total_cmd > 0 else 0
                st.metric(f"Commande {order_num}", f"{rate:.1f}%")
    
    tabs = st.tabs(["📈 Statistiques", "🏆 Top produits"])
    
    with tabs[0]:
        st.markdown("### 📈 Articles manquants par code article")
        missing_by_code = {}
        for order_num, df in results.items():
            if not order_included(df):
                continue
            missing = df[df["status"] == "MISSING_IN_BL"]
            for _, row in missing.iterrows():
                code = row["code_article"]
                if code not in missing_by_code:
                    missing_by_code[code] = {"Code article": code, "Qté totale manquante": 0}
                missing_by_code[code]["Qté totale manquante"] += int(row["qte_commande"])
        
        if missing_by_code:
            df_missing = pd.DataFrame(list(missing_by_code.values()))
            df_missing = df_missing.sort_values("Qté totale manquante", ascending=False).head(10)
            st.markdown("#### Top 10 des codes articles manquants")
            st.dataframe(df_missing, use_container_width=True, hide_index=True)
        else:
            st.success("✅ Aucun article manquant !")
    
    with tabs[1]:
        st.markdown("### 🏆 Classement des produits")
        all_products = []
        for order_num, df in results.items():
            if not order_included(df):
                continue
            for _, row in df.iterrows():
                all_products.append({
                    "Code article": row["code_article"],
                    "EAN": row["ref"],
                    "Qté commandée": int(row["qte_commande"]),
                    "Qté livrée": int(row["qte_bl"])
                })
        
        if all_products:
            df_products = pd.DataFrame(all_products)
        else:
            df_products = pd.DataFrame(columns=["Code article", "EAN", "Qté commandée", "Qté livrée"])
        
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("#### 📦 Top 10 commandés")
            if not df_products.empty:
                top_cmd = df_products.groupby("Code article")["Qté commandée"].sum().sort_values(ascending=False).head(10)
                st.dataframe(top_cmd.reset_index(), use_container_width=True, hide_index=True)
            else:
                st.info("Aucun produit à afficher.")
        with col2:
            st.markdown("#### 📋 Top 10 livrés")
            if not df_products.empty:
                top_livre = df_products.groupby("Code article")["Qté livrée"].sum().sort_values(ascending=False).head(10)
                st.dataframe(top_livre.reset_index(), use_container_width=True, hide_index=True)
            else:
                st.info("Aucun produit à afficher.")
else:
    st.info("👆 Téléversez vos fichiers et lancez la comparaison pour commencer")

# Modal d'aide
if st.session_state.show_help == "guide":
    st.markdown("---")
    st.markdown("## 📖 Guide d'utilisation")
    
    with st.expander("🚀 Démarrage rapide", expanded=True):
        st.markdown("""
        ### Étapes principales :
        1. **Téléversez vos PDF** dans la barre latérale gauche
           - 📦 Commandes client (un ou plusieurs)
           - 📋 Bons de livraison (un ou plusieurs)
        
        2. **Cliquez sur "🔍 Lancer la comparaison"**
        
        3. **Consultez les résultats** :
           - Détails par commande
           - Rapport Excel téléchargeable
           - Statistiques et KPIs
        """)
    
    with st.expander("📊 Comprendre les résultats"):
        st.markdown("""
        ### Codes couleur :
        - 🟢 **OK** : Quantité commandée = Quantité livrée
        - 🟡 **QTY_DIFF** : Différence de quantité
        - 🔴 **MISSING_IN_BL** : Article non trouvé dans le BL
        
        ### KPIs :
        - **Taux de service** : (Qté livrée / Qté commandée) × 100
        - **Total manquant** : Somme des articles non livrés
        """)
    
    with st.expander("⚙️ Options avancées"):
        st.markdown("""
        ### Masquer les commandes sans correspondance
        Exclut de l'export Excel les commandes qui n'ont pas de BL correspondant.
        
        ### Historique
        Toutes vos comparaisons sont sauvegardées temporairement dans la session.
        
        ### Vérification DESADV (Admin uniquement)
        Connecte automatiquement aux sites EDI pour récupérer les commandes à traiter.
        """)
    
    with st.expander("👥 Gestion des utilisateurs (Admin)"):
        st.markdown("""
        ### Ajouter un nouvel utilisateur :
        
        Dans le code, section `USERS_DB`, ajoutez :
        ```python
        "nom_utilisateur": {
            "password": "mot_de_passe",
            "role": "admin",  # ou "user"
            "web_access": True  # ou False
        }
        ```
        
        **Paramètres :**
        - `role: "admin"` → Accès complet
        - `role: "user"` → Accès limité
        - `web_access: True` → Peut vérifier les DESADV
        - `web_access: False` → Pas d'accès aux plateformes EDI
        """)
    
    if st.button("✅ Compris, retour à l'outil", type="primary"):
        st.session_state.show_help = False
        st.rerun()

st.markdown("""
<div style='text-align: center; margin-top: 40px; font-size: 18px; color: #888;'>
    ⭐⭐⭐⭐⭐<br>
    <strong>Powered by IC - 2025</strong>
</div>
""", unsafe_allow_html=True)
