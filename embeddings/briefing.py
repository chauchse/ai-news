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
ZDI_PATTERN = re.compile(r"\bZDI-CAN-\d+\b", re.I)
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


# Strict identity: never infer that two different advisories are the same CVE.
PRODUCT_PATTERNS = {
    "citrix_netscaler": r"\b(?:citrix\s+)?netscaler\b|\bcitrix\s+(?:adc|gateway)\b",
    "sonicwall_sma": r"\bsonicwall\s+sma\s*\d+\b|\bsma1000\b",
    "myscada": r"\bmyscada\b",
    "ahsaycbs": r"\bahsaycbs\b",
    "fortinet_fortios": r"\bfortios\b",
    "vmware_esxi": r"\besxi\b",
    "microsoft_exchange": r"\b(?:microsoft\s+)?exchange\s+server\b",
}
VERSION_PATTERN = re.compile(r"\b(?:v(?:ersion)?\s*)?(\d{1,3}\.\d{1,3}(?:\.\d{1,4}){0,2})\b", re.I)


def vulnerability_identity(article):
    text = article["title"] + " " + article.get("summary", "")
    cves, zdis = identifiers(article)
    products = {name for name, pattern in PRODUCT_PATTERNS.items()
                if re.search(pattern, text, re.I)}
    # Versions from the title only; summary versions may refer to other products.
    versions = set(VERSION_PATTERN.findall(article["title"]))
    return cves, zdis, products, versions


def same_vulnerability(a, b):
    ac, az, ap, av = vulnerability_identity(a)
    bc, bz, bp, bv = vulnerability_identity(b)
    if az or bz:
        return bool(az and bz and az == bz)
    if ac or bc:
        return bool(ac and bc and ac == bc)
    # Without an identifier, never merge two distinct advisory titles on
    # product similarity alone: product/version are not unique advisory IDs.
    return bool(ap and bp and ap == bp and av and bv and av == bv
                and normalized_title(a["title"]) == normalized_title(b["title"]))


def same_event(a, b, similarity):
    """Strict grouping: CVE/ZDI identity or same URL/title, no semantic-only vuln merge."""
    acve, azdi = identifiers(a)
    bcve, bzdi = identifiers(b)
    if acve or azdi or bcve or bzdi:
        return same_vulnerability(a, b)
    if canonical_url(a.get("url")) and canonical_url(a.get("url")) == canonical_url(b.get("url")):
        return True
    at, bt = normalized_title(a["title"]), normalized_title(b["title"])
    if at and at == bt:
        return True
    if a["vulnerability"]["candidate"] or b["vulnerability"]["candidate"]:
        return False
    common = distinctive_tokens(a["title"]) & distinctive_tokens(b["title"])
    union = distinctive_tokens(a["title"]) | distinctive_tokens(b["title"])
    overlap = len(common) / max(1, len(union))
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



def deduplicate_security_events(groups, cfg):
    """Conservative Qwen review of possible same-product duplicates."""
    budget = max(0, int(cfg["briefing"].get("business_triage", {}).get("max_dedup_reviews", 6)))
    result, reviews, merges = [], 0, 0
    for group in groups:
        joined = False
        if group_category(group) == "security":
            for existing in result:
                if reviews >= budget:
                    break
                if group_category(existing) != "security":
                    continue
                pairs = [(a, b) for a in group for b in existing]
                def compatible(a, b):
                    ac, az, ap, av = vulnerability_identity(a)
                    bc, bz, bp, bv = vulnerability_identity(b)
                    if (ac and bc and ac != bc) or (az and bz and az != bz):
                        return False
                    if av and bv and av.isdisjoint(bv):
                        return False
                    return bool(ap & bp)
                if not all(compatible(a, b) for a, b in pairs):
                    continue
                reviews += 1
                a = max(group, key=lambda x: x["score"])
                b = max(existing, key=lambda x: x["score"])
                system = ("Determine if reports concern the SAME specific vulnerability or "
                          "exploitation campaign, not just the same vendor/product. "
                          "Require shared event details beyond product name. "
                          "If unsure answer false. Output JSON keys same_event (boolean), "
                          "confidence (0..1), evidence (short factual string).")
                prompt = (f"A: {a['title']}\n{a['summary'][:650]}\n"
                          f"B: {b['title']}\n{b['summary'][:650]}")
                try:
                    v = ollama_object([{"role":"system","content":system},
                        {"role":"user","content":prompt}], cfg["llm"]["model"],
                        cfg["llm"].get("temperature", 0), predict=140)
                    same = (v.get("same_event") is True and
                            float(v.get("confidence",0)) >= .85 and
                            bool(str(v.get("evidence","")).strip()))
                except Exception as exc:
                    print(f"Arbitrage déduplication indisponible : {exc}", file=sys.stderr)
                    same = False
                if same:
                    existing.extend(group)
                    merges += 1
                    joined = True
                    print(f"Fusion contrôlée : {a['title'][:60]} <> {b['title'][:60]}", flush=True)
                    break
        if not joined:
            result.append(group)
    print(f"Déduplication V55 : {reviews} arbitrages, {merges} fusions, {len(result)} événements", flush=True)
    return result


def strategic_topic(group):
    a = max(group, key=lambda x: x["score"])
    t = (a["title"] + " " + a["summary"][:550]).lower()
    patterns = {
      "regulation": r"\b(luxembourg|cssf|cnpd|dora|nis2|nis 2|ai act|rgpd|gdpr|conformit\w*|regulat\w*|directive)\b",
      "risk": r"\b(business continuity|resilien\w*|operational risk|fraud|fraude|risk management|gestion des risques|disaster recovery)\b",
      "transformation": r"\b(automation|automatisation|workflow|data analytics|digital transformation|digitalisation|business intelligence|process mining|api management)\b",
      "ai": r"\b(ai agent|agents? ia|agentic|least privilege|artificial intelligence|intelligence artificielle|llm|prompt injection|machine learning|anthropic|openai|qwen)\b",
    }
    for name, pattern in patterns.items():
        if re.search(pattern, t):
            return name
    return "other"


def near_duplicate_topic(a, b):
    ta = distinctive_tokens(max(a["group"], key=lambda x: x["score"])["title"])
    tb = distinctive_tokens(max(b["group"], key=lambda x: x["score"])["title"])
    return len(ta & tb) >= 3 and len(ta & tb) / max(1, len(ta | tb)) >= .45

def rank_groups(groups):
    def key(group):
        return (max(a["score"] for a in group) + min(len(group) - 1, 3) * 3,
                max(a["published_at"] for a in group))
    return sorted(groups, key=key, reverse=True)


def group_category(group):
    if any(a["vulnerability"]["candidate"] for a in group):
        return "security"
    return max(group, key=lambda a: (a["score"], a["semantic_similarity"]))["category"]


def candidate_pool(groups, cfg):
    """Round-robin strategic themes before security/global fill."""
    cap = max(1, int(cfg["briefing"].get("business_triage",{}).get("max_candidates",24)))
    chosen, seen = [], set()
    def add(i):
        if i not in seen:
            chosen.append(groups[i]); seen.add(i)
    for i, g in enumerate(groups):
        if any(a["vulnerability"]["priority"] == "P1_review" for a in g):
            add(i)
    strategic = [i for i,g in enumerate(groups) if group_category(g) != "security"]
    security = [i for i,g in enumerate(groups) if group_category(g) == "security"]
    buckets = {t:[i for i in strategic if strategic_topic(groups[i]) == t]
               for t in ("regulation","risk","transformation","ai","other")}
    target = max(8,cap//2)
    strategic_selected = 0
    while strategic_selected < target:
        progress = False
        for bucket in buckets.values():
            if bucket:
                i = bucket.pop(0)
                if i not in seen:
                    add(i); strategic_selected += 1
                progress = True
                if strategic_selected >= target: break
        if not progress: break
    for i in security[:max(4,cap//3)]: add(i)
    for i in range(len(groups)):
        if len(chosen) >= cap: break
        add(i)
    return chosen


def applicability(group, cfg):
    """A watchlist match is not proof of deployment or exposure."""
    found = set()
    for article in group:
        found |= vulnerability_identity(article)[2]
    return "unknown", sorted(found)


def watch_priority(group):
    """Secondary tie-breaker, not a business relevance override."""
    return max((2 if a['watch'].get('whitelist_priority') == 'high' else
                1 if a['watch'].get('whitelist_priority') == 'normal' else 0
                for a in group), default=0)


def business_assessments(groups, cfg):
    pool = candidate_pool(groups, cfg)
    print(f"Évaluation métier Qwen : {len(pool)} candidats", flush=True)
    assessed = []
    for index, group in enumerate(pool, 1):
        a = max(group, key=lambda x: x["score"])
        vuln = a["vulnerability"]
        is_security = any(x["vulnerability"]["candidate"] for x in group)
        app_status, products = applicability(group, cfg)
        system = (
            "You assess RSS news for a CTO/CISO of a PHYSICAL casino in Luxembourg, "
            "not an online gambling or video-game company. Score relevance to business operations, "
            "not vendor name recognition. Priorities: infrastructure security, business continuity, "
            "fraud detection, operational risk, Luxembourg/EU compliance, practical AI adoption, "
            "process automation, data analytics and digital transformation. "
            "Do not claim the casino uses any named product, or that any regulation applies, "
            "unless explicitly established in the input. Do not infer an asset inventory. "
            "Rate business_relevance and news_importance as integers 0..5. "
            "Assign one category: security, artificial_intelligence, regulation_and_risk, "
            "gaming_and_transformation. Return ONLY JSON object keys "
            "business_relevance, news_importance, category, reason. "
            "The reason must be factual and no more than 20 words."
        )
        user = (f"Title: {a['title']}\nSummary: {a['summary'][:900]}\n"
                f"Preliminary vulnerability triage: {vuln['priority']} (unverified). "
                f"Exploitation mentioned: {vuln['exploitation_mentioned']}. "
                f"CVSS mentioned: {vuln['cvss']}. "
                f"Product applicability: {app_status} (determined externally; do not upgrade). "
                f"Products recognized: {', '.join(products) or 'none'}. "
                f"Whitelist: {', '.join(a['watch']['whitelist']) or 'none'}.")
        try:
            result = ollama_object(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                cfg["llm"]["model"], cfg["llm"].get("temperature", 0), predict=160)
            relevance = int(result["business_relevance"])
            importance = int(result["news_importance"])
            category = str(result["category"])
            if not (0 <= relevance <= 5 and 0 <= importance <= 5 and
                    category in ("security", "artificial_intelligence",
                                 "regulation_and_risk", "gaming_and_transformation")):
                raise ValueError("Qwen score/category out of range")
            reason = str(result.get("reason", ""))[:120]
        except Exception as exc:
            print(f"ATTENTION évaluation {index} impossible : {exc}", file=sys.stderr)
            relevance, importance, category, reason = 0, 0, group_category(group), "evaluation unavailable"
        # Deterministic security classification overrides Qwen.
        if is_security:
            category = "security"
        p1 = any(x["vulnerability"]["priority"] == "P1_review" for x in group)
        if is_security:
            levels = {x["vulnerability"]["priority"] for x in group}
            importance = max(importance, 5 if "P1_review" in levels else
                             4 if "P2_review" in levels else 2)
        assessed.append({"group": group, "category": category, "business": relevance,
                         "importance": importance, "priority": p1,
                         "ranking": relevance * 12 + importance * 8,
                         "watch_priority": watch_priority(group),
                         "reason": reason, "applicability": app_status, "products": products,
                         "topic": strategic_topic(group) if not is_security else "security"})
        print(f"  {index:02d}/{len(pool)} [{category}] métier={relevance}/5 "
              f"importance={importance}/5 watch={watch_priority(group)} applicabilité={app_status} {a['title'][:75]}", flush=True)
    return assessed


def select_groups(groups, cfg):
    """Separate security alerts from strategic news; do not fill strategic slots with CVEs."""
    settings = cfg["briefing"].get("business_triage", {})
    assessed = business_assessments(groups, cfg)
    minimum = int(settings.get("min_business_relevance", 2))
    max_security = int(settings.get("max_security_alerts", 4))
    max_strategic = int(settings.get("max_strategic_events", 6))
    max_p1_extra = int(settings.get("max_p1_overflow", 2))
    ranked = sorted(assessed, key=lambda x: (x["ranking"], x["watch_priority"], x["importance"]), reverse=True)
    security = [x for x in ranked if x["category"] == "security"]
    strategic = [x for x in ranked if x["category"] != "security" and x["business"] >= minimum]
    p1 = [x for x in security if x["priority"]]
    p1.sort(key=lambda x: (x["ranking"], x["watch_priority"]), reverse=True)
    selected_security = p1[:max_security + max_p1_extra]
    for item in security:
        if len(selected_security) >= max(max_security, len(selected_security)):
            break
        if item not in selected_security and item["business"] >= minimum:
            selected_security.append(item)
    selected_strategic = []
    targets = settings.get("strategic_topic_targets", {"ai":2,"regulation":1,"risk":1,"transformation":1,"other":1})
    for topic in ("regulation","risk","transformation","ai","other"):
        target = max(0, int(targets.get(topic,0)))
        for item in strategic:
            if len(selected_strategic) >= max_strategic: break
            if sum(x["topic"] == topic for x in selected_strategic) >= target: break
            if item["topic"] != topic or item in selected_strategic: continue
            if any(near_duplicate_topic(item,x) for x in selected_strategic): continue
            selected_strategic.append(item)
    # No forced fill: unrepresented categories stay empty.
    selected = selected_security + selected_strategic
    print("Répartition :", dict(Counter(x["category"] for x in selected)), flush=True)
    print(f"Rubriques : sécurité={len(selected_security)} ; stratégique={len(selected_strategic)}")
    print("Sélection :")
    for item in selected:
        a = max(item["group"], key=lambda x: x["score"])
        print(f"  [{item['category']}] métier={item['business']}/5 "
              f"importance={item['importance']}/5 thème={item['topic']} applicabilité={item['applicability']} "
              f"P1={item['priority']} {a['title'][:85]} — {item['reason']}", flush=True)
    return selected


def ollama_object(messages, model, temperature, predict=320):
    payload = {"model": model, "messages": messages, "stream": False,
               "format": "json", "options": {"temperature": temperature,
               "num_ctx": 3072, "num_predict": predict}, "keep_alive": "5m"}
    request = urllib.request.Request(
        OLLAMA_URL + "/api/chat", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=360) as response:
        answer = json.load(response)
    result = json.loads(answer["message"]["content"])
    if not isinstance(result, dict):
        raise ValueError("JSON Qwen must be an object")
    return result


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

    previous_section = None
    for index, item in enumerate(events, 1):
        assessment = item["assessment"]
        section = "security" if assessment["category"] == "security" else "strategic"
        if section != previous_section:
            lines += ["## Alertes de sécurité prioritaires" if section == "security" else
                      "## Veille stratégique et métier", ""]
            previous_section = section
        article = item["generated"]

        lines += [f"### {index}. {article['title']}", "", article["summary"], "",

                  f"**Score historique :** {item['score']:.0f}  ",
                  f"**Pertinence métier :** {assessment['business']}/5 ; **Importance :** {assessment['importance']}/5  ",
                  f"**Applicabilité :** {assessment['applicability']}  ",

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
    groups = rank_groups(deduplicate_security_events(groups, cfg))

    selected = select_groups(groups, cfg)

    print(f"Événements distincts : {len(groups)} ; sélectionnés : {len(selected)}", flush=True)
    if os.getenv("BRIEFING_DRY_RUN", "0") == "1":
        print("DRY RUN : évaluation Qwen effectuée ; aucune synthèse, traduction ou publication.", flush=True)
        return



    # Les appels Qwen sont terminés AVANT le chargement de MarianMT.

    generated = []

    for index, assessment in enumerate(selected, 1):

        group = assessment["group"]
        print(f"Synthèse {index}/{len(selected)} ({len(group)} source(s))...", flush=True)

        try:

            english = summarize_group(group, cfg)

            generated.append({"english": english, "sources": group,

                              "score": max(a["score"] for a in group), "assessment": assessment})

        except Exception as exc:

            print(f"ERREUR synthèse événement {index}: {exc}", file=sys.stderr)



    translator = Translator() if language == "fr" and generated else None

    completed = []

    for index, item in enumerate(generated, 1):

        try:

            translated = ({key: translator.translate(value) for key, value in item["english"].items()}

                          if translator else item["english"])

            completed.append({"generated": translated, "sources": item["sources"], "score": item["score"], "assessment": item["assessment"]})

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
