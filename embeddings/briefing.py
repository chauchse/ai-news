import os
import json
import urllib.request
import psycopg
from langdetect import detect, DetectorFactory, LangDetectException



DATABASE_URL = os.environ["DATABASE_URL"]
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
EXCLUDED_TITLE_PATTERNS = [
    r"^veille cyber\b",
    r"^revue de presse\b",
    r"^newsletter\b",
    r"^weekly roundup\b",
    r"^weekly digest\b",
]

DetectorFactory.seed = 0


def is_french(text):
    try:
        return detect(text) == "fr"
    except LangDetectException:
        return False

def is_generic_article(title):
    import re

    return any(
        re.search(pattern, title or "", flags=re.IGNORECASE)
        for pattern in EXCLUDED_TITLE_PATTERNS
    )

def summarize(title, content):
    system_prompt = """
Tu es un analyste francophone spécialisé en cybersécurité,
intelligence artificielle et infrastructure informatique.

Tu rédiges exclusivement en français.

RÈGLES OBLIGATOIRES :
1. Toutes les phrases doivent être rédigées en français.
2. Traduis les informations provenant de textes anglais.
3. Conserve les noms propres, produits, logiciels et identifiants CVE.
4. Utilise le vocabulaire technique français approprié.
5. Rédige entre 2 et 3 phrases, avec un maximum de 80 mots.
6. Ne présente que les faits explicitement présents dans la source.
7. N'invente aucune information et ne déduis aucune conséquence.
8. Ne commence pas par « Synthèse » ou « Voici un résumé ».
9. Ne recopie pas le texte anglais.

Si le contenu ne fournit aucune information factuelle exploitable,
réponds uniquement : CONTENU_INSUFFISANT.
"""

    user_prompt = f"""
Rédige une synthèse en français de l'article suivant.

TITRE :
{title}

CONTENU SOURCE :
{content[:2500]}
"""

    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": user_prompt,
            },
        ],
        "stream": False,
        "options": {
            "temperature": 0,
            "num_predict": 180,
            "num_ctx": 3072,
        },
    }

    request = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=240) as response:
        result = json.load(response)

    return result["message"]["content"].strip()


def translate_to_french(text):
    system_prompt = (
        "Tu es un traducteur technique professionnel. "
        "Traduis intégralement le texte en français naturel. "
        "Conserve les noms de produits, les CVE et les termes techniques. "
        "N'ajoute, ne supprime et ne modifie aucune information. "
        "Réponds uniquement avec la traduction."
    )

    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
        "stream": False,
        "options": {
            "temperature": 0,
            "num_predict": 250,
            "num_ctx": 2048,
        },
    }

    request = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=240) as response:
        result = json.load(response)

    return result["message"]["content"].strip()

def main():
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    id,
                    title,
                    normalized_summary,
                    relevance_score
                FROM unique_articles
                WHERE published_at BETWEEN
                    NOW() - INTERVAL '48 hours' AND NOW()
                  AND relevance_score >= 40
                ORDER BY relevance_score DESC, published_at DESC
                LIMIT 3
            """)

            articles = cur.fetchall()
            articles = [
                article
                for article in articles
                if not is_generic_article(article[1])
            ][:3]

    print(f"Articles sélectionnés : {len(articles)}")

    for index, (article_id, title, summary, score) in enumerate(
        articles, 1
    ):
        print(f"\n{'=' * 60}")
        print(f"ARTICLE {index} — Score : {score:.1f}")
        print(f"{'=' * 60}")
        print(f"Titre : {title}")

        if not summary or not summary.strip():
            print("Résumé source absent : génération ignorée.")
            continue

        try:
            generated = summarize(title, summary)

        if generated == "CONTENU_INSUFFISANT":
            print("Article ignoré : contenu insuffisant.")
            continue

        if not is_french(generated):
            print("Langue non française détectée : traduction corrective...")
            generated = translate_to_french(generated)

        if not is_french(generated):
            print("Échec de la validation linguistique : article ignoré.")
            continue

        print(f"\nSynthèse IA :\n{generated}")
        except Exception as exc:
            print(f"Erreur de génération : {exc}")


if __name__ == "__main__":
    main()
