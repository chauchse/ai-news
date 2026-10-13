"""One explicit discovery slot while preserving category counts and critical alerts."""
import random
from feedback.common import digest


def discover(assessed, selected, cfg, briefing_id):
    minimum = cfg['briefing'].get('business_triage',{}).get('min_strategic_importance',2)
    removable = [x for x in selected if not x.get('priority')]
    categories = {x['category'] for x in removable}
    candidates = [x for x in assessed if x not in selected and x['category'] in categories
                  and x['technology']>=2 and x['importance']>=minimum]
    # Keep most uncertain half, then sample uniformly; include selection probability.
    candidates.sort(key=lambda x:(abs(x.get('learned_utility',.5)-.5),x['group'][0]['presentation_id']))
    pool = candidates[:max(1,(len(candidates)+1)//2)]
    if not pool:
        return selected,{'status':'no_eligible_discovery_candidate'}
    seed = digest(['discovery-v1',briefing_id])
    choice = random.Random(seed).choice(pool)
    victim = next(x for x in reversed(removable) if x['category']==choice['category'])
    # Do not introduce an event already excluded as a near-duplicate in this briefing.
    from feedback.repository import source_key
    existing = {source_key(a) for x in selected if x is not victim for a in x['group']}
    if any(source_key(a) in existing for a in choice['group']):
        return selected,{'status':'source_overlap'}
    new = [choice if x is victim else x for x in selected]
    return new,{'status':'selected','seed':seed,'presentation_id':choice['group'][0]['presentation_id'],
                'replaced':victim['group'][0]['presentation_id'],'probability':1/len(pool),
                'pool':[x['group'][0]['presentation_id'] for x in pool],
                'mechanism':'uniform_in_most_uncertain_half_with_category_replacement'}
