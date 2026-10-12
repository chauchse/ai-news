import os
import re
from datetime import datetime, timezone

import psycopg
from project_config import config_path, load_section

CONFIG_PATH = os.getenv("SCORING_FILE") or config_path()
BRIEFING_CONFIG_PATH = os.getenv("BRIEFING_CONFIG_FILE") or config_path()

# Categories et expressions indicatives ; surchargeables dans config.yaml, section briefing.scoring.
DOMAIN_KEYWORDS = {
    "artificial_intelligence": [
        "artificial intelligence", "generative ai", "genai", "machine learning",
        "large language model", "llm", "agentic ai", "ai agent", "agents ia",
        "intelligence artificielle", "apprentissage automatique", "rag",
        "local llm", "on-premise ai", "on-premises ai", "ai governance",
        "gouvernance de l'ia", "predictive analytics", "analyse prédictive",
    ],
    "cybersecurity": [
        "cybersecurity", "cybersécurité", "ransomware", "vulnerability",
        "vulnérabilité", "exploit", "zero-day", "zero day", "phishing",
        "data breach", "fuite de données", "security operations", "soc",
        "siem", "edr", "xdr", "threat intelligence", "cyberattack",
        "cyberattaque", "identity security", "iam", "cve-",
    ],
    "risk_management": [
        "risk management", "gestion des risques", "operational risk",
        "risque opérationnel", "business continuity", "continuité d'activité",
        "disaster recovery", "fraud detection", "détection de fraude",
        "anti-fraud", "antifraude", "fraud prevention", "third-party risk",
        "third party risk", "gestion des tiers", "internal control",
        "contrôle interne", "aml", "lutte anti-blanchiment",
    ],
    "digital_transformation": [
        "digital transformation", "transformation numérique", "digitalisation",
        "digitization", "process automation", "automatisation des processus",
        "workflow automation", "dématérialisation", "integration platform",
        "data governance", "gouvernance des données", "business process",
        "process mining", "robotic process automation", "rpa",
    ],
    "regulation": [
        "gdpr", "rgpd", "nis2", "nis 2", "ai act", "règlement ia",
        "dora", "compliance", "conformité", "regulatory",
        "réglementation", "cssf", "cnpd", "luxembourg",
        "anti-money laundering", "lbc/ft", "aml/cft",
    ],
    "gaming_industry": [
        "casino", "slot machine", "machine à sous", "gaming floor",
        "table games", "jeux de table", "gaming technology",
        "casino management system", "player tracking", "gaming regulator",
        "gaming industry", "industrie du jeu", "surveillance casino",
    ],
}

BUSINESS_KEYWORDS = {
    "ai_adoption": ["ai deployment", "ai adoption", "adoption de l'ia", "local llm", "on-premise ai", "on-premises ai", "ai agent", "agents ia", "rag"],
    "fraud_detection": ["fraud detection", "détection de fraude", "fraud prevention", "anti-fraud", "antifraude", "transaction monitoring"],
    "operational_risk": ["operational risk", "risque opérationnel", "risk assessment", "évaluation des risques", "internal control", "contrôle interne"],
    "business_continuity": ["business continuity", "continuité d'activité", "disaster recovery", "résilience opérationnelle", "backup", "sauvegarde"],
    "process_automation": ["process automation", "automatisation des processus", "workflow automation", "rpa", "process mining"],
    "data_analytics": ["data analytics", "analyse de données", "predictive analytics", "analyse prédictive", "business intelligence", "data platform"],
    "infrastructure_security": ["infrastructure security", "sécurité des infrastructures", "zero trust", "identity security", "iam", "siem", "edr", "xdr"],
}

ONLINE_ONLY_TERMS = [
    "online gambling", "online casino", "i-gaming", "igaming",
    "jeux en ligne", "casino en ligne", "sports betting", "paris sportifs",
]


def contains_term(text, term):
    """Correspondance insensible à la casse, avec limites de mots."""
    term = str(term).strip().lower()
    if not term:
        return False
    return re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text) is not None


def matched_terms(text, terms):
    return sum(1 for term in terms if contains_term(text, term))


def score_article(title, content, published_at, topics):
    """Score historique inchangé, sur 100."""
    title = (title or "").lower()
    content = (content or "").lower()
    text = f"{title} {content}"

    topic_score = 0
    for topic in topics:
        keywords = topic.get("keywords", [])
        matches = sum(1 for keyword in keywords if str(keyword).lower() in text)
        if matches:
            candidate = min(topic["weight"], matches * 15)
            topic_score = max(topic_score, candidate)
    topic_score = min(topic_score, 60)

    freshness_score = 0
    if published_at:
        now = datetime.now(timezone.utc)
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)
        age_hours = max(0, (now - published_at).total_seconds() / 3600)
        if age_hours <= 24:
            freshness_score = 25
        elif age_hours <= 48:
            freshness_score = 20
        elif age_hours <= 72:
            freshness_score = 15
        elif age_hours <= 168:
            freshness_score = 5

    length = len(content)
    if length >= 2000:
        quality_score = 15
    elif length >= 1000:
        quality_score = 10
    elif length >= 500:
        quality_score = 5
    else:
        quality_score = 0

    return min(100, topic_score + freshness_score + quality_score)


def business_bonus(title, content, scoring_config):
    """Bonus de pertinence métier de 0 à 20, sans recours à un LLM."""
    if not scoring_config:
        return 0, []

    text = f"{title or ''} {content or ''}".lower()
    title_text = (title or "").lower()
    domain_cfg = scoring_config.get("domains", {})
    business_cfg = scoring_config.get("business_context", {})
    priorities = business_cfg.get("priorities", [])

    domain_hits = []
    for domain, cfg in domain_cfg.items():
        terms = cfg.get("keywords", DOMAIN_KEYWORDS.get(domain, []))
        if matched_terms(text, terms):
            domain_hits.append((domain, max(0, float(cfg.get("weight", 0)))))

    if not domain_hits:
        return 0, []

    # Domaines cumulés avec rendements décroissants : jusqu'à 12 points.
    weights = sorted((weight for _, weight in domain_hits), reverse=True)
    domain_points = min(12, round(weights[0] / 3 + sum(weights[1:]) / 10))

    # Priorités explicites : jusqu'à 6 points.
    priority_hits = [
        priority for priority in priorities
        if matched_terms(text, BUSINESS_KEYWORDS.get(priority, []))
    ]
    priority_points = min(6, len(priority_hits) * 2)

    # Signal métier dans le titre : jusqu'à 2 points.
    title_points = 2 if any(
        matched_terms(title_text, DOMAIN_KEYWORDS.get(domain, []))
        for domain, _ in domain_hits
    ) else 0

    bonus = min(20, domain_points + priority_points + title_points)

    # Les jeux en ligne ne sont pas prioritaires, sauf si l'article
    # présente aussi un intérêt transversal IA/cyber/risques.
    if scoring_config.get("online_gaming_priority") == "low":
        online_only = matched_terms(text, ONLINE_ONLY_TERMS) > 0
        cross_cutting = any(
            domain in {"artificial_intelligence", "cybersecurity", "risk_management", "digital_transformation"}
            for domain, _ in domain_hits
        )
        if online_only and not cross_cutting:
            bonus = max(0, bonus - 5)

    return int(bonus), [domain for domain, _ in domain_hits]


def main():
    config = load_section('scoring', CONFIG_PATH)
    topics = config.get("topics", [])
    briefing_config = load_section('briefing', BRIEFING_CONFIG_PATH)
    scoring_config = briefing_config.get("scoring", {})

    # Force un recalcul lors de l'introduction du scoring métier.
    # À repasser à false après le premier passage.
    rescore_all = bool(scoring_config.get("rescore_all", False))

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, title, normalized_content, published_at
                FROM articles
                WHERE %s OR relevance_score IS NULL
                   OR scored_at < NOW() - INTERVAL '12 hours'
            """, (rescore_all,))
            articles = cur.fetchall()
            changed = 0
            examples = []
            for article_id, title, content, published_at in articles:
                base = score_article(title, content, published_at, topics)
                bonus, domains = business_bonus(title, content, scoring_config)
                score = min(100, base + bonus)
                if bonus:
                    changed += 1
                if len(examples) < 10 and bonus:
                    examples.append((article_id, base, bonus, score, domains))
                cur.execute("""
                    UPDATE articles
                    SET relevance_score = %s, scored_at = NOW()
                    WHERE id = %s
                """, (score, article_id))
        conn.commit()

    print(f"Articles évalués : {len(articles)}")
    print(f"Articles avec bonus métier : {changed}")
    print("Exemples (id, score de base, bonus, score final, domaines) :")
    for example in examples:
        print(example)


if __name__ == "__main__":
    main()
