import streamlit as st
import pdfplumber
import pandas as pd
import io
import re

st.title("🧾 Comparateur Commande vs Bon de livraison")
st.write("Déposez les deux PDF ci-dessous pour obtenir les différences.")

# Uploads
commande_file = st.file_uploader("📥 PDF Commande client", type=["pdf"])
bl_file = st.file_uploader("📥 PDF Bon de livraison", type=["pdf"])

# Extraction de la commande
def extraire_commande(pdf_bytes):
    donnees = []
    with pdfplumber.open(pdf_bytes) as pdf:
        for page in pdf.pages:
            texte = page.extract_text()
            if not texte:
                continue
            lignes = texte.split("\n")
            for ligne in lignes:
                # On cherche des lignes commençant par un numéro de ligne
                parts = ligne.split()
                if len(parts) < 2:
                    continue
                if not parts[0].isdigit():
                    continue
                ref = parts[1]
                
                # Chercher Qté commandée automatiquement avant "Pcb"
                qte = None
                for i, val in enumerate(parts):
                    if val.lower() in ["pcb", "pcs"]:
                        if i >= 1:
                            try:
                                qte = int(parts[i-1].replace(",", ""))
                            except:
                                pass
                        break
                if qte is None:
                    # fallback sur 6ème colonne si regex échoue
                    try:
                        qte = int(parts[5])
                    except:
                        continue
                donnees.append({"ref": ref, "qte_commande": qte})
    df = pd.DataFrame(donnees).drop_duplicates("ref")
    if df.empty:
        df = pd.DataFrame(columns=["ref", "qte_commande"])
    return df

# Extraction du BL
def extraire_bl(pdf_bytes):
    donnees = []
    with pdfplumber.open(pdf_bytes) as pdf:
        for page in pdf.pages:
            texte = page.extract_text()
            if not texte:
                continue
            lignes = texte.split("\n")
            for ligne in lignes:
                parts = ligne.split()
                if len(parts) < 2:
                    continue
                if not parts[0].isdigit():
                    continue
                ref = parts[0]
                try:
                    qte = float(parts[-2].replace(",", "."))
                except:
                    continue
                donnees.append({"ref": ref, "qte_bl": qte})
    df = pd.DataFrame(donnees).groupby("ref", as_index=False).sum()
    if df.empty:
        df = pd.DataFrame(columns=["ref", "qte_bl"])
    return df

# Comparaison commande vs BL
def comparer(df_commande, df_bl):
    df = pd.merge(df_commande, df_bl, on="ref", how="left")
    manquants = df[df["qte_bl"].isna()]
    diff = df[df["qte_bl"].notna() & (df["qte_commande"] != df["qte_bl"])]
    ok = df[df["qte_commande"] == df["qte_bl"]]
    return manquants, diff, ok

# Bouton Comparer
if st.button("🔍 Comparer"):
    if not commande_file or not bl_file:
        st.error("Merci de télécharger les deux fichiers PDF.")
    else:
        df_commande = extraire_commande(commande_file)
        df_bl = extraire_bl(bl_file)

        if df_commande.empty or df_bl.empty:
            st.warning("⚠️ Aucun article trouvé dans un des PDFs. Vérifiez le format.")
        else:
            manquants, diff, ok = comparer(df_commande, df_bl)

            st.subheader("📌 Résultats :")
            st.write(f"**❌ Références manquantes dans le BL : {len(manquants)}**")
            st.dataframe(manquants)

            st.write(f"**⚠️ Différences de quantité : {len(diff)}**")
            st.dataframe(diff)

            st.write(f"**✅ Correspondances exactes : {len(ok)}**")
            st.dataframe(ok)

            # Export Excel
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
                manquants.to_excel(writer, sheet_name="Manquants", index=False)
                diff.to_excel(writer, sheet_name="Quantite_diff", index=False)
                ok.to_excel(writer, sheet_name="OK", index=False)

            st.download_button(
                label="📥 Télécharger le fichier Excel",
                data=output.getvalue(),
                file_name="Differences.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
