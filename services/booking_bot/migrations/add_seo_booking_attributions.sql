-- Deploy only after BookingBot/Growth review and before setting SEO_ATTRIBUTION_SECRET.
-- No changes to existing bookings or private client data. One row per SEO-generated visit.
CREATE TABLE IF NOT EXISTS seo_booking_attributions (
    id BIGSERIAL PRIMARY KEY,
    business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    booking_id INTEGER NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
    article_slug VARCHAR(160) NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
    CONSTRAINT uq_seo_booking_attributions_booking UNIQUE (booking_id)
);
CREATE INDEX IF NOT EXISTS ix_seo_booking_attributions_business_id
    ON seo_booking_attributions (business_id);
CREATE INDEX IF NOT EXISTS ix_seo_booking_attributions_article_slug
    ON seo_booking_attributions (article_slug);
CREATE INDEX IF NOT EXISTS ix_seo_attributions_business_article_created
    ON seo_booking_attributions (business_id, article_slug, created_at);
