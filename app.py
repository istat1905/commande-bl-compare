import streamlit as st
import pdfplumber
import pandas as pd
import io
import re

st.title("🧾 Comparateur Commande vs Bon de livraison")
st.write("Déposez les deux PDF ci-dessous pour obtenir les différences.")

# Upload PDFs
commande_file = st.file_uploader("📥 PDF Commande client", type=["pdf"])
bl_file = st.file_uploader("📥 PDF Bon de livraison", type=["pdf"])

# Extraction commande par code-barres
def extraire_commande(pdf_bytes):
    donnees = []
    with pdfplumber.open(pdf_bytes) as pdf:
        for page in pdf.pages:
            texte = page.extract_text()
            if not texte:
                continue
            lignes = texte.split("\n")
            for ligne in lignes:
                # Chercher un code-barres : 13 chiffres
                match = re.search(r"\b(\d{13})\b", ligne)
                if match:
                    codebarre = match.group(1)
                    # chercher la quantité juste avant la dernière valeur EUR
                    qte_match = re.findall(r"\b\d+\b", ligne)
                    if len(qte_match) >= 2:
                        qte = int(qte_match[-2])
                        donnees.append({"ref": codebarre, "qte_commande": qte})
    df = pd.DataFrame(donnees).drop_duplicates("ref")
    if df.empty:
        df = pd.DataFrame(columns=["ref", "qte_commande"])
    return df

# Extraction BL par code-barres
def extraire_bl(pdf_bytes):
    donnees = []
    with pdfplumber.open(pdf_bytes) as pdf:
        for page in pdf.pages:
            texte = page.extract_text()
            if not texte:
                continue
            lignes = texte.split("\n")
            for ligne in lignes:
                match = re.search(r"\b(\d{13})\b", ligne)
                if match:
                    codebarre = match.group(1)
                    # quantité = le nombre juste avant le code-barres
                    qte_match = re.findall(r"[\d,.]+", ligne)
                    if len(qte_match) >= 2:
                        try:
                            qte = float(qte_match[-2].replace(",", "."))
                            donnees.append({"ref": codebarre, "qte_bl": qte})
                        except:
                            continue
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
            # Afficher texte pour debug
            st.subheader("📄 Texte brut Commande")
            with pdfplumber.open(commande_file) as pdf:
                for page in pdf.pages:
                    st.text(page.extract_text())
            st.subheader("📄 Texte brut BL")
            with pdfplumber.open(bl_file) as pdf:
                for page in pdf.pages:
                    st.text(page.extract_text())
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
