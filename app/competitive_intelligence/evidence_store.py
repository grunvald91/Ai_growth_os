"""Normalize supplied facts deterministically; never infer missing publication dates."""
import hashlib
import re
from datetime import datetime, timezone

from app.competitive_intelligence.contracts import Evidence, RunInput
from app.workflows.research_contracts import ResearchSourceBundle


def normalize(bundles: list[ResearchSourceBundle], request: RunInput, now: datetime) -> list[Evidence]:
    seen = set()
    evidence = []
    competitors = {name.strip().casefold() for name in request.competitors}
    for bundle in bundles:
        for source in bundle.sources:
            if len(evidence) >= 50:
                return evidence
            excerpt = source.excerpt
            # Syndicated identical excerpts for one competitor count only once, even at another URL.
            normalized = re.sub(r"\s+", " ", excerpt).strip().casefold()
            digest = hashlib.sha256((source.competitor.casefold() + "\n" + normalized).encode()).hexdigest()
            published = source.published_at
            if published is not None:
                published = published.replace(tzinfo=timezone.utc) if published.tzinfo is None else published.astimezone(timezone.utc)
            observed = now
            if source.observed_at:
                try:
                    observed = datetime.fromisoformat(source.observed_at.replace("Z", "+00:00"))
                    observed = observed.replace(tzinfo=timezone.utc) if observed.tzinfo is None else observed.astimezone(timezone.utc)
                except ValueError:
                    pass
            reason = None
            if digest in seen:
                reason = "duplicate"
            elif competitors and source.competitor.strip().casefold() not in competitors:
                reason = "competitor_filter"
            elif published and published > now:
                reason = "future_publication_date"
            elif request.date_from or request.date_to:
                if published is None:
                    reason = "unknown_publication_date"
                elif request.date_from and published.date() < request.date_from:
                    reason = "outside_period"
                elif request.date_to and published.date() > request.date_to:
                    reason = "outside_period"
            seen.add(digest)
            age = max(0, (now - published).days) if published else None
            evidence.append(Evidence(
                id=f"e{len(evidence) + 1}", competitor=source.competitor,
                source_url=source.url, source_type=source.source_type, platform=source.platform,
                published_at=published, observed_at=observed, supplied_observed_at=source.observed_at,
                excerpt=excerpt, rating=source.rating, dedupe_hash=digest,
                recency_score=round(1 / (1 + age / 180), 3) if age is not None else None,
                exclusion_reason=reason,
            ))
    return evidence
