"""Briefing RSS local : déduplication, événements, Qwen, traduction MarianMT."""
import gc
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
from embeddings.worker import extract_cves, build_groups, MODEL_NAME, THRESHOLD

DATABASE_URL = os.environ["DATABASE_URL"]
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434").rstrip("/")
CONFIG_PATH = os.getenv("BRIEFING_CONFIG", "/config/briefing.yaml")
GLOSSARY_PATH = os.getenv("GLOSSARY_CONFIG", "/config/glossary.yaml")
TRANSLATOR = "Helsinki-NLP/opus-mt-en-fr"
EXCLUDED = re.compile(r"^(veille cyber\b|revue de presse\b|newsletter\b|weekly roundup\b|weekly digest\b)", re.I)


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
    """Découvre le nom éventuel de la colonne URL dans la vue."""
    b = cfg["briefing"]
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'unique_articles'")
            columns = {row[0] for row in cur.fetchall()}
            url_column = next((c for c in ("url", "link", "article_url", "original_url", "canonical_url", "source_url") if c in columns), None)
            url_expr = sql.Identifier(url_column) if url_column else sql.SQL("NULL::text")
            query = sql.SQL("""
                SELECT id, title, normalized_summary, relevance_score,
                       published_at, {url} AS source_url
                FROM unique_articles
                WHERE published_at BETWEEN NOW() - (%s * INTERVAL '1 hour') AND NOW()
                  AND relevance_score >= %s
                ORDER BY relevance_score DESC NULLS LAST, published_at DESC
                LIMIT %s
            """).format(url=url_expr)
            # Plafond d'entrée pour maîtriser le coût quadratique de comparaison.
            cur.execute(query, (int(b["lookback_hours"]), float(b["min_relevance_score"]), 250))
            rows = cur.fetchall()
    articles = []
    for id_, title, summary, score, published, url in rows:
        if not title or EXCLUDED.search(title):
            continue
        if not (summary and summary.strip()):
            continue
        articles.append({
            "id": id_, "title": title.strip(), "summary": summary.strip(),
            "score": float(score), "published_at": published,
            "url": str(url) if url else None,
        })
    print(f"Articles exploitables : {len(articles)} (colonne URL : {url_column or 'absente'})", flush=True)
    return articles


def group_articles(articles):
    if not articles:
        return []
    if len(articles) == 1:
        return [articles]
    print("Chargement MiniLM et regroupement...", flush=True)
    model = SentenceTransformer(MODEL_NAME, device="cpu")
    vectors = model.encode(
        [a["summary"] or a["title"] for a in articles],
        normalize_embeddings=True, batch_size=16, show_progress_bar=False,
    )
    similarities = cos_sim(vectors, vectors)
    pairs = []
    for i in range(len(articles)):
        for j in range(i + 1, len(articles)):
            similarity = similarities[i][j].item()
            if similarity < THRESHOLD:
                continue
            a = extract_cves(articles[i]["title"] + " " + articles[i]["summary"])
            b = extract_cves(articles[j]["title"] + " " + articles[j]["summary"])
            if a and b and not (a & b):
                continue
            pairs.append((similarity, i, j))
    # worker.build_groups attend des éléments indexables par [0] pour le tri.
    indexed = [(i, a) for i, a in enumerate(articles)]
    grouped = build_groups(indexed, pairs)
    result = [[entry[1] for entry in group] for group in grouped]
    del model, vectors, similarities
    gc.collect()
    return result


def rank_groups(groups):
    # Le score du meilleur article prime, avec un bonus limité pour les sources multiples.
    def key(group):
        return (max(a["score"] for a in group) + min(len(group) - 1, 3) * 3,
                max(a["published_at"] for a in group))
    return sorted(groups, key=key, reverse=True)


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
        f"Source {i} - Title: {a['title']}\nSummary: {a['summary'][:900]}"
        for i, a in enumerate(best, 1)
    )
    system = (
        "You are a precise cybersecurity news editor. Write ONLY in English. "
        "The input contains reports about the same event. Produce ONE factual news item. "
        "Use only claims supported by the provided reports. Do not invent CVEs, dates, "
        "impact, exploitation status or attribution. When sources disagree, avoid the disputed claim. "
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
    heading = "Veille cybersécurité" if lang == "fr" else "Cybersecurity briefing"
    lines = [f"# {heading} — {now}", "",
             f"{'Articles analysés' if lang == 'fr' else 'Articles analyzed'} : {analyzed_count}  ",
             f"{'Événements détectés' if lang == 'fr' else 'Events detected'} : {total_events}", ""]
    for index, item in enumerate(events, 1):
        article = item["generated"]
        lines += [f"## {index}. {article['title']}", "", article["summary"], "",
                  f"**{'Score' if lang == 'fr' else 'Score'} :** {item['score']:.0f}  ",
                  f"**{'Sources' if lang == 'fr' else 'Sources'} :**"]
        for source in item["sources"]:
            title = source["title"].replace("[", "\\[").replace("]", "\\]")
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
    selected = groups[:int(cfg["briefing"]["max_events"])]
    print(f"Événements distincts : {len(groups)} ; sélectionnés : {len(selected)}", flush=True)

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
