import streamlit as st
import pdfplumber
import pandas as pd
import io
import re
from collections import defaultdict
from datetime import datetime
import time  # ajouté pour générer des clés uniques pour les uploaders

# Vérifier si plotly est disponible
try:
    import plotly.express as px
    import plotly.graph_objects as go
    PLOTLY_AVAILABLE = True
except ImportError:
    PLOTLY_AVAILABLE = False
    st.warning("⚠️ Plotly non installé. Les graphiques ne seront pas affichés. Installez-le avec: `pip install plotly`")

st.set_page_config(
    page_title="Comparateur Commande vs BL",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Initialiser le session state
if 'historique' not in st.session_state:
    st.session_state.historique = []

# --- Clés dynamiques pour reset propre des file_uploaders (NE PAS toucher ces clés directement ailleurs) ---
if "key_cmd" not in st.session_state:
    st.session_state.key_cmd = "cmd_1"
if "key_bl" not in st.session_state:
    st.session_state.key_bl = "bl_1"

# --------------------------
# Styles CSS personnalisés
# --------------------------
st.markdown("""
<style>
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

st.markdown('<h1 class="main-header">🧾 Comparateur Commande vs Bon de livraison</h1>', unsafe_allow_html=True)
st.markdown('<p class="subtitle">Analysez vos commandes et bons de livraison en quelques clics</p>', unsafe_allow_html=True)

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
    """Calcule le taux de service"""
    if pd.isna(qte_bl) or qte_cmd == 0:
        return 0
    return min((qte_bl / qte_cmd) * 100, 100)

# --------------------------
# Sidebar
# --------------------------
with st.sidebar:
    st.header("📁 Fichiers")
    
    # Bouton Nouveau comparatif -> utilise clés dynamiques pour reset safe
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
        "Masquer les commandes non matchés dans l'Excel",
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

# --------------------------
# Traitement
# --------------------------
if st.button("🔍 Lancer la comparaison", use_container_width=True, type="primary"):
    if not commande_files or not bl_files:
        st.error("⚠️ Veuillez téléverser des commandes ET des bons de livraison.")
        st.stop()

    with st.spinner("🔄 Analyse en cours..."):
        # Extraction des commandes
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

        # Extraction des BL
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

        # Matching et statuts
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
        
        # Sauvegarder dans l'historique
        comparison_data = {
            "timestamp": datetime.now(),
            "results": results,
            "commandes_dict": commandes_dict,
            "bls_dict": bls_dict,
            "hide_unmatched": hide_unmatched
        }
        st.session_state.historique.append(comparison_data)

# --------------------------
# Affichage des résultats
# --------------------------
if st.session_state.historique:
    latest = st.session_state.historique[-1]
    results = latest["results"]
    commandes_dict = latest["commandes_dict"]
    bls_dict = latest["bls_dict"]
    hide_unmatched = latest["hide_unmatched"]
    
    # helper : inclure commande ? (si hide_unmatched True on exclut celles avec qte_bl total == 0)
    def order_included(df):
        total_bl = df["qte_bl"].sum() if "qte_bl" in df.columns else 0
        if hide_unmatched and total_bl == 0:
            return False
        return True

    # Calculer les KPIs globaux (en excluant les commandes non matchées si hide_unmatched)
    total_commande = sum([df["qte_commande"].sum() for df in results.values() if order_included(df)])
    total_livre = sum([df["qte_bl"].sum() for df in results.values() if order_included(df)])
    total_manquant = total_commande - total_livre
    taux_service_global = (total_livre / total_commande * 100) if total_commande > 0 else 0
    
    total_articles_ok = sum([(df["status"] == "OK").sum() for df in results.values() if order_included(df)])
    total_articles_diff = sum([(df["status"] == "QTY_DIFF").sum() for df in results.values() if order_included(df)])
    total_articles_missing = sum([(df["status"] == "MISSING_IN_BL").sum() for df in results.values() if order_included(df)])
    
    # KPIs en haut
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
    
    # Graphiques
    col1, col2 = st.columns(2)
    
    if PLOTLY_AVAILABLE:
        with col1:
            # Répartition des statuts (sur commandes incluses)
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
            # Taux de service par commande (en excluant commandes non matchées si hide_unmatched)
            service_rates = []
            for order_num, df in results.items():
                if not order_included(df):
                    continue
                total_cmd = df["qte_commande"].sum()
                total_bl = df["qte_bl"].sum()
                rate = (total_bl / total_cmd * 100) if total_cmd > 0 else 0
                service_rates.append({
                    'Commande': order_num,
                    'Taux de service': rate
                })
            
            df_service = pd.DataFrame(service_rates)
            if not df_service.empty:
                fig_service = px.bar(
                    df_service,
                    x='Commande',
                    y='Taux de service',
                    title='Taux de service par commande',
                    color='Taux de service',
                    color_continuous_scale=['#ff6b6b', '#ffd93d', '#38ef7d'],
                    range_color=[0, 100]
                )
                fig_service.update_layout(showlegend=False)
                st.plotly_chart(fig_service, use_container_width=True)
            else:
                st.info("Aucune commande à afficher (filtrage actif ou pas de données).")
    else:
        # Version sans graphiques
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
    
    # Tabs
    tabs = st.tabs(["📋 Détails commandes", "📈 Statistiques", "🏆 Top produits"])
    
    with tabs[0]:
        st.markdown("### 🔎 Détails par commande")
        
        for order_num, df in results.items():
            # skip commandes non matchées si hide_unmatched activé
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
    
    with tabs[1]:
        st.markdown("### 📈 Articles manquants par code article")
        
        # Créer un DataFrame des articles manquants (en respectant le filtre)
        missing_articles = []
        for order_num, df in results.items():
            if not order_included(df):
                continue
            missing = df[df["status"] == "MISSING_IN_BL"]
            for _, row in missing.iterrows():
                missing_articles.append({
                    "Code article": row["code_article"],
                    "EAN": row["ref"],
                    "Commande": order_num,
                    "Qté commandée": int(row["qte_commande"])
                })
        
        if missing_articles:
            df_missing = pd.DataFrame(missing_articles)
            
            # Top des codes articles manquants
            top_missing = df_missing.groupby("Code article")["Qté commandée"].sum().sort_values(ascending=False).head(10)
            
            if PLOTLY_AVAILABLE:
                fig_missing = px.bar(
                    x=top_missing.values,
                    y=top_missing.index.astype(str),
                    orientation='h',
                    title='Top 10 des codes articles manquants',
                    labels={'x': 'Quantité totale', 'y': 'Code article'},
                    color=top_missing.values,
                    color_continuous_scale='Reds'
                )
                fig_missing.update_layout(showlegend=False)
                st.plotly_chart(fig_missing, use_container_width=True)
            else:
                st.bar_chart(top_missing)
            
            st.dataframe(df_missing, use_container_width=True)
        else:
            st.success("✅ Aucun article manquant !")
    
    with tabs[2]:
        st.markdown("### 🏆 Classement des produits")
        
        # Top commandés (en respectant filtre)
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
        
        df_products = pd.DataFrame(all_products) if all_products else pd.DataFrame(columns=["Code article", "EAN", "Qté commandée", "Qté livrée"])
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.markdown("#### 📦 Top 10 commandés")
            if not df_products.empty:
                top_cmd = df_products.groupby("Code article")["Qté commandée"].sum().sort_values(ascending=False).head(10)
                st.dataframe(top_cmd.reset_index(), use_container_width=True)
            else:
                st.info("Aucun produit à afficher.")
        
        with col2:
            st.markdown("#### 📋 Top 10 livrés")
            if not df_products.empty:
                top_livre = df_products.groupby("Code article")["Qté livrée"].sum().sort_values(ascending=False).head(10)
                st.dataframe(top_livre.reset_index(), use_container_width=True)
            else:
                st.info("Aucun produit à afficher.")
    
    # Export Excel
    st.markdown("---")
    st.markdown("### 📥 Export")
    
    output = io.BytesIO()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"Comparaison_{timestamp}.xlsx"
    
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        # Écrire chaque commande
        for order_num, df in results.items():
            # Filtrer si nécessaire : skip commandes non matchées si hide_unmatched activé
            total_bl = df["qte_bl"].sum() if "qte_bl" in df.columns else 0
            if hide_unmatched and total_bl == 0:
                continue

            # Filtrer si nécessaire au niveau des lignes (conserver le comportement original pour les lignes)
            if hide_unmatched:
                # On garde toutes les lignes de la commande (si tu souhaites exclure aussi les lignes MISSING_IN_BL, adapte ici)
                df_export = df.copy()
            else:
                df_export = df.copy()
            
            # safe sheet name
            sheet_name = f"C_{order_num}"[:31]
            df_export.to_excel(writer, sheet_name=sheet_name, index=False)
            
            # Formatage
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
        
        # Ajouter une feuille récapitulative
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

else:
    st.info("👆 Téléversez vos fichiers et lancez la comparaison pour commencer")

    # --- Signature 5 étoiles ---
st.markdown("""
<div style='text-align: center; margin-top: 40px; font-size: 18px; color: #888;'>
    ⭐⭐⭐⭐⭐<br>
    <strong>Powered by IC - 2025</strong>
</div>
""", unsafe_allow_html=True)


