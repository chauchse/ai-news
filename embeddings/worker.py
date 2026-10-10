import os
import re
import psycopg

from sentence_transformers import SentenceTransformer
from sentence_transformers.util import cos_sim


# ============================================================
# CONFIGURATION
# ============================================================

MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
THRESHOLD = 0.80

DATABASE_URL = os.environ["DATABASE_URL"]


# ============================================================
# EXTRACTION DES CVE
# ============================================================

def extract_cves(text):
    """Extrait les identifiants CVE présents dans un texte."""

    return set(
        match.upper()
        for match in re.findall(
            r"CVE-\d{4}-\d{4,}",
            text or "",
            flags=re.IGNORECASE,
        )
    )


# ============================================================
# REGROUPEMENT DES ARTICLES
# ============================================================

def build_groups(articles, pairs):
    """
    Regroupe les articles reliés par une similarité suffisante.

    Utilise l'algorithme Union-Find pour construire
    les composantes connexes.
    """

    parent = list(range(len(articles)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]

        return x

    def union(a, b):
        root_a = find(a)
        root_b = find(b)

        if root_a != root_b:
            parent[root_b] = root_a

    # Fusion des articles similaires
    for score, i, j in pairs:
        union(i, j)

    # Construction des groupes
    groups = {}

    for i, article in enumerate(articles):
        root = find(i)
        groups.setdefault(root, []).append(article)

    # Groupes les plus importants en premier
    return sorted(
        groups.values(),
        key=lambda group: (-len(group), group[0][0])
    )


# ============================================================
# PROGRAMME PRINCIPAL
# ============================================================

def main():

    # --------------------------------------------------------
    # 1. Chargement du modèle IA
    # --------------------------------------------------------

    print("Chargement du modèle IA...")

    model = SentenceTransformer(
        MODEL_NAME,
        device="cpu"
    )

    # --------------------------------------------------------
    # 2. Récupération des articles
    # --------------------------------------------------------

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:

            cur.execute("""
                SELECT
                    id,
                    title,
                    normalized_summary
                FROM unique_articles
                WHERE published_at BETWEEN
                    NOW() - INTERVAL '48 hours' AND NOW()
                  AND relevance_score >= 40
                ORDER BY relevance_score DESC
            """)

            articles = cur.fetchall()

    print(f"\nArticles analysés : {len(articles)}")

    if len(articles) < 2:
        print("Pas assez d'articles pour effectuer une comparaison.")
        return

    # --------------------------------------------------------
    # 3. Préparation des textes
    # --------------------------------------------------------

    texts = [
        (
            summary.strip()
            if summary and summary.strip()
            else title or ""
        )
        for _, title, summary in articles
    ]

    # --------------------------------------------------------
    # 4. Génération des embeddings
    # --------------------------------------------------------

    print("Génération des embeddings...")

    vectors = model.encode(
        texts,
        normalize_embeddings=True,
        show_progress_bar=False,
        batch_size=16,
    )

    # --------------------------------------------------------
    # 5. Calcul des similarités
    # --------------------------------------------------------

    similarities = cos_sim(vectors, vectors)

    pairs = []

    for i in range(len(articles)):
        for j in range(i + 1, len(articles)):

            score = similarities[i][j].item()

            if score < THRESHOLD:
                continue

            # Vérification des CVE
            text_i = (
                f"{articles[i][1]} "
                f"{articles[i][2] or ''}"
            )

            text_j = (
                f"{articles[j][1]} "
                f"{articles[j][2] or ''}"
            )

            cves_i = extract_cves(text_i)
            cves_j = extract_cves(text_j)

            # Si les deux articles mentionnent des CVE
            # mais n'en partagent aucune, on évite la fusion.
            if cves_i and cves_j and not (cves_i & cves_j):
                continue

            pairs.append((score, i, j))

    pairs.sort(reverse=True)

    # --------------------------------------------------------
    # 6. Affichage des paires similaires
    # --------------------------------------------------------

    print(
        f"\nPaires similaires "
        f"(seuil {THRESHOLD}) : {len(pairs)}"
    )

    for score, i, j in pairs:

        print(f"\nSimilarité : {score:.3f}")
        print(f"  [{articles[i][0]}] {articles[i][1]}")
        print(f"  [{articles[j][0]}] {articles[j][1]}")

    # --------------------------------------------------------
    # 7. Construction des groupes
    # --------------------------------------------------------

    groups = build_groups(articles, pairs)

    # --------------------------------------------------------
    # 8. Statistiques
    # --------------------------------------------------------

    grouped_events = [
        group for group in groups
        if len(group) > 1
    ]

    print(f"\n{'=' * 60}")
    print("REGROUPEMENT DES ÉVÉNEMENTS")
    print(f"{'=' * 60}")

    print(f"Articles analysés      : {len(articles)}")
    print(f"Événements distincts   : {len(groups)}")
    print(f"Événements regroupés   : {len(grouped_events)}")
    print(
        f"Articles regroupés     : "
        f"{sum(len(group) for group in grouped_events)}"
    )

    # --------------------------------------------------------
    # 9. Affichage des événements regroupés
    # --------------------------------------------------------

    for index, group in enumerate(grouped_events, 1):

        print(
            f"\nÉvénement {index} "
            f"({len(group)} articles)"
        )

        for article in group:
            article_id, title, summary = article

            print(f"  [{article_id}] {title}")

    print(f"\n{'=' * 60}")
    print("Analyse terminée.")
    print(f"{'=' * 60}")


# ============================================================
# POINT D'ENTRÉE
# ============================================================

if __name__ == "__main__":
    main()