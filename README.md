# GPL Outage Tracker

GPL posts outage notices on Facebook and then deletes them, usually by the next day. That makes it hard to look back and see how often the lights actually go off, where, and what GPL said the reason was.

This repo keeps a copy of every post. Every 15 minutes a small server reads GPL's page through Facebook's embeddable page widget, which doesn't need a login, and saves anything new to `data/posts.json`. Nothing gets removed from that file, so a post stays even after GPL deletes it, and gets marked as deleted. The dashboard is `index.html`, served by GitHub Pages.

## What gets counted

Each post is sorted into one of these:

- unplanned outage
- planned outage
- load shedding (only when GPL actually says so)
- restoration or update (not counted as a new outage)
- anything else, like job ads (ignored)

The stated reason comes from the wording of the post: a vehicle, trees, weather, equipment, generation, and so on. Most "a feeder went offline" posts don't give a reason at all, so those show up as "No reason given".

"Suspected load shedding" is my own guess, not something GPL says. It flags unplanned outages that give no reason and either happen between 5 and 11 pm, or come within 3 hours of a different feeder going off on the same grid. Each flagged post shows which of those it hit, so you can judge for yourself.

## Running it on a server

It runs on an EC2 instance, since Facebook shows the widget to that server without asking it to log in. Any always-on Linux box should work.

1. Install git and cron. On Amazon Linux 2023 that's `sudo dnf install -y git cronie && sudo systemctl enable --now crond`. On Ubuntu, `sudo apt install -y git`.
2. Make a key the server can push with: run `ssh-keygen -t ed25519 -f ~/.ssh/gpl -N ""`, then `cat ~/.ssh/gpl.pub`. In the repo on GitHub, go to Settings → Deploy keys → Add deploy key, paste it in, and tick "Allow write access".
3. Clone the repo and point git at that key:
   ```
   GIT_SSH_COMMAND="ssh -i ~/.ssh/gpl" git clone git@github.com:<you>/gpl-outage-tracker.git ~/gpl-outage-tracker
   cd ~/gpl-outage-tracker
   git config core.sshCommand "ssh -i ~/.ssh/gpl"
   git config user.name "outage-bot" && git config user.email "outage-bot@users.noreply.github.com"
   ```
4. Run `bash run.sh` once by hand to check it works, then add it to cron with `crontab -e`:
   ```
   */15 * * * * bash ~/gpl-outage-tracker/run.sh >> ~/gpl-capture.log 2>&1
   ```

Each run adds a line to `~/gpl-capture.log` saying how many posts it saw, how many were new, and how many deletions it has caught. If the widget ever stops returning posts, the run stops and saves what Facebook sent to `last_fetch.html`.

A post counts as deleted when it's gone from the page on two runs in a row while older posts are still there. If it shows up again, the mark is taken back off.

The old GitHub Action still works with an RSS feed if you set `FEED_URL`, but turn it off while the server is running, so the two don't both commit.

## The dashboard

Turn on Pages under Settings → Pages: deploy from `main`, folder `/ (root)`. Keep the repo public so Pages stays free.

If you want to see the dashboard before any posts have come in, add `?demo` to the end of the URL. That loads fake sample data.

## The map

The outline of Guyana's regions comes from Natural Earth (public domain). Places are pinned using a hand-made list of rough coordinates, `PLACES` in `index.html`, so treat positions as approximate. A post is pinned at each place it names that's on the list. If none are, it's pinned at its region instead. If it has neither, it's counted under "not on map". Add a name and its rough coordinates to `PLACES` to get it pinned.

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

- The widget only shows GPL's most recent posts. A post that's posted and deleted between two runs (15 minutes apart) will be missed.
- Facebook could change or block the widget at any time.
- Some notices are just an image with little text. Those are saved, but the areas and times on the image aren't read.
