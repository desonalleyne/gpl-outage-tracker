# GPL Outage Tracker

GPL posts outage notices on Facebook and then deletes them, usually by the next day. That makes it hard to look back and see how often the lights actually go off, where, and what GPL said the reason was.

This repo keeps a copy of every post. Every 30 minutes a GitHub Action reads the page and saves anything new to `data/posts.json`. The dashboard is `index.html`, served by GitHub Pages.

## What gets counted

Each post is sorted into one of these:

- unplanned outage
- planned outage
- load shedding (only when GPL actually says so)
- restoration or update (not counted as a new outage)
- anything else, like job ads (ignored)

The stated reason comes from the wording of the post: a vehicle, trees, weather, equipment, generation, and so on. Most "a feeder went offline" posts don't give a reason at all, so those show up as "No reason given".

"Suspected load shedding" is my own guess, not something GPL says. It flags unplanned outages that give no reason and either happen between 5 and 11 pm, or come within 3 hours of a different feeder going off on the same grid. Each flagged post shows which of those it hit, so you can judge for yourself.

## Setting it up

1. Make the repo public, so Pages and Actions are free.
2. Add the RSS feed link as a secret called `FEED_URL`: Settings → Secrets and variables → Actions.
3. Turn on Pages: Settings → Pages, deploy from `main`, folder `/ (root)`.
4. Go to Actions → Capture GPL posts → Run workflow to kick off the first run.

The workflow file lives at `.github/workflows/capture.yml`.

If you want to see the dashboard before any posts have come in, add `?demo` to the end of the URL. That loads fake sample data.

## Fixing mistakes

Don't edit `data/posts.json` by hand. Every run recalculates it from the post text, so your changes would be wiped.

If the same mistake keeps coming up, fix the rule in `capture.py`. Place names are in the `AREAS` list, and the keywords for reasons are in `CAUSES`. The next run will update every saved post, old ones included.

If it's just one post that came out wrong, add a correction to `corrections.json` using the post's `id`:

```json
{
  "https://m.facebook.com/GuyanaPowerAndLight/posts/pfbid0...": {
    "areas": ["Best", "Vriesland"],
    "cause": "Equipment failure"
  }
}
```

Whatever you put there wins over the calculated value on every run.

## Known gaps

- The feed only holds the latest 10 or so posts. If rss.app doesn't check often enough, a post GPL deletes quickly can be missed.
- Some notices are just an image with little text. Those are saved, but the areas and times on the image aren't read.
