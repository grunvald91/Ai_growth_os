"""One-shot, ServGo-only cleanup of stale full_article appendices.

This never edits the independently reviewed reader_article, approved facts,
public slug, URL, approval or publication status. A changed copy is backed up.
Run read-only first; apply requires the exact digest of all intended source
rows. No LLM calls. A separate, independently reviewed editor must handle
visible reader-copy defects.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.domain import Artifact, ArtifactStatus, Product
from app.seo.publishing import _EDITORIAL_LEAK, reader_hash, static_problems
from app.workflows.article_contracts import ArticleArtifact


def visible_text(article: ArticleArtifact) -> str:
    reader = article.reader_article
    return "\n".join(filter(None, [
        reader.title, reader.lead, reader.direct_answer or "",
        reader.meta_description or "", reader.closing, article.conversion.cta,
        *(part for section in reader.sections for part in (section.heading, section.body)),
        *(part for faq in reader.faq for part in (faq.question, faq.answer)),
    ]))


def rebuild_full(reader) -> str:
    parts = ["# " + reader.title, reader.lead]
    if reader.direct_answer:
        parts.append("**Коротко:** " + reader.direct_answer)
    parts.extend("## " + section.heading + "\n\n" + section.body
                 for section in reader.sections)
    parts.append(reader.closing)
    if reader.faq:
        parts.append("## Частые вопросы\n\n" + "\n\n".join(
            "### " + f.question + "\n\n" + f.answer for f in reader.faq))
    return "\n\n".join(filter(None, parts))


def run(*, apply: bool, expected_batch_hash: str | None = None) -> dict:
    with SessionLocal() as db:
        company_id = db.scalar(select(Product.id).where(
            Product.slug == "servgo").with_for_update())
        if company_id is None:
            raise RuntimeError("ServGo product not found")
        targets = []
        rejected = 0
        for row in db.scalars(select(Artifact).where(
                Artifact.product_id == company_id, Artifact.kind == "article")
                .order_by(Artifact.created_at, Artifact.id)):
            try:
                original = ArticleArtifact.model_validate_json(row.content)
            except (ValueError, TypeError):
                continue
            if original.reader_article is None:
                continue
            if "internal editorial verification leaked into reader copy" not in static_problems(original):
                continue
            if _EDITORIAL_LEAK.search(visible_text(original)):
                rejected += 1
                continue
            new = original.model_copy(deep=True)
            new.full_article = rebuild_full(new.reader_article)
            if new.full_article == original.full_article:
                continue
            new = ArticleArtifact.model_validate(new.model_dump(mode="json"))
            if _EDITORIAL_LEAK.search(new.full_article):
                rejected += 1
                continue
            if "internal editorial verification leaked into reader copy" in static_problems(new):
                rejected += 1
                continue
            if reader_hash(new.reader_article) != reader_hash(original.reader_article):
                raise RuntimeError("Reader changed unexpectedly; abort")
            digest = hashlib.sha256(row.content.encode("utf-8")).hexdigest()
            targets.append((row, new, digest))
        if len(targets) > 10:
            raise RuntimeError("More than ten cleanup targets: require a new review")
        batch_input = "\n".join(sorted(str(row.id) + ":" + digest
                                       for row, _, digest in targets))
        batch_hash = hashlib.sha256(batch_input.encode("utf-8")).hexdigest()
        result = {
            "mode": "apply" if apply else "dry_run",
            "target_count": len(targets),
            "reader_defects_not_touched": rejected,
            "batch_sha256": batch_hash,
            "statuses": sorted(str(row.status.value if hasattr(row.status, "value")
                                    else row.status) for row, _, _ in targets),
        }
        if not apply:
            return result
        if not expected_batch_hash or expected_batch_hash != batch_hash:
            raise RuntimeError("Batch changed since reviewed read-only preflight")
        for row, updated, digest in targets:
            if hashlib.sha256(row.content.encode("utf-8")).hexdigest() != digest:
                raise RuntimeError("Artifact changed during cleanup; rollback")
            db.add(Artifact(
                product_id=company_id, task_id=row.task_id,
                kind="seo_article_revision_backup", status=ArtifactStatus.DRAFT,
                title="Pre-appendix cleanup " + digest[:12], content=row.content,
            ))
            # Public HTML uses reader_article; unchanged reader/slug means the
            # SEO page has NOT been revised, so do not alter dateModified/version.
            row.content = updated.model_dump_json()
        db.commit()
        result["backups_created"] = len(targets)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-batch-hash", default=None)
    args = parser.parse_args()
    print("SEO_APPENDIX_CLEANUP=" + json.dumps(run(
        apply=args.apply, expected_batch_hash=args.expected_batch_hash),
        ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
