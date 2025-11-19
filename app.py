import streamlit as st
import pdfplumber
import pandas as pd
import io
import re
from collections import defaultdict
from datetime import datetime, timedelta
import time
import base64
import os
import socket

try:
    import plotly.express as px
    import plotly.graph_objects as go
    PLOTLY_AVAILABLE = True
except ImportError:
    PLOTLY_AVAILABLE = False
    st.warning("⚠️ Plotly non installé. Les graphiques ne seront pas affichés.")

# Import requests / bs4 for scraping
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from requests.exceptions import RequestException

st.set_page_config(
    page_title="DESATHOR",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Logo plus haut
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
    .help-button {
        position: fixed;
        bottom: 20px;
        right: 20px;
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        padding: 15px 25px;
        border-radius: 50px;
        font-size: 16px;
        font-weight: bold;
        box-shadow: 0 4px 15px rgba(0,0,0,0.2);
        cursor: pointer;
        z-index: 999;
        border: none;
    }
</style>
""", unsafe_allow_html=True)

# Try to load logo non-blocking
logo_path = "Desathor.png"
if os.path.exists(logo_path):
    try:
        with open(logo_path, "rb") as f:
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
    except Exception:
        pass

if 'historique' not in st.session_state:
    st.session_state.historique = []
if "key_cmd" not in st.session_state:
    st.session_state.key_cmd = "cmd_1"
if "key_bl" not in st.session_state:
    st.session_state.key_bl = "bl_1"
if "show_help" not in st.session_state:
    st.session_state.show_help = False
if "desadv_notifications" not in st.session_state:
    st.session_state.desadv_notifications = []
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "user_role" not in st.session_state:
    st.session_state.user_role = None

# Base de données utilisateurs simulée (À REMPLACER par vraie BDD)
USERS_DB = {
    "ISA": {"password": "admin123", "role": "admin", "web_access": True},
    "bak": {"password": "bak123", "role": "user", "web_access": False},
}

def check_password(username, password):
    """Vérifie les identifiants utilisateur"""
    if username in USERS_DB and USERS_DB[username]["password"] == password:
        return True, USERS_DB[username]["role"], USERS_DB[username]["web_access"]
    return False, None, False

# -----------------------
# Utilitaires parsing
# -----------------------
def parse_number_fr(text):
    """Convertit '1 234,56' ou '1234.56' -> float"""
    if text is None:
        return 0.0
    txt = str(text).strip()
    txt = txt.replace("\u202f", "").replace("\xa0", "").replace(" ", "")
    txt = txt.replace(",", ".")
    txt = re.sub(r"[^\d\.-]", "", txt)
    try:
        return float(txt) if txt != "" else 0.0
    except:
        return 0.0

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
    if not code:
        return False
    s = re.sub(r"\D", "", str(code))
    if len(s) != 13:
        return False
    # optional prefix exclusion
    if s.startswith(('302', '376')):
        return False
    # checksum
    digits = [int(c) for c in s]
    checksum = digits[-1]
    evens = sum(digits[-2::-2])
    odds = sum(digits[-3::-2])
    total = odds + evens * 3
    calc = (10 - (total % 10)) % 10
    return calc == checksum

# -----------------------
# PDF Extraction
# -----------------------
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
                        try:
                            qte = int(qty_match.group(1))
                        except:
                            qte = None
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

# -----------------------
# Scraping helpers (robust)
# -----------------------
def get_proxy_from_secrets_or_env():
    try:
        proxy = st.secrets.get("PROXY_URL", None)
    except Exception:
        proxy = None
    if not proxy:
        proxy = os.getenv("PROXY_URL") or os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")
    return proxy

def robust_session(retries=3, backoff_factor=0.3, status_forcelist=(500,502,503,504)):
    s = requests.Session()
    retry = Retry(total=retries, read=retries, connect=retries,
                  backoff_factor=backoff_factor, status_forcelist=status_forcelist)
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    })
    s.trust_env = True
    proxy_url = get_proxy_from_secrets_or_env()
    if proxy_url:
        s.proxies.update({"http": proxy_url, "https": proxy_url})
    return s

def _find_column_indices(table):
    header = None
    for tr in table.find_all("tr"):
        ths = tr.find_all(['th','td'])
        if ths and len(ths) > 1:
            header = [ (th.get_text(" ", strip=True).lower()) for th in ths ]
            break
    indices = {'numero': None, 'entrepot': None, 'date': None, 'montant': None}
    if not header:
        return indices
    for i, h in enumerate(header):
        if indices['numero'] is None and any(k in h for k in ['num', 'n°', 'numero', 'no commande', 'commande']):
            indices['numero'] = i
        if indices['entrepot'] is None and any(k in h for k in ['livrer', 'livrer à', 'livrer a', 'livraison', 'adresse livraison', 'livré à', 'client']):
            indices['entrepot'] = i
        if indices['date'] is None and any(k in h for k in ['livrer le', 'livrer', 'date', 'création le', 'création']):
            indices['date'] = i
        if indices['montant'] is None and 'montant' in h:
            indices['montant'] = i
    if indices['numero'] is None:
        indices['numero'] = 0
    return indices

def _extract_date_only(text):
    if not text:
        return ""
    m = re.search(r"(\d{2}/\d{2}/\d{4})", text)
    return m.group(1) if m else text.strip()

def _save_debug_html(prefix, content):
    try:
        path = f"/tmp/{prefix}_debug.html"
        with open(path, "wb") as f:
            if isinstance(content, str):
                f.write(content.encode("utf-8", errors="ignore"))
            else:
                f.write(content)
        return path
    except Exception:
        return None

# -----------------------
# Auchan scraping (robust)
# -----------------------
def fetch_desadv_from_auchan_real():
    """
    Retourne (desadv_list, target_date) ; desadv_list = [{'entrepot','montant_total','nb_commandes','commandes'}, ...]
    """
    try:
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%d/%m/%Y")
        # credentials
        try:
            username = st.secrets["AUCHAN_USERNAME"]
            password = st.secrets["AUCHAN_PASSWORD"]
        except Exception:
            username = os.getenv("AUCHAN_USERNAME", "")
            password = os.getenv("AUCHAN_PASSWORD", "")

        if not username or not password:
            # return empty and date for caller
            return [], tomorrow

        # quick DNS check
        try:
            socket.gethostbyname("auchan.atgped.net")
        except Exception:
            # DNS resolution failed
            return [], tomorrow

        session = robust_session()
        login_url = "https://auchan.atgped.net/gui.php"

        # GET login page to collect hidden inputs
        try:
            r = session.get(login_url, timeout=15)
        except RequestException:
            return [], tomorrow
        if r.status_code != 200:
            return [], tomorrow

        soup = BeautifulSoup(r.content, 'html.parser')
        payload = {}
        for inp in soup.select("input[type=hidden]"):
            name = inp.get("name")
            if name:
                payload[name] = inp.get("value", "")

        payload.update({"username": username, "password": password, "action": "login"})
        try:
            login_response = session.post(login_url, data=payload, headers={"Referer": login_url}, timeout=15, allow_redirects=True)
        except RequestException:
            return [], tomorrow

        if login_response.status_code >= 400:
            return [], tomorrow

        text_low = login_response.text.lower()
        if not any(k in text_low for k in ["liste des commandes", "documents", "logout", "déconnexion", "deconnexion"]):
            # maybe login ok but wording different; still try to load commandes page
            pass

        # 2. RÉCUPÉRER LA LISTE DES COMMANDES
        commandes_url = "https://auchan.atgped.net/gui.php"
        params = {
            "query": "documents_commandes_liste",
            "page": "documents_commandes_liste",
            "pos": "0",
            "acces_page": "1",
            "lines_per_page": "1000",
        }
        try:
            response = session.get(commandes_url, params=params, timeout=15)
        except RequestException:
            return [], tomorrow
        if response.status_code != 200:
            return [], tomorrow

        soup = BeautifulSoup(response.content, 'html.parser')
        table = soup.find('table')
        if not table:
            # try alternative direct url
            try_url = f"{commandes_url}?query=documents_commandes_liste&page=documents_commandes_liste&acces_page=1&lines_per_page=1000"
            try:
                r_alt = session.get(try_url, timeout=15)
                if r_alt.status_code == 200:
                    soup_alt = BeautifulSoup(r_alt.content, 'html.parser')
                    table = soup_alt.find('table')
                    if table:
                        soup = soup_alt
                else:
                    # save debug
                    _save_debug_html("auchan_commandes", response.content)
            except Exception:
                _save_debug_html("auchan_commandes_exc", b"")
            if not table:
                return [], tomorrow

        # parse table with header detection
        idx = _find_column_indices(table)
        rows = table.find_all('tr')
        commandes_brutes = []
        for row in rows[1:]:
            cols = [td.get_text(" ", strip=True) for td in row.find_all("td")]
            if not cols:
                continue
            try:
                numero = cols[idx['numero']] if idx['numero'] is not None and idx['numero'] < len(cols) else cols[0]
                entrepot = cols[idx['entrepot']] if idx['entrepot'] is not None and idx['entrepot'] < len(cols) else (cols[2] if len(cols) > 2 else "")
                date_livraison_raw = cols[idx['date']] if idx['date'] is not None and idx['date'] < len(cols) else ""
                date_livraison = _extract_date_only(date_livraison_raw)
                montant_text = cols[idx['montant']] if idx['montant'] is not None and idx['montant'] < len(cols) else (cols[-1] if cols else "")
                # filter by date
                if date_livraison.strip() != tomorrow:
                    continue
                montant = parse_number_fr(montant_text)
                commandes_brutes.append({
                    "numero": numero,
                    "entrepot": entrepot,
                    "montant": montant,
                    "date_livraison": date_livraison
                })
            except Exception:
                continue

        # 4. REGROUPER PAR ENTREPÔT ET ADDITIONNER
        entrepots = {}
        for cmd in commandes_brutes:
            entrepot = cmd["entrepot"]
            if entrepot not in entrepots:
                entrepots[entrepot] = {"montant_total": 0, "commandes": []}
            entrepots[entrepot]["montant_total"] += cmd["montant"]
            entrepots[entrepot]["commandes"].append(cmd["numero"])

        # 5. FILTRER CEUX >= 850€
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
        return desadv_a_faire, tomorrow

    except Exception as e:
        # generic fallback: return empty so caller can use simulation
        return [], tomorrow

# Wrapper with fallback simulation (keeps your original behavior)
def fetch_desadv_from_auchan():
    """
    Version avec fallback: essaie la vraie connexion, sinon utilise simulation
    """
    # Essayer la vraie connexion
    result, date = fetch_desadv_from_auchan_real()

    # Si échec ou pas de résultats, utiliser la simulation pour démo
    if not result:
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%d/%m/%Y")

        # Données simulées basées sur votre capture d'écran
        commandes_brutes = [
            {"numero": "03385063", "entrepot": "PFI VENDENHEIM", "montant": 5432.70, "date_livraison": tomorrow},
            {"numero": "03311038", "entrepot": "APPRO PFI LE COUDRAY", "montant": 3406.81, "date_livraison": tomorrow},
            {"numero": "03401873", "entrepot": "PFI CARVIN", "montant": 3226.07, "date_livraison": tomorrow},
            {"numero": "03216884", "entrepot": "PFI Le Pontet", "montant": 3018.19, "date_livraison": tomorrow},
            {"numero": "03250180", "entrepot": "PFI Saint Ouen", "montant": 2902.82, "date_livraison": tomorrow},
            {"numero": "03328847", "entrepot": "PFI Toussieu", "montant": 2417.31, "date_livraison": tomorrow},
            {"numero": "03188291", "entrepot": "ALC PFI AIX EN PROVENCE", "montant": 1396.71, "date_livraison": tomorrow},
            {"numero": "03129969", "entrepot": "APPRO PFI VALENCE", "montant": 978.82, "date_livraison": tomorrow},
            {"numero": "03201385", "entrepot": "APPRO PFI IDF CHILLY", "montant": 893.07, "date_livraison": tomorrow},
            {"numero": "03134203", "entrepot": "APPRO PFI COURNON", "montant": 718.87, "date_livraison": tomorrow},
            {"numero": "03433110", "entrepot": "APPRO PFI NORD SAINT SAUVEUR", "montant": 657.28, "date_livraison": tomorrow},
            {"numero": "03134614", "entrepot": "APPRO PFI COURNON", "montant": 223.85, "date_livraison": tomorrow},
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
        return desadv_a_faire, tomorrow

    return result, date

# -----------------------
# EDI1 scraping (simple robust)
# -----------------------
def fetch_desadv_from_edi1_real():
    """
    Récupère commandes EDI1 filtrées par date (demain) et par entrepôts ALBY/DOLE/LUXEMONT
    Retourne (list, date)
    """
    try:
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%d/%m/%Y")
        allowed_entrepots = ['ALBY', 'DOLE', 'LUXEMONT']

        # credentials optional
        try:
            username = st.secrets["EDI1_USERNAME"]
            password = st.secrets["EDI1_PASSWORD"]
        except Exception:
            username = os.getenv("EDI1_USERNAME", "")
            password = os.getenv("EDI1_PASSWORD", "")

        # DNS quick check
        try:
            socket.gethostbyname("edi1.atgpedi.net")
        except Exception:
            return [], tomorrow

        session = robust_session()
        login_url = "https://edi1.atgpedi.net/gui.php"

        # GET initial page
        try:
            r = session.get(login_url, timeout=15)
        except RequestException:
            return [], tomorrow

        content = r.content
        if username and password:
            soup = BeautifulSoup(r.content, "html.parser")
            payload = {}
            for inp in soup.select("input[type=hidden]"):
                name = inp.get("name")
                if name:
                    payload[name] = inp.get("value", "")
            payload.update({"username": username, "password": password, "action": "login"})
            try:
                r2 = session.post(login_url, data=payload, headers={"Referer": login_url}, timeout=15, allow_redirects=True)
                if r2.status_code >= 400:
                    return [], tomorrow
                content = r2.content
            except RequestException:
                return [], tomorrow

        # Try explicit commandes list URL like in screenshot
        try_url = "https://edi1.atgpedi.net/gui.php?query=documents_commandes_liste&page=documents_commandes_liste&acces_page=1&lines_per_page=1000"
        try:
            r3 = session.get(try_url, timeout=15)
            if r3.status_code == 200:
                content = r3.content
        except RequestException:
            pass

        soup3 = BeautifulSoup(content, "html.parser")
        table = soup3.find("table")
        if not table:
            # save debug html for inspection
            _save_debug_html("edi1_commandes", content)
            return [], tomorrow

        idx = _find_column_indices(table)
        rows = table.find_all("tr")
        commandes_brutes = []
        for row in rows[1:]:
            cols = [td.get_text(" ", strip=True) for td in row.find_all("td")]
            if not cols:
                continue
            try:
                numero = cols[idx['numero']] if idx['numero'] is not None and idx['numero'] < len(cols) else cols[0]
                entrepot = cols[idx['entrepot']] if idx['entrepot'] is not None and idx['entrepot'] < len(cols) else (cols[2] if len(cols) > 2 else "")
                date_livraison_raw = cols[idx['date']] if idx['date'] is not None and idx['date'] < len(cols) else ""
                date_livraison = _extract_date_only(date_livraison_raw)
                if date_livraison.strip() != tomorrow:
                    continue
                eup = entrepot.upper()
                allowed_match = False
                for a in allowed_entrepots:
                    if a.strip().upper() in eup:
                        allowed_match = True
                        break
                if not allowed_match:
                    continue
                commandes_brutes.append({
                    "numero": numero.strip(),
                    "entrepot": entrepot.strip(),
                    "date_livraison": date_livraison.strip()
                })
            except Exception:
                continue

        # group by entrepot
        entrepots = {}
        for cmd in commandes_brutes:
            e = cmd["entrepot"]
            entrepots.setdefault(e, {"commandes": []})
            entrepots[e]["commandes"].append(cmd["numero"])

        desadv_a_faire = []
        for entrepot, data in entrepots.items():
            desadv_a_faire.append({
                "entrepot": entrepot,
                "nb_commandes": len(data["commandes"]),
                "commandes": data["commandes"]
            })

        return desadv_a_faire, tomorrow

    except Exception:
        return [], tomorrow

# -----------------------
# SIDEBAR UI (majorité reprise de votre code)
# -----------------------
with st.sidebar:
    st.header("📁 Fichiers")
    
    # Bouton déconnexion
    if st.button("🚪 Déconnexion", use_container_width=True):
        st.session_state.authenticated = False
        st.session_state.user_role = None
        st.session_state.user_web_access = False
        st.session_state.username = None
        st.rerun()
    
    st.markdown("---")
    
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
    
    # Section DESADV (uniquement si accès web autorisé)
    if st.session_state.user_web_access:
        st.markdown("---")
        st.header("🌐 Vérification DESADV")
        
        if st.button("🔍 Vérifier les DESADV du jour", use_container_width=True, type="secondary"):
            with st.spinner("Connexion à Auchan ATGPED..."):
                time.sleep(1.5)
                desadv_list, date_livraison = fetch_desadv_from_auchan()
                st.session_state.desadv_data = desadv_list
                st.session_state.desadv_date = date_livraison
            st.rerun()
        
        if hasattr(st.session_state, 'desadv_data') and st.session_state.desadv_data:
            nb_total = len(st.session_state.desadv_data)
            montant_total = sum([d["montant_total"] for d in st.session_state.desadv_data])
            
            st.success(f"✅ **{nb_total} DESADV** à faire pour le {st.session_state.desadv_date}")
            st.metric("Montant total", f"{montant_total:,.2f} €")
            
            with st.expander("📋 Détails des DESADV", expanded=True):
                for idx, desadv in enumerate(st.session_state.desadv_data, 1):
                    st.markdown(f"""
                    **{idx}. {desadv['entrepot']}**  
                    💰 Montant: **{desadv['montant_total']:,.2f} €**  
                    📦 {desadv['nb_commandes']} commande(s): {', '.join(desadv['commandes'])}
                    """)
                    st.markdown("---")
            
            if st.button("🗑️ Effacer les notifications", use_container_width=True):
                st.session_state.desadv_data = []
                st.rerun()
    else:
        st.markdown("---")
        st.info("🔒 Vérification DESADV\nAccès non autorisé pour votre compte")
    
    st.markdown("---")
    if st.button("❓ Comment utiliser", use_container_width=True):
        st.session_state.show_help = "guide"
        st.rerun()

# -----------------------
# Main compare button (identique à votre logique)
# -----------------------
if st.button("🔍 Lancer la comparaison", use_container_width=True, type="primary"):
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
            # Ensure qte_bl column presence
            if "qte_bl" not in merged.columns:
                merged["qte_bl"] = 0
            merged["qte_bl"] = pd.to_numeric(merged["qte_bl"], errors="coerce").fillna(0)
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

# -----------------------
# Display results (kept same as your origin)
# -----------------------
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

# Modal d'aide / Configuration
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
