# GPL Outage Tracker

Archives every post from GPL's Facebook page before it gets deleted, then charts the outages.

How it works: an RSS bridge turns the Facebook page into a feed. A GitHub Action checks that feed every 30 minutes and adds new posts to `data/posts.json`. Nothing is ever removed from that file. If a post vanishes from the feed while newer posts are still there, it's marked "Deleted by GPL". The dashboard (`index.html`) is served free by GitHub Pages.

## Setup (about 15 minutes)

1. **Make the feed.** Go to rss.app (or fetchrss.com), choose "Facebook page", paste GPL's page URL and copy the feed URL it gives you. Pick the fastest refresh your plan allows. If the bridge checks only once a day, posts that GPL deletes quickly can slip through.
2. **Create a GitHub repo.** Make it **public**: Pages and Actions minutes are free for public repos. A private repo has 2,000 Action minutes a month, so change the cron schedule to hourly. Upload everything in this folder, including the hidden `.github` folder.
3. **Add the feed URL as a secret.** Go to Settings → Secrets and variables → Actions → New repository secret. Name it `FEED_URL` and paste the feed URL as the value.
4. **Turn on the dashboard.** Go to Settings → Pages → Source: "Deploy from a branch" → `main`, folder `/ (root)`. Your site will be at `https://<you>.github.io/<repo>/`.
5. **Run it once.** Go to Actions → "Capture GPL posts" → Run workflow. After that it runs on its own.

To check the layout before any real data has come in, open the site with `?demo` on the end of the URL. This shows labelled sample data.

## Tuning

- **Areas:** `AREAS` in `scripts/capture.py` lists the place names it looks for. Add any that GPL uses and the list misses.
- **Classification:** posts are labelled unplanned, planned, load-shedding, restored or other, based on keywords (`PLANNED`, `UNPLANNED` and the others at the top of the script). Adjust these after you've looked at a few weeks of real posts.
- **Image-only posts:** many planned-outage notices are flyers with little text. The image URLs are saved in `images`, but Facebook image links expire. If those posts matter, they'll need OCR or an image download step.
- **Reprocessing:** if you change the parsing rules, existing posts keep their old labels. Delete the derived fields or re-run `extract()` over the archive to update them.
