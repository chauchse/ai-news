"""AI News semantic relevance experiment v2 (READ ONLY).
Run: python -m embeddings.semantic_scorer
"""
import os
import re
import time
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import psycopg
import yaml
from sentence_transformers import SentenceTransformer

CONFIG_PATH = Path(os.getenv('SEMANTIC_SCORING_FILE', '/app/config/semantic_scoring.yaml'))
MODEL_NAME = os.getenv('EMBEDDING_MODEL', 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')


def load_config():
    with CONFIG_PATH.open(encoding='utf-8') as f:
        cfg = yaml.safe_load(f) or {}
    domains = cfg.get('domains', [])
    if not domains or any(not d.get('name') or not d.get('descriptions') for d in domains):
        raise ValueError('Configuration invalide : domains/name/descriptions manquants')
    return cfg, domains


def fetch_articles(days, limit):
    # Unique by GUID when possible; retain the most recently published copy.
    query = '''
        SELECT DISTINCT ON (guid) id, title, normalized_summary,
               normalized_content, relevance_score, published_at, guid
        FROM articles
        WHERE published_at >= %s AND published_at <= NOW() + INTERVAL '1 day'
          AND guid IS NOT NULL
        ORDER BY guid, published_at DESC NULLS LAST, id DESC
    '''
    with psycopg.connect(os.environ['DATABASE_URL']) as conn:
        with conn.cursor() as cur:
            cur.execute(query, (datetime.now(timezone.utc) - timedelta(days=days),))
            rows = cur.fetchall()
    rows.sort(key=lambda r: (r[5] or datetime.min.replace(tzinfo=timezone.utc), r[0]), reverse=True)
    return rows[:limit] if limit > 0 else rows


def make_input(title, summary, content):
    title = ' '.join((title or '').split())[:240]
    body = ' '.join((summary or content or '').split())[:650]
    return (title + '. ' + body).strip('. ') if body else title


def score_relevance(similarities, domains, slices, cfg):
    # Preserve the cosine values for transparency; the score is a heuristic,
    # NOT a probability, risk level, or a model-trained calibration.
    low = float(cfg.get('similarity_floor', 0.30))
    high = float(cfg.get('similarity_ceiling', 0.80))
    if not 0 <= low < high <= 1:
        raise ValueError('similarity_floor/ceiling invalides')
    details = []
    for d, (a, b) in zip(domains, slices):
        sim = float(np.max(similarities[a:b]))
        base = max(0.0, min(1.0, (sim - low) / (high - low)))
        weight = float(d.get('weight', 0))
        # Limited weight impact: a relevant article should not be discarded
        # solely because its primary category has a lower strategic weight.
        weight_factor = 0.75 + 0.25 * max(0.0, min(1.0, weight / 25.0))
        details.append((d['name'], sim, base * weight_factor))
    ranked = sorted(details, key=lambda x: x[2], reverse=True)
    primary = ranked[0]
    secondary = ranked[1] if len(ranked) > 1 else ('', 0.0, 0.0)
    # Blend two related interests, but avoid cumulative inflation across 7 domains.
    score = round(100 * (0.85 * primary[2] + 0.15 * secondary[2]))
    return int(max(0, min(100, score))), primary[0], primary[1], ranked


def urgency_flags(title, summary):
    """Transparent keyword signals, NOT proof of exploitation or severity."""
    text = f'{title or ""} {summary or ""}'.lower()
    rules = {
        'exploitation_mention': r'\b(actively exploited|exploited in (the wild|attacks)|exploitation active|activement exploit[eé]e|exploitées? dans des attaques)\b',
        'critical_mention': r'\b(critical vulnerability|critical flaw|critique|cvss\s*(?:9\.[0-9]|10(?:\.0)?)|remote code execution|\brce\b)\b',
        'ransomware_mention': r'\b(ransomware|rançongiciel)\b',
    }
    return [name for name, pattern in rules.items() if re.search(pattern, text, flags=re.I)]


def main():
    start = time.monotonic()
    cfg, domains = load_config()
    days = int(os.getenv('SEMANTIC_LOOKBACK_DAYS', cfg.get('lookback_days', 7)))
    limit = int(os.getenv('SEMANTIC_TEST_LIMIT', cfg.get('test_limit', 0)))
    batch_size = int(cfg.get('batch_size', 24))
    articles = fetch_articles(days, limit)
    if not articles:
        print(f'Aucun article avec GUID publié sur les {days} derniers jours. Base inchangée.')
        return
    descriptions, slices = [], []
    for domain in domains:
        begin = len(descriptions)
        descriptions.extend(domain['descriptions'])
        slices.append((begin, len(descriptions)))
    inputs = [make_input(r[1], r[2], r[3]) for r in articles]
    print(f'Fenêtre: {days} jours ; articles uniques (GUID): {len(articles)} ; domaines: {len(domains)} ; profils: {len(descriptions)}', flush=True)
    model = SentenceTransformer(MODEL_NAME, device='cpu')
    vectors = model.encode(descriptions + inputs, batch_size=batch_size,
                           normalize_embeddings=True, show_progress_bar=True)
    similarities = np.matmul(vectors[len(descriptions):], vectors[:len(descriptions)].T)
    results = []
    for row, sims in zip(articles, similarities):
        score, domain, sim, details = score_relevance(sims, domains, slices, cfg)
        flags = urgency_flags(row[1], row[2])
        results.append({'id': row[0], 'title': row[1] or '', 'old': row[4],
                        'score': score, 'domain': domain, 'sim': sim,
                        'flags': flags, 'details': details})
    results.sort(key=lambda r: (r['score'], r['sim']), reverse=True)
    print('\nTOP 25 — nouveau / historique / domaine / similarité / signaux / titre')
    for r in results[:25]:
        print(f"{r['id']:>6} {r['score']:>3} / {str(r['old']):>5} {r['domain']:<24} {r['sim']:.3f} {','.join(r['flags']) or '-':<35} {r['title'][:85]}")
    print('\nCONTRÔLES — exemples ciblés')
    for probe in ['sonicwall', 'netscaler', 'anthropic', 'ventoy', 'green card', 'dgfip', 'tatouage', 'openai']:
        matches = [r for r in results if probe in r['title'].lower()]
        for r in matches[:2]:
            print(f"{r['id']:>6} {r['score']:>3} / {str(r['old']):>5} {r['domain']:<24} {r['sim']:.3f} {r['title'][:95]}")
    print('\nDomaines dominants (sur les articles récents) :')
    for name, count in Counter(r['domain'] for r in results).most_common():
        print(f'  {name}: {count}')
    print(f'\nDurée: {time.monotonic() - start:.1f}s ; aucune écriture en base.')
    print('Note : les signaux de criticité sont des mentions textuelles, non des faits vérifiés.')

if __name__ == '__main__':
    main()
