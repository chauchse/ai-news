"""Briefing RSS local : déduplication, événements, Qwen, traduction MarianMT."""

import gc
import unicodedata
from collections import Counter
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import json

import os

import re

import sys

import time

import urllib.request

from datetime import datetime, timezone

from pathlib import Path



import psycopg

from psycopg import sql

import torch

import yaml

from sentence_transformers import SentenceTransformer

from sentence_transformers.util import cos_sim



from embeddings.glossary import CyberGlossary
from embeddings.watchlist import load_rules, evaluate
from embeddings.vulnerability_detector import detect

from embeddings.worker import extract_cves, MODEL_NAME, THRESHOLD



DATABASE_URL = os.environ["DATABASE_URL"]

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434").rstrip("/")

CONFIG_PATH = os.getenv("BRIEFING_CONFIG", "/config/briefing.yaml")

GLOSSARY_PATH = os.getenv("GLOSSARY_CONFIG", "/config/glossary.yaml")

TRANSLATOR = "Helsinki-NLP/opus-mt-en-fr"

EXCLUDED = re.compile(
    r"^(veille cyber\b|revue de presse\b|newsletter\b|weekly roundup\b|weekly digest\b|"
    r"weekly recap\b|security roundup\b|bulletin hebdomadaire\b)", re.I)
ZDI_PATTERN = re.compile(r"\bZDI-CAN-\\d+\b", re.I)
STOPWORDS = {"the", "and", "for", "with", "from", "into", "that", "this", "are", "new",
             "des", "les", "une", "dans", "pour", "avec", "sur", "aux", "de", "du", "et",
             "flaw", "flaws", "vulnerability", "vulnerabilities", "critical", "security",
             "multiple", "multiples", "vulnerabilites", "vulnerabilite", "zero", "day"}
DOMAIN_MAP = {
    "cybersecurity": "security", "artificial_intelligence": "artificial_intelligence",
    "risk_management": "regulation_and_risk", "regulation": "regulation_and_risk",
    "digital_transformation": "gaming_and_transformation",
    "gaming_industry": "gaming_and_transformation", "infrastructure": "security",
    "gaming": "gaming_and_transformation", "risk": "regulation_and_risk",
    "ai": "artificial_intelligence"
}






def config_load():

    with open(CONFIG_PATH, encoding="utf-8") as fh:

        cfg = yaml.safe_load(fh) or {}

    briefing = cfg["briefing"]

    language = str(briefing["language"]).lower()

    if language not in ("fr", "en"):

        raise ValueError("briefing.language doit valoir 'fr' ou 'en'")

    if int(briefing["max_events"]) < 1:

        raise ValueError("max_events doit être positif")

    return cfg





def fetch_articles(cfg):
    """Read recent items without prefiltering on the old keyword score."""
    b = cfg['briefing']
    rules = load_rules()
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'unique_articles'")
            columns = {row[0] for row in cur.fetchall()}
            url_column = next((c for c in ('url', 'link', 'article_url', 'original_url', 'canonical_url', 'source_url') if c in columns), None)
            url_expr = sql.Identifier(url_column) if url_column else sql.SQL('NULL::text')
            query = sql.SQL("""
                SELECT id, title, normalized_summary, relevance_score, published_at,
                       {url} AS source_url
                FROM unique_articles
                WHERE published_at BETWEEN NOW() - (%s * INTERVAL '1 hour') AND NOW()
                ORDER BY published_at DESC, id DESC
                LIMIT %s
            """).format(url=url_expr)
            # Configurable cap; inspect truncation if your RSS volume grows.
            cur.execute(query, (int(b['lookback_hours']), int(os.getenv('BRIEFING_FETCH_LIMIT', '2000'))))
            rows = cur.fetchall()
    articles = []
    for id_, title, summary, score, published, url in rows:
        if not title:
            continue
        summary = (summary or '').strip()
        watch = evaluate(title, summary, config=rules)
        vuln = detect(title, summary)
        if watch['absolute_blacklist']:
            continue
        if watch['blacklist'] and not (watch['whitelist'] or vuln['candidate']):
            continue
        if EXCLUDED.search(title):
            continue
        if not summary and not (watch['whitelist'] or vuln['candidate']):
            continue
        old_score = float(score or 0)
        if old_score < float(b['min_relevance_score']) and not (watch['whitelist'] or vuln['candidate']):
            continue
        # Ranking remains provisional until a structured Qwen assessment is added.
        boost = {'P1_review': 95, 'P2_review': 85, 'P3_review': 65, 'none': 0}[vuln['priority']]
        if watch['whitelist']:
            boost = max(boost, 75)
        articles.append({'id': id_, 'title': title.strip(), 'summary': summary,
                         'score': max(old_score, boost), 'original_score': old_score,
                         'published_at': published, 'url': str(url) if url else None,
                         'watch': watch, 'vulnerability': vuln})
    print(f"Articles exploitables : {len(articles)} (colonne URL : {url_column or 'absente'})", flush=True)
    print(f"Candidats vulnérabilités : {sum(a['vulnerability']['candidate'] for a in articles)} ; whitelist : {sum(bool(a['watch']['whitelist']) for a in articles)}", flush=True)
    return articles



def canonical_url(url):
    if not url:
        return None
    parsed = urlsplit(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    query = [(k, v) for k, v in parse_qsl(parsed.query)
             if not (k.lower().startswith("utm_") or k.lower() in ("fbclid", "gclid"))]
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower().removeprefix("www."),
                       parsed.path.rstrip("/") or "/", urlencode(query), ""))


def normalized_title(title):
    text = unicodedata.normalize("NFKD", title or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def identifiers(article):
    text = article["title"] + " " + article.get("summary", "")
    return (set(extract_cves(text)), set(x.upper() for x in ZDI_PATTERN.findall(text)))


def distinctive_tokens(title):
    return {t for t in normalized_title(title).split()
            if len(t) >= 4 and t not in STOPWORDS and not t.isdigit()}


def same_event(a, b, similarity):
    """Conservative grouping: semantic proximity alone is never sufficient."""
    acve, azdi = identifiers(a)
    bcve, bzdi = identifiers(b)
    if azdi or bzdi:
        return bool(azdi and bzdi and azdi == bzdi)
    if acve or bcve:
        return bool(acve and bcve and acve == bcve)
    if canonical_url(a.get("url")) and canonical_url(a.get("url")) == canonical_url(b.get("url")):
        return True
    at, bt = normalized_title(a["title"]), normalized_title(b["title"])
    if at and at == bt:
        return True
    common = distinctive_tokens(a["title"]) & distinctive_tokens(b["title"])
    union = distinctive_tokens(a["title"]) | distinctive_tokens(b["title"])
    overlap = len(common) / max(1, len(union))
    # Strong semantic match AND strong overlap of distinctive title words.
    return similarity >= max(0.93, float(THRESHOLD)) and len(common) >= 3 and overlap >= 0.65


def load_domain_profiles():
    path = Path(os.getenv("SEMANTIC_SCORING_FILE", "/config/semantic_scoring.yaml"))
    if not path.is_file():
        raise FileNotFoundError(f"Profils MiniLM introuvables : {path}. Monte semantic_scoring.yaml dans /config.")
    with path.open(encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    domains = cfg.get("domains", [])
    descriptions, names, slices = [], [], []
    for domain in domains:
        if not domain.get("name") or not domain.get("descriptions"):
            continue
        begin = len(descriptions)
        descriptions.extend(domain["descriptions"])
        names.append(str(domain["name"]).lower())
        slices.append((begin, len(descriptions)))
    if not descriptions:
        raise ValueError("Aucun profil valide dans semantic_scoring.yaml")
    return descriptions, names, slices


def group_articles(articles):
    if not articles:
        return []
    print("Chargement MiniLM : classification et regroupement...", flush=True)
    descriptions, names, slices = load_domain_profiles()
    model = SentenceTransformer(MODEL_NAME, device="cpu")
    texts = [(a["title"] + ". " + a["summary"][:650]).strip() for a in articles]
    vectors = model.encode(descriptions + texts, normalize_embeddings=True,
                           batch_size=16, show_progress_bar=False)
    count = len(descriptions)
    article_vectors = vectors[count:]
    domain_scores = article_vectors @ vectors[:count].T
    for article, sims in zip(articles, domain_scores):
        scored = [(float(max(sims[start:end])), name)
                  for name, (start, end) in zip(names, slices)]
        article["semantic_domain"] = max(scored)[1]
        article["semantic_similarity"] = max(scored)[0]
        article["category"] = DOMAIN_MAP.get(article["semantic_domain"], "regulation_and_risk")
        # Security event classification takes precedence over thematic similarity.
        if article["vulnerability"]["candidate"]:
            article["category"] = "security"

    similarities = article_vectors @ article_vectors.T
    # Complete-link grouping: each new member must match EVERY existing member,
    # preventing transitive chains of loosely related vulnerability notices.
    groups = []
    for i, article in enumerate(articles):
        placed = False
        for group in groups:
            if all(same_event(article, articles[j], float(similarities[i, j])) for j in group):
                group.append(i)
                placed = True
                break
        if not placed:
            groups.append([i])
    del model, vectors, article_vectors, similarities
    gc.collect()
    return [[articles[i] for i in group] for group in groups]


def rank_groups(groups):
    def key(group):
        return (max(a["score"] for a in group) + min(len(group) - 1, 3) * 3,
                max(a["published_at"] for a in group))
    return sorted(groups, key=key, reverse=True)


def group_category(group):
    if any(a["vulnerability"]["candidate"] for a in group):
        return "security"
    return max(group, key=lambda a: (a["score"], a["semantic_similarity"]))["category"]


def select_groups(groups, cfg):
    """Flexible quotas; P1 events first, then categories, then fill unused slots."""
    limit = int(cfg["briefing"]["max_events"])
    selection = cfg["briefing"].get("selection", {})
    if not selection:
        return groups[:limit]
    selected, chosen = [], set()
    def take(index):
        if index not in chosen and len(selected) < limit:
            selected.append(groups[index])
            chosen.add(index)

    if selection.get("critical_security_override", True):
        for i, group in enumerate(groups):
            if any(a["vulnerability"]["priority"] == "P1_review" for a in group):
                take(i)
    for category in ("security", "artificial_intelligence",
                     "regulation_and_risk", "gaming_and_transformation"):
        target = int(selection.get(category, {}).get("target", 0))
        already = sum(group_category(g) == category for g in selected)
        for i, group in enumerate(groups):
            if already >= target or len(selected) >= limit:
                break
            if i not in chosen and group_category(group) == category:
                take(i)
                already += 1
    if selection.get("flexible_quotas", True):
        for i in range(len(groups)):
            take(i)
    print("Répartition :", dict(Counter(group_category(g) for g in selected)), flush=True)
    print("Sélection :")
    for group in selected:
        a = max(group, key=lambda item: item["score"])
        print(f"  [{group_category(group)}] {a['score']:.0f} {a['vulnerability']['priority']} "
              f"{a['title'][:95]}", flush=True)
    return selected


def ollama_json(messages, model, temperature):

    payload = {

        "model": model, "messages": messages, "stream": False, "format": "json",

        "options": {"temperature": temperature, "num_ctx": 3072, "num_predict": 320},

        "keep_alive": 0,  # libère la RAM avant de charger MarianMT

    }

    request = urllib.request.Request(

        OLLAMA_URL + "/api/chat", data=json.dumps(payload).encode(),

        headers={"Content-Type": "application/json"}, method="POST",

    )

    with urllib.request.urlopen(request, timeout=360) as response:

        answer = json.load(response)

    result = json.loads(answer["message"]["content"])

    if not isinstance(result, dict) or not all(isinstance(result.get(k), str) and result[k].strip() for k in ("title", "summary")):

        raise ValueError("JSON Qwen incomplet")

    return {k: result[k].strip() for k in ("title", "summary")}





def summarize_group(group, cfg):

    # On fournit au plus trois articles d'un même événement pour limiter les tokens.

    best = sorted(group, key=lambda a: a["score"], reverse=True)[:3]

    entries = "\n\n".join(

        f"Source {i} - Title: {a['title']}\nSummary: {a['summary'][:900]}\n"
        f"Triage (unverified): {a['vulnerability']['priority']}; CVEs: {', '.join(a['vulnerability']['cves']) or 'none'}"

        for i, a in enumerate(best, 1)

    )

    system = (

        "You are a precise CTO/CISO news editor covering cybersecurity, AI, regulation and business risk. Write ONLY in English. "

        "The input contains reports about the same event. Produce ONE factual news item. "

        "Use only claims supported by the provided reports. Do not invent CVEs, dates, "

        "impact, exploitation status or attribution. Do not present provisional triage as confirmed. When sources disagree, avoid the disputed claim. "

        "Preserve proper names, technical identifiers and product names. "

        "Summary: 1 to 3 short sentences, at most 75 words. "

        "Title: concise and factual. Return only JSON with string fields title and summary."

    )

    return ollama_json(

        [{"role": "system", "content": system}, {"role": "user", "content": entries}],

        cfg["llm"]["model"], cfg["llm"].get("temperature", 0),

    )





class Translator:

    def __init__(self):

        from transformers import MarianMTModel, MarianTokenizer

        torch.set_num_threads(4)

        self.glossary = CyberGlossary(GLOSSARY_PATH)

        print("Chargement MarianMT...", flush=True)

        self.tokenizer = MarianTokenizer.from_pretrained(TRANSLATOR)

        self.model = MarianMTModel.from_pretrained(TRANSLATOR)

        self.model.eval()



    def translate(self, text):

        protected_text, mapping = self.glossary.protect(text)

        inputs = self.tokenizer(protected_text, return_tensors="pt", truncation=True, max_length=512)

        with torch.inference_mode():

            tokens = self.model.generate(**inputs, max_new_tokens=180, num_beams=4)

        raw = self.tokenizer.decode(tokens[0], skip_special_tokens=True)

        missing = [token for token in mapping if raw.count(token) != 1]

        if missing:

            raise ValueError(f"Marqueurs techniques perdus/dupliqués : {missing}")

        restored = self.glossary.restore(raw, mapping)

        if re.search(r"ZXQTERM\d+QXZ", restored):

            raise ValueError("Marqueur non restauré")

        return restored





def format_markdown(events, cfg, analyzed_count, total_events):

    lang = cfg["briefing"]["language"]

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    heading = "Veille technologique et risques" if lang == "fr" else "Technology and risk briefing"

    lines = [f"# {heading} — {now}", "",

             f"{'Articles analysés' if lang == 'fr' else 'Articles analyzed'} : {analyzed_count}  ",

             f"{'Événements détectés' if lang == 'fr' else 'Events detected'} : {total_events}", ""]

    for index, item in enumerate(events, 1):

        article = item["generated"]

        lines += [f"## {index}. {article['title']}", "", article["summary"], "",

                  f"**{'Score' if lang == 'fr' else 'Score'} :** {item['score']:.0f}  ",

                  f"**{'Sources' if lang == 'fr' else 'Sources'} :**"]

        for source in item["sources"]:

            title = source["title"].replace("[", "\\\\[").replace("]", "\\\\]")

            url = source["url"]

            if url and url.startswith(("https://", "http://")):

                lines.append(f"- [{title}]({url})")

            else:

                lines.append(f"- {title} (article ID {source['id']}, URL indisponible)")

        lines.append("")

    return "\n".join(lines) + "\n"





def main():

    start = time.monotonic()

    cfg = config_load()

    language = cfg["briefing"]["language"].lower()

    articles = fetch_articles(cfg)

    if not articles:

        print("Aucun article éligible.")

        return

    groups = rank_groups(group_articles(articles))

    selected = select_groups(groups, cfg)

    print(f"Événements distincts : {len(groups)} ; sélectionnés : {len(selected)}", flush=True)
    if os.getenv("BRIEFING_DRY_RUN", "0") == "1":
        print("DRY RUN : aucune requête Qwen, aucune traduction, aucun fichier écrit.", flush=True)
        return



    # Les appels Qwen sont terminés AVANT le chargement de MarianMT.

    generated = []

    for index, group in enumerate(selected, 1):

        print(f"Synthèse {index}/{len(selected)} ({len(group)} source(s))...", flush=True)

        try:

            english = summarize_group(group, cfg)

            generated.append({"english": english, "sources": group,

                              "score": max(a["score"] for a in group)})

        except Exception as exc:

            print(f"ERREUR synthèse événement {index}: {exc}", file=sys.stderr)



    translator = Translator() if language == "fr" and generated else None

    completed = []

    for index, item in enumerate(generated, 1):

        try:

            translated = ({key: translator.translate(value) for key, value in item["english"].items()}

                          if translator else item["english"])

            completed.append({"generated": translated, "sources": item["sources"], "score": item["score"]})

            print(f"Événement {index} : {translated['title']}", flush=True)

        except Exception as exc:

            # Pas de publication d'une traduction partiellement corrompue.

            print(f"ERREUR traduction événement {index} (ignoré): {exc}", file=sys.stderr)



    if not completed:

        raise RuntimeError("Aucun événement généré avec succès")

    markdown = format_markdown(completed, cfg, len(articles), len(groups))

    output = Path(os.getenv("BRIEFING_OUTPUT", "/tmp/ai-news-briefing.md"))

    output.parent.mkdir(parents=True, exist_ok=True)

    output.write_text(markdown, encoding="utf-8")

    print("\n" + markdown)

    print(f"Fichier dans le conteneur : {output}")

    print(f"Durée totale : {time.monotonic() - start:.1f}s")





if __name__ == "__main__":

    main()
