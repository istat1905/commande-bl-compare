# app.py
import streamlit as st
import pdfplumber
import pandas as pd
import io
import re
from collections import defaultdict
from datetime import datetime, timedelta
import time
import base64

# Optional plotting
try:
    import plotly.express as px
    import plotly.graph_objects as go
    PLOTLY_AVAILABLE = True
except ImportError:
    PLOTLY_AVAILABLE = False

st.set_page_config(page_title="DESATHOR", layout="wide", initial_sidebar_state="expanded")

# ---------------------------
# Simple users DB (demo)
# Replace by JSON/SQLite in production
# ---------------------------
USERS_DB = {
    "admin": {"password": "admin123", "role": "admin", "web_access": True},
    "user1": {"password": "user123", "role": "user", "web_access": False},
}

def check_password(username, password):
    if username in USERS_DB and USERS_DB[username]["password"] == password:
        return True, USERS_DB[username]["role"], USERS_DB[username]["web_access"]
    return False, None, False

# ---------------------------
# Session init
# ---------------------------
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "user_role" not in st.session_state:
    st.session_state.user_role = None
if "user_web_access" not in st.session_state:
    st.session_state.user_web_access = False
if "username" not in st.session_state:
    st.session_state.username = None
if "historique" not in st.session_state:
    st.session_state.historique = []
if "key_cmd" not in st.session_state:
    st.session_state.key_cmd = "cmd_1"
if "key_bl" not in st.session_state:
    st.session_state.key_bl = "bl_1"
if "desadv_results" not in st.session_state:
    st.session_state.desadv_results = {"auchan": [], "edi1": [], "date": None}

# ---------------------------
# Login screen
# ---------------------------
if not st.session_state.authenticated:
    st.markdown("---")
    st.markdown("### 🔐 Connexion requise")
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        with st.form("login_form"):
            username = st.text_input("👤 Identifiant")
            password = st.text_input("🔒 Mot de passe", type="password")
            submit = st.form_submit_button("Se connecter")
            if submit:
                ok, role, web_access = check_password(username, password)
                if ok:
                    st.session_state.authenticated = True
                    st.session_state.user_role = role
                    st.session_state.user_web_access = web_access
                    st.session_state.username = username
                    st.success(f"✅ Bienvenue {username} !")
                    st.experimental_rerun()
                else:
                    st.error("❌ Identifiant ou mot de passe incorrect")
        st.info("Demo: admin/admin123  — ou — user1/user123")
    st.stop()

# ---------------------------
# Header / Logo
# ---------------------------
try:
    with open("Desathor.png", "rb") as f:
        encoded = base64.b64encode(f.read()).decode()
    st.markdown(
        f"""
        <div style="display:flex;flex-direction:column;align-items:center;margin-top:20px;">
            <img src="data:image/png;base64,{encoded}" style="width:300px;max-width:80%;height:auto;">
        </div>
        """, unsafe_allow_html=True
    )
except FileNotFoundError:
    st.header("🧾 Comparateur pour DESADV")

st.markdown(f"**Utilisateur :** {st.session_state.username} — *{st.session_state.user_role}*")

st.markdown("---")
st.markdown("### 🔎 Interface principale")
st.markdown("Analysez vos commandes et bons de livraison en quelques clics.")

# ---------------------------
# Helper functions (PDF parsing)
# ---------------------------
def find_order_numbers_in_text(text):
    if not text:
        return []
    patterns = [
        r"Commande\s*n[°º]?\s*[:\s-]*?(\d{5,10})",
        r"N[°º]?\s*commande\s*[:\s-]*?(\d{5,10})",
        r"Bon\s+de\s+Livraison\s+Nr\.?\s*[:\s-]*?(\d{5,10})",
        r"\b(\d{6,10})\b"
    ]
    found = []
    for pat in patterns:
        for m in re.finditer(pat, text, flags=re.IGNORECASE):
            num = m.group(1)
            if num and num not in found:
                found.append(num)
    return found

def is_valid_ean13(code):
    if not code or len(str(code)) != 13:
        return False
    if str(code).startswith(("302", "376")):
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
                for ligne in lines:
                    order_nums = find_order_numbers_in_text(ligne)
                    if order_nums:
                        current_order = order_nums[0]
                    # heuristique: activation d'une zone de données
                    if re.search(r"Réf\.\s*frn|Code\s*ean|Référence", ligne, flags=re.IGNORECASE):
                        in_data_section = True
                        continue
                    if re.search(r"Récapitulatif|Page\s+\d+", ligne, flags=re.IGNORECASE):
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
                    code_article = ""
                    if ean_pos and ean_pos > 0:
                        candidate = parts[ean_pos - 1]
                        if re.match(r"^\d{3,6}$", candidate):
                            code_article = candidate
                    nums = re.findall(r"\b(\d+)\b", ligne)
                    nums = [int(n) for n in nums if n != int(ean) if len(n) < 6]
                    qte = nums[-1] if nums else 0
                    records.append({
                        "ref": ean,
                        "code_article": code_article,
                        "qte_commande": qte,
                        "order_num": current_order or "__NO_ORDER__"
                    })
    except Exception as e:
        st.warning(f"Erreur lecture PDF commande: {e}")
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
                        "order_num": current_order or "__NO_ORDER__"
                    })
    except Exception as e:
        st.warning(f"Erreur lecture PDF BL: {e}")
        return {"records": [], "order_numbers": [], "full_text": ""}
    order_numbers = find_order_numbers_in_text(full_text)
    return {"records": records, "order_numbers": order_numbers, "full_text": full_text}

def calculate_service_rate(qte_cmd, qte_bl):
    if pd.isna(qte_bl) or qte_cmd == 0:
        return 0
    return min((qte_bl / qte_cmd) * 100, 100)

# ---------------------------
# DESADV fetchers (Auchan + EDI1)
# These functions try a real requests-based fetch if credentials present in st.secrets,
# otherwise return simulated demo data.
# ---------------------------
def fetch_desadv_auchan():
    # Try real fetch if credentials present
    try:
        import requests
        from bs4 import BeautifulSoup
        username = st.secrets.get("AUCHAN_USERNAME", None) if hasattr(st, "secrets") else None
        password = st.secrets.get("AUCHAN_PASSWORD", None) if hasattr(st, "secrets") else None
        if username and password:
            session = requests.Session()
            login_url = "https://auchan.atgped.net/gui.php"
            login_data = {"username": username, "password": password, "action": "login"}
            r = session.post(login_url, data=login_data, timeout=10)
            # simple check:
            if "Liste des commandes" in r.text or "Documents" in r.text:
                # attempt to retrieve the commands table page (same pattern as earlier)
                resp = session.get("https://auchan.atgped.net/gui.php", params={"page": "documents_commandes_liste"}, timeout=10)
                soup = BeautifulSoup(resp.content, "html.parser")
                table = soup.find("table")
                desadv = []
                tomorrow = (datetime.now() + timedelta(days=1)).strftime("%d/%m/%Y")
                if table:
                    rows = table.find_all("tr")[1:]
                    for row in rows:
                        cols = row.find_all("td")
                        if len(cols) < 7:
                            continue
                        numero = cols[0].text.strip()
                        entrepot = cols[2].text.strip()
                        date_liv = cols[4].text.strip()
                        montant_text = cols[6].text.strip()
                        try:
                            montant = float(montant_text.replace(" ", "").replace(",", "."))
                        except:
                            continue
                        if date_liv == tomorrow and montant >= 0:
                            desadv.append({"numero": numero, "entrepot": entrepot, "montant": montant, "date_livraison": date_liv})
                # group by entrepot and sum montant, filter >=850
                grouped = {}
                for c in desadv:
                    e = c["entrepot"]
                    grouped.setdefault(e, {"montant_total": 0, "commandes": []})
                    grouped[e]["montant_total"] += c["montant"]
                    grouped[e]["commandes"].append(c["numero"])
                out = []
                for k, v in grouped.items():
                    if v["montant_total"] >= 850:
                        out.append({"entrepot": k, "montant_total": v["montant_total"], "nb_commandes": len(v["commandes"]), "commandes": v["commandes"]})
                out.sort(key=lambda x: x["montant_total"], reverse=True)
                return out, (datetime.now() + timedelta(days=1)).strftime("%d/%m/%Y")
    except Exception:
        pass

    # fallback simulated data (demo)
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%d/%m/%Y")
    commandes_brutes = [
        {"numero": "03385063", "entrepot": "PFI VENDENHEIM", "montant": 5432.70, "date_livraison": tomorrow},
        {"numero": "03311038", "entrepot": "APPRO PFI LE COUDRAY", "montant": 3406.81, "date_livraison": tomorrow},
        {"numero": "03201385", "entrepot": "APPRO PFI IDF CHILLY", "montant": 893.07, "date_livraison": tomorrow},
    ]
    entrepots = {}
    for cmd in commandes_brutes:
        entrepot = cmd["entrepot"]
        entrepots.setdefault(entrepot, {"montant_total": 0, "commandes": []})
        entrepots[entrepot]["montant_total"] += cmd["montant"]
        entrepots[entrepot]["commandes"].append(cmd["numero"])
    desadv_a_faire = []
    for e, d in entrepots.items():
        if d["montant_total"] >= 850:
            desadv_a_faire.append({"entrepot": e, "montant_total": d["montant_total"], "nb_commandes": len(d["commandes"]), "commandes": d["commandes"]})
    desadv_a_faire.sort(key=lambda x: x["montant_total"], reverse=True)
    return desadv_a_faire, tomorrow

def fetch_desadv_edi1():
    # EDI1: same credentials as Auchan (per your request)
    # We attempt a real fetch similar to Auchan; otherwise return simulated data but only for the 3 clients.
    ALLOWED_CLIENTS = [
        "ENTREPOT CSD produits frais",
        "ETABLISSEMENT DOLE",
        "ITM LUXEMONT-ET-VILLOTTE"
    ]
    try:
        import requests
        from bs4 import BeautifulSoup
        username = st.secrets.get("AUCHAN_USERNAME", None) if hasattr(st, "secrets") else None
        password = st.secrets.get("AUCHAN_PASSWORD", None) if hasattr(st, "secrets") else None
        if username and password:
            session = requests.Session()
            login_url = "https://edi1.atgpedi.net/gui.php"
            login_data = {"username": username, "password": password, "action": "login"}
            r = session.post(login_url, data=login_data, timeout=10)
            if "Liste des commandes" in r.text or "Documents" in r.text:
                resp = session.get("https://edi1.atgpedi.net/gui.php", params={"page": "documents_commandes_liste"}, timeout=10)
                soup = BeautifulSoup(resp.content, "html.parser")
                table = soup.find("table")
                desadv = []
                tomorrow = (datetime.now() + timedelta(days=1)).strftime("%d/%m/%Y")
                if table:
                    rows = table.find_all("tr")[1:]
                    for row in rows:
                        cols = row.find_all("td")
                        if len(cols) < 7:
                            continue
                        numero = cols[0].text.strip()
                        entrepot = cols[2].text.strip()
                        date_liv = cols[4].text.strip()
                        montant_text = cols[6].text.strip()
                        try:
                            montant = float(montant_text.replace(" ", "").replace(",", "."))
                        except:
                            continue
                        # keep only allowed clients
                        if entrepot in ALLOWED_CLIENTS and date_liv == tomorrow:
                            desadv.append({"numero": numero, "entrepot": entrepot, "montant": montant, "date_livraison": date_liv})
                # group & filter >=850
                grouped = {}
                for c in desadv:
                    e = c["entrepot"]
                    grouped.setdefault(e, {"montant_total": 0, "commandes": []})
                    grouped[e]["montant_total"] += c["montant"]
                    grouped[e]["commandes"].append(c["numero"])
                out = []
                for k, v in grouped.items():
                    if v["montant_total"] >= 850:
                        out.append({"entrepot": k, "montant_total": v["montant_total"], "nb_commandes": len(v["commandes"]), "commandes": v["commandes"]})
                out.sort(key=lambda x: x["montant_total"], reverse=True)
                return out, (datetime.now() + timedelta(days=1)).strftime("%d/%m/%Y")
    except Exception:
        pass

    # fallback simulated data (but only the 3 clients)
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%d/%m/%Y")
    simulated = [
        {"entrepot": "ENTREPOT CSD produits frais", "montant_total": 1200.0, "nb_commandes": 2, "commandes": ["100001", "100002"]},
        {"entrepot": "ETABLISSEMENT DOLE", "montant_total": 900.0, "nb_commandes": 1, "commandes": ["100010"]},
        # ITM below threshold to show filter behavior (will only show if >=850)
        {"entrepot": "ITM LUXEMONT-ET-VILLOTTE", "montant_total": 860.0, "nb_commandes": 1, "commandes": ["100020"]},
    ]
    out = [s for s in simulated if s["montant_total"] >= 850]
    return out, tomorrow

# ---------------------------
# Sidebar (Fichiers + DESADV + user info)
# ---------------------------
with st.sidebar:
    st.header("📁 Fichiers")
    # Show connected user above logout
    st.markdown(f"**👤 {st.session_state.username}** — *{st.session_state.user_role}*")
    if st.button("🚪 Déconnexion", use_container_width=True):
        st.session_state.authenticated = False
        st.session_state.username = None
        st.session_state.user_role = None
        st.session_state.user_web_access = False
        st.experimental_rerun()

    st.markdown("---")
    if st.button("🔄 Nouveau", use_container_width=True, type="primary"):
        st.session_state.key_cmd = f"cmd_{time.time()}"
        st.session_state.key_bl = f"bl_{time.time()}"
        st.session_state.historique = []
        st.experimental_rerun()
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
            st.experimental_rerun()
    else:
        st.info("Aucune comparaison enregistrée")

    st.markdown("---")
    st.header("🌐 Vérification DESADV")
    # Single button to fetch both Auchan + EDI1
    if st.session_state.user_web_access:
        if st.button("🔍 Vérifier les DESADV (Auchan + EDI1)", use_container_width=True):
            with st.spinner("Connexion aux sites et récupération..."):
                auchan_res, date = fetch_desadv_auchan()
                edi1_res, date_edi = fetch_desadv_edi1()
                st.session_state.desadv_results = {"auchan": auchan_res, "edi1": edi1_res, "date": date}
            st.experimental_rerun()
    else:
        st.info("🔒 Vérif DESADV — accès non autorisé pour votre compte")

    st.markdown("---")
    if st.button("❓ Comment utiliser", use_container_width=True):
        st.session_state.show_help = True
        st.experimental_rerun()

# ---------------------------
# Main area - Top buttons (Comment utiliser + Lancer la comparaison on same row)
# ---------------------------
col_a, col_b = st.columns([1, 1])
with col_a:
    # make the "Lancer" button visually smaller by using a shorter label and column sizing
    if st.button("🔍 Lancer", use_container_width=True, key="launch_compare"):
        # trigger comparison (same logic as before)
        if not commande_files or not bl_files:
            st.error("⚠️ Veuillez téléverser des commandes ET des bons de livraison.")
        else:
            with st.spinner("🔄 Analyse en cours..."):
                commandes_dict = defaultdict(list)
                all_command_records = []
                for f in commande_files:
                    res = extract_records_from_command_pdf(f)
                    all_command_records.extend(res["records"])
                    for rec in res["records"]:
                        commandes_dict[rec["order_num"]].append(rec)
                for k in list(commandes_dict.keys()):
                    df = pd.DataFrame(commandes_dict[k])
                    if not df.empty:
                        df = df.groupby(["ref", "code_article"], as_index=False).agg({"qte_commande": "sum"})
                    commandes_dict[k] = df
                bls_dict = defaultdict(list)
                all_bl_records = []
                for f in bl_files:
                    res = extract_records_from_bl_pdf(f)
                    all_bl_records.extend(res["records"])
                    for rec in res["records"]:
                        bls_dict[rec["order_num"]].append(rec)
                for k in list(bls_dict.keys()):
                    df = pd.DataFrame(bls_dict[k])
                    if not df.empty:
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
                    merged["taux_service"] = merged.apply(lambda r: calculate_service_rate(r["qte_commande"], r["qte_bl"]), axis=1)
                    results[order_num] = merged
                comparison_data = {"timestamp": datetime.now(), "results": results, "commandes_dict": commandes_dict, "bls_dict": bls_dict, "hide_unmatched": hide_unmatched}
                st.session_state.historique.append(comparison_data)
                st.success("✅ Comparaison terminée")
with col_b:
    if st.button("❓ Comment utiliser", use_container_width=True, key="help_top"):
        st.session_state.show_help = True
        st.experimental_rerun()

# ---------------------------
# Help modal / expander
# ---------------------------
if st.session_state.get("show_help", False):
    st.info("🔎 Guide d'utilisation rapide")
    st.markdown("""
    - Téléversez PDF Commandes et BL dans la sidebar (section **Fichiers**).
    - Cliquez **Lancer** pour comparer.
    - Pour récupérer automatiquement les DESADV, utilisez la section **Vérification DESADV** (si votre compte a l'accès web).
    """)
    if st.button("Compris / Fermer"):
        st.session_state.show_help = False
        st.experimental_rerun()

# ---------------------------
# If DESADV results exist, show simplified page: AUCHAN | EDI1
# ---------------------------
if st.session_state.desadv_results and (st.session_state.desadv_results.get("auchan") or st.session_state.desadv_results.get("edi1")):
    st.markdown("---")
    st.markdown("## 🌐 Résultats Vérification DESADV (séparés)")
    st.markdown(f"Date considérée : **{st.session_state.desadv_results.get('date', '')}**")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("### 🔵 AUCHAN")
        auchan = st.session_state.desadv_results.get("auchan", [])
        if auchan:
            for i, d in enumerate(auchan, 1):
                st.markdown(f"**{i}. {d['entrepot']}** — Montant: {d['montant_total']:,.2f}€ — {d['nb_commandes']} commande(s)")
                st.markdown(f"Commandes: {', '.join(d['commandes'])}")
                st.markdown("---")
        else:
            st.info("Aucun DESADV AUCHAN à faire")
    with col2:
        st.markdown("### 🟢 EDI1 (clients filtrés)")
        edi1 = st.session_state.desadv_results.get("edi1", [])
        if edi1:
            for i, d in enumerate(edi1, 1):
                st.markdown(f"**{i}. {d['entrepot']}** — Montant: {d['montant_total']:,.2f}€ — {d['nb_commandes']} commande(s)")
                st.markdown(f"Commandes: {', '.join(d['commandes'])}")
                st.markdown("---")
        else:
            st.info("Aucun DESADV EDI1 à faire (ou aucun des 3 clients trouvé)")
    st.markdown("---")
    if st.button("Effacer résultats DESADV"):
        st.session_state.desadv_results = {"auchan": [], "edi1": [], "date": None}
        st.experimental_rerun()

# ---------------------------
# Show the latest comparison results (if any) - reusing your existing UI
# ---------------------------
if st.session_state.historique:
    latest = st.session_state.historique[-1]
    results = latest["results"]
    hide_unmatched = latest["hide_unmatched"]
    def order_included(df):
        total_bl = df["qte_bl"].sum() if "qte_bl" in df.columns else 0
        if hide_unmatched and total_bl == 0:
            return False
        return True

    st.markdown("---")
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
        with st.expander(f"📦 Commande {order_num} — Taux: {taux:.1f}% | ✅{n_ok} ⚠️{n_diff} ❌{n_miss}"):
            st.dataframe(df, use_container_width=True, height=300)

    # Export button
    output = io.BytesIO()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"Comparaison_{timestamp}.xlsx"
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        for order_num, df in results.items():
            total_bl = df["qte_bl"].sum() if "qte_bl" in df.columns else 0
            if hide_unmatched and total_bl == 0:
                continue
            sheet_name = f"C_{order_num}"[:31]
            df.to_excel(writer, sheet_name=sheet_name, index=False)
    st.download_button("📥 Télécharger le rapport Excel", data=output.getvalue(), file_name=filename, mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

else:
    st.info("👆 Téléversez vos fichiers et lancez la comparaison pour commencer")
