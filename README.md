# SPC Meals Notifier

Push notifications for St Peter's College meal times, sent to your phone each day during term.

---

## Get notifications on your phone

**This is all you need to do** — no setup required on your end.

1. Download the **ntfy** app: [iOS (App Store)](https://apps.apple.com/app/ntfy/id1625396347) · [Android (Play Store)](https://play.google.com/store/apps/details?id=io.heckel.ntfy)
2. Subscribe to whichever meals you want notifications for:
   - `spc-meals-breakfast`
   - `spc-meals-lunch`
   - `spc-meals-supper`
3. That's it. Notifications arrive at ~7am, ~11am, and ~4:30pm on term days.

> **Note:** Notifications are sent from a Mac running this script. They won't arrive if that machine is off or disconnected.

---

## How it works

- A Python script runs 3 times a day via macOS LaunchAgent (7am, 11am, 4:30pm)
- It scrapes the [SPC meals page](https://www.spc.ox.ac.uk/student-life/living-at-st-peters/meals) and parses today's meal times and menu
- It sends a push notification to [ntfy.sh](https://ntfy.sh) on the relevant topic
- ntfy delivers it to anyone subscribed to that topic
- During vacations, no notification is sent; if hall is closed on a given day, a closed notification is sent instead

> **Note:** This topic is public, so people other than me could send notifications to it.

---

## Run your own copy

If you want to run this independently (so notifications don't depend on someone else's machine):

**Requirements:** macOS, Python 3

```bash
git clone https://github.com/ewmitchell/spc-meals.git
cd spc-meals
cp config.json.example config.json
# Edit config.json: set log_file to an absolute path on your machine,
# and change ntfy_topic_prefix to something unique
./install.sh
```

`install.sh` registers a LaunchAgent that runs the script automatically on schedule.
