import streamlit as st
import pdfplumber
import pandas as pd
import io
import re
from collections import defaultdict
from datetime import datetime
import time
import base64
import os

try:
    import plotly.express as px
    import plotly.graph_objects as go
    PLOTLY_AVAILABLE = True
except ImportError:
    PLOTLY_AVAILABLE = False

st.set_page_config(
    page_title="DESATHOR",
    layout="wide",
    initial_sidebar_state="expanded"
)

# -- CSS / Logo -------------------------------------------------------------
st.markdown("""
<style>
    .logo-container { display:flex; justify-content:center; margin-top:10px; margin-bottom:20px; }
    .main-header { font-size:2.5rem; font-weight:700; color:#1f77b4; margin-bottom:0.5rem; }
    .subtitle { font-size:1.1rem; color:#666; margin-bottom:2rem;}
    .kpi-card { background: linear-gradient(135deg,#667eea 0%,#764ba2 100%); padding:1.5rem; border-radius:10px; color:white; text-align:center; box-shadow:0 4px 6px rgba(0,0,0,0.1); }
    .kpi-value { font-size:2.5rem; font-weight:bold; margin:0.5rem 0;}
    .kpi-label { font-size:0.9rem; opacity:0.9; }
</style>
""", unsafe_allow_html=True)

# Load logo safely (non-blocking)
logo_path = "Desathor.png"
if os.path.exists(logo_path):
    try:
        with open(logo_path, "rb") as f:
            data = f.read()
        encoded = base64.b64encode(data).decode()
        st.markdown(
            f'<div class="logo-container"><img src="data:image/png;base64,{encoded}" style="width:250px; max-width:80%; height:auto;"></div>',
            unsafe_allow_html=True
        )
    except Exception:
        pass

# -------------------------
# Session state defaults
# -------------------------
if "historique" not in st.session_state:
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
if "username" not in st.session_state:
    st.session_state.username = None
# keep a user_web_access key to avoid AttributeError elsewhere
if "user_web_access" not in st.session_state:
    st.session_state.user_web_access = False

# -------------------------
# Simple users DB (demo)
# -------------------------
# Replace with a real DB + hashed passwords in production
USERS_DB = {
    "ISA": {"password": "admin123", "role": "admin", "web_access": True},
    "bak": {"password": "bak123", "role": "user", "web_access": False},
}

def check_password(username, password):
    if username in USERS_DB and USERS_DB[username]["password"] == password:
        return True, USERS_DB[username]["role"], USERS_DB[username]["web_access"]
    return False, None, False

def save_user(username, password, role, web_access):
    USERS_DB[username] = {"password": password, "role": role, "web_access": web_access}
    return True

def delete_user(username):
    if username in USERS_DB and username != "ISA":
        del USERS_DB[username]
        return True
    return False

# -------------------------
# PDF parsing helpers
# -------------------------
def find_order_numbers_in_text(text):
    if not text:
        return []
    patterns = [
        r"Commande\s*n[°º]?\s*[:\s-]*?(\d{4,12})",
        r"N[°º]?\s*commande\s*[:\s-]*?(\d{4,12})",
        r"Bon\s+de\s+Livraison\s+Nr\.?\s*[:\s-]*?(\d{4,12})",
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
    # checksum
    digits = [int(c) for c in s]
    checksum = digits[-1]
    evens = sum(digits[-2::-2])
    odds = sum(digits[-3::-2])
    total = odds + evens * 3
    calc = (10 - (total % 10)) % 10
    return calc == checksum

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
                    # simple heuristic: lines with EAN and qty
                    ean_matches = re.findall(r"\b(\d{13})\b", ligne)
                    valid_eans = [ean for ean in ean_matches if is_valid_ean13(ean)]
                    if not valid_eans:
                        continue
                    ean = valid_eans[0]
                    # try to find qty labels
                    qte = None
                    m = re.search(r"(?:Qt[eé]e|QTE|Qty|Quantit[eé])\s*[:\-]?\s*([0-9]+(?:[.,][0-9]+)?)", ligne, flags=re.IGNORECASE)
                    if m:
                        try:
                            qte = int(float(m.group(1).replace(",", ".")))
                        except:
                            qte = None
                    if qte is None:
                        nums = re.findall(r"\b(\d{1,6})\b", ligne)
                        nums = [int(n) for n in nums if len(n) < 7]
                        if nums:
                            qte = nums[-1]
                    if qte is None:
                        continue
                    # code article: token before EAN if numeric
                    parts = ligne.split()
                    code_article = ""
                    for i, p in enumerate(parts):
                        if ean in p:
                            if i > 0:
                                cand = re.sub(r"\D", "", parts[i-1])
                                if 2 <= len(cand) <= 6:
                                    code_article = cand
                            break
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
                    qte = None
                    m = re.search(r"(?:Qt[eé]e|QTE|Qty|Quantit[eé])\s*[:\-]?\s*([0-9]+(?:[.,][0-9]+)?)", ligne, flags=re.IGNORECASE)
                    if m:
                        try:
                            qte = float(m.group(1).replace(",", "."))
                        except:
                            qte = None
                    if qte is None:
                        nums = re.findall(r"([0-9]+(?:[.,][0-9]+)?)", ligne)
                        if nums:
                            try:
                                qte = float(nums[-1].replace(",", "."))
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

# -------------------------
# Login page
# -------------------------
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
        st.info("💡 Demo: ISA / admin123  or bak / bak123")
    st.stop()

# -------------------------
# Header
# -------------------------
st.markdown('<h1 class="main-header">🧾 Comparateur pour DESADV</h1>', unsafe_allow_html=True)
st.markdown(f'<p class="subtitle">Bienvenue {st.session_state.username} ({st.session_state.user_role})</p>', unsafe_allow_html=True)

# -------------------------
# Sidebar (files, options, history, user management)
# -------------------------
with st.sidebar:
    st.markdown(f"### 👤 {st.session_state.username}")
    st.caption(f"Rôle: {st.session_state.user_role}")

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
    st.header("📁 Fichiers")
    commande_files = st.file_uploader("📦 PDF(s) Commande client", type="pdf", accept_multiple_files=True, key=st.session_state.key_cmd)
    bl_files = st.file_uploader("📋 PDF(s) Bon de livraison", type="pdf", accept_multiple_files=True, key=st.session_state.key_bl)

    st.markdown("---")
    st.header("⚙️ Options")
    hide_unmatched = st.checkbox("👁️‍🗨️ Masquer les commandes sans correspondance", value=True, help="Exclut les articles MISSING_IN_BL de l'export Excel")

    st.markdown("---")
    st.header("📊 Historique")
    if st.session_state.historique:
        st.write(f"**{len(st.session_state.historique)}** comparaison(s) enregistrée(s)")
        if st.button("🗑️ Supprimer tout l'historique", use_container_width=True):
            st.session_state.historique = []
            st.success("Historique supprimé")
            st.rerun()
    else:
        st.info("Aucune comparaison enregistrée")

    # User management (admin only)
    if st.session_state.user_role == "admin":
        st.markdown("---")
        st.header("👥 Gestion utilisateurs")
        if st.button("⚙️ Gérer les utilisateurs", use_container_width=True):
            st.session_state.show_help = "manage_users"
            st.rerun()

    st.markdown("---")
    if st.button("❓ Aide", use_container_width=True):
        st.session_state.show_help = "guide"
        st.rerun()

# -------------------------
# Main actions: compare
# -------------------------
col1, col2 = st.columns([4, 1])
with col1:
    launch_button = st.button("🔍 Lancer la comparaison", use_container_width=True, type="primary")
with col2:
    if st.button("❓ Aide", use_container_width=True):
        st.session_state.show_help = "guide"
        st.rerun()

if launch_button:
    if not commande_files or not bl_files:
        st.error("⚠️ Veuillez téléverser des commandes ET des bons de livraison.")
        st.stop()

    with st.spinner("🔄 Analyse en cours..."):
        commandes_dict = defaultdict(list)
        for f in commande_files:
            res = extract_records_from_command_pdf(f)
            for rec in res["records"]:
                commandes_dict[rec["order_num"]].append(rec)
        for k in list(commandes_dict.keys()):
            df = pd.DataFrame(commandes_dict[k])
            if df.empty:
                commandes_dict[k] = pd.DataFrame(columns=["ref", "code_article", "qte_commande"])
            else:
                df = df.groupby(["ref", "code_article"], as_index=False).agg({"qte_commande": "sum"})
                commandes_dict[k] = df

        bls_dict = defaultdict(list)
        for f in bl_files:
            res = extract_records_from_bl_pdf(f)
            for rec in res["records"]:
                bls_dict[rec["order_num"]].append(rec)
        for k in list(bls_dict.keys()):
            df = pd.DataFrame(bls_dict[k])
            if df.empty:
                bls_dict[k] = pd.DataFrame(columns=["ref", "qte_bl"])
            else:
                df = df.groupby("ref", as_index=False).agg({"qte_bl": "sum"})
                bls_dict[k] = df

        results = {}
        for order_num, df_cmd in commandes_dict.items():
            df_bl = bls_dict.get(order_num, pd.DataFrame(columns=["ref", "qte_bl"]))
            merged = pd.merge(df_cmd, df_bl, on="ref", how="left")
            if "qte_commande" not in merged.columns:
                merged["qte_commande"] = 0
            merged["qte_commande"] = pd.to_numeric(merged["qte_commande"], errors="coerce").fillna(0)
            if "qte_bl" not in merged.columns:
                merged["qte_bl"] = 0
            merged["qte_bl"] = pd.to_numeric(merged["qte_bl"], errors="coerce").fillna(0)
            def status_row(r):
                if r["qte_bl"] == 0:
                    return "MISSING_IN_BL"
                return "OK" if r["qte_commande"] == r["qte_bl"] else "QTY_DIFF"
            merged["status"] = merged.apply(status_row, axis=1)
            merged["diff"] = merged["qte_bl"] - merged["qte_commande"]
            merged["taux_service"] = merged.apply(lambda r: calculate_service_rate(r["qte_commande"], r["qte_bl"]), axis=1)
            results[order_num] = merged

        comparison_data = {
            "timestamp": datetime.now(),
            "results": results,
            "commandes_dict": commandes_dict,
            "bls_dict": bls_dict,
            "hide_unmatched": hide_unmatched
        }
        st.session_state.historique.append(comparison_data)
        st.success("✅ Comparaison terminée")

# -------------------------
# Display last result
# -------------------------
if st.session_state.historique:
    latest = st.session_state.historique[-1]
    results = latest["results"]
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
        with st.expander(f"📦 Commande {order_num} — Taux de service: {taux:.1f}% | ✅ {n_ok} | ⚠️ {n_diff} | ❌ {n_miss}"):
            c1, c2, c3 = st.columns(3)
            c1.metric("Commandé", int(total_cmd))
            c2.metric("Livré", int(total_bl))
            c3.metric("Manquant", int(total_cmd - total_bl))
            def color_status(val):
                if val == "OK":
                    return "background-color: #d4edda"
                if val == "QTY_DIFF":
                    return "background-color: #fff3cd"
                if val == "MISSING_IN_BL":
                    return "background-color: #f8d7da"
                return ""
            st.dataframe(df.style.applymap(color_status, subset=["status"]), use_container_width=True, height=350)

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
            sheet_name = f"C_{order_num}"[:31]
            df.to_excel(writer, sheet_name=sheet_name, index=False)
            workbook = writer.book
            worksheet = writer.sheets[sheet_name]
            ok_format = workbook.add_format({'bg_color': '#d4edda'})
            diff_format = workbook.add_format({'bg_color': '#fff3cd'})
            miss_format = workbook.add_format({'bg_color': '#f8d7da'})
            for idx, row in df.iterrows():
                excel_row = idx + 1
                if row.get('status') == 'OK':
                    worksheet.set_row(excel_row, None, ok_format)
                elif row.get('status') == 'QTY_DIFF':
                    worksheet.set_row(excel_row, None, diff_format)
                elif row.get('status') == 'MISSING_IN_BL':
                    worksheet.set_row(excel_row, None, miss_format)
        # summary
        summary = {
            'Commande': [], 'Taux de service (%)': [], 'Qté commandée': [], 'Qté livrée': [], 'Qté manquante': [],
            'Articles OK': [], 'Articles différence': [], 'Articles manquants': []
        }
        for order_num, df in results.items():
            total_bl = df["qte_bl"].sum() if "qte_bl" in df.columns else 0
            if hide_unmatched and total_bl == 0:
                continue
            total_cmd = df["qte_commande"].sum()
            taux = (total_bl / total_cmd * 100) if total_cmd > 0 else 0
            summary['Commande'].append(order_num)
            summary['Taux de service (%)'].append(round(taux, 2))
            summary['Qté commandée'].append(int(total_cmd))
            summary['Qté livrée'].append(int(total_bl))
            summary['Qté manquante'].append(int(total_cmd - total_bl))
            summary['Articles OK'].append((df["status"] == "OK").sum())
            summary['Articles différence'].append((df["status"] == "QTY_DIFF").sum())
            summary['Articles manquants'].append((df["status"] == "MISSING_IN_BL").sum())
        pd.DataFrame(summary).to_excel(writer, sheet_name="Récapitulatif", index=False)

    c1, c2 = st.columns([3,1])
    with c1:
        st.download_button("📥 Télécharger le rapport Excel", data=output.getvalue(), file_name=filename, mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)
    with c2:
        if st.button("🗑️ Supprimer ce résultat", use_container_width=True):
            st.session_state.historique.pop()
            st.rerun()

# -------------------------
# Help and User Management modal-like pages
# -------------------------
if st.session_state.show_help == "guide":
    st.markdown("---")
    st.markdown("## 📖 Guide d'utilisation")
    with st.expander("🚀 Démarrage rapide", expanded=True):
        st.markdown("""
        1. Téléversez vos PDF: Commandes et Bons de livraison.
        2. Cliquez "Lancer la comparaison".
        3. Consultez et téléchargez le rapport Excel.
        """)
    if st.button("✅ Compris, retour à l'outil"):
        st.session_state.show_help = False
        st.rerun()

elif st.session_state.show_help == "manage_users":
    # Admin-only user management interface
    if st.session_state.user_role != "admin":
        st.error("🔒 Accès refusé")
    else:
        st.markdown("---")
        st.markdown("## 👥 Gestion des utilisateurs")
        tabs = st.tabs(["📋 Liste", "➕ Ajouter", "✏️ Modifier / Supprimer"])
        with tabs[0]:
            st.markdown("### Liste des utilisateurs")
            users_data = []
            for username, data in USERS_DB.items():
                users_data.append({"Utilisateur": username, "Rôle": data["role"], "Accès DESADV": "✅" if data["web_access"] else "❌"})
            st.dataframe(pd.DataFrame(users_data), use_container_width=True, hide_index=True)
        with tabs[1]:
            st.markdown("### Ajouter un utilisateur")
            with st.form("add_user"):
                new_username = st.text_input("👤 Nom d'utilisateur")
                new_password = st.text_input("🔒 Mot de passe", type="password")
                new_role = st.selectbox("Rôle", ["user", "admin"])
                new_web_access = st.checkbox("Accès vérification DESADV (info only)", value=False)
                if st.form_submit_button("➕ Ajouter"):
                    if not new_username or not new_password:
                        st.error("Veuillez remplir tous les champs")
                    elif new_username in USERS_DB:
                        st.error("Cet utilisateur existe déjà")
                    else:
                        save_user(new_username, new_password, new_role, new_web_access)
                        st.success(f"Utilisateur {new_username} ajouté")
                        st.experimental_rerun()
        with tabs[2]:
            st.markdown("### Modifier / Supprimer un utilisateur")
            user_to_edit = st.selectbox("Sélectionner", list(USERS_DB.keys()))
            if user_to_edit:
                current = USERS_DB[user_to_edit]
                with st.form("edit_user"):
                    edit_password = st.text_input("🔒 Nouveau mot de passe (laisser vide pour ne pas changer)", type="password")
                    edit_role = st.selectbox("Rôle", ["user", "admin"], index=0 if current["role"] == "user" else 1)
                    edit_web_access = st.checkbox("Accès vérification DESADV (info only)", value=current["web_access"])
                    if st.form_submit_button("💾 Sauvegarder"):
                        new_pwd = edit_password if edit_password else current["password"]
                        save_user(user_to_edit, new_pwd, edit_role, edit_web_access)
                        st.success(f"Utilisateur {user_to_edit} modifié")
                        st.experimental_rerun()
                if user_to_edit != "ISA":
                    if st.button("🗑️ Supprimer cet utilisateur"):
                        if delete_user(user_to_edit):
                            st.success(f"Utilisateur {user_to_edit} supprimé")
                            st.experimental_rerun()
                        else:
                            st.error("Impossible de supprimer cet utilisateur")
    if st.button("↩️ Retour"):
        st.session_state.show_help = False
        st.rerun()

# Footer
st.markdown("<div style='text-align:center; margin-top:40px; font-size:14px; color:#888;'>Powered by IC - 2025</div>", unsafe_allow_html=True)
