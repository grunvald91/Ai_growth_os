"""Compatibility entrypoint; production deploys include app/, not scripts/."""
from app.ops.recover_campaign_orphans import main


if __name__ == "__main__":
    main()
