# Recorded from live Kalshi data captured into tests/fixtures/kalshi_markets_typed.json
# (Task 1, 2026-09-06):
#
# Total-market `title` wording differs by sport and does NOT include team names:
#   - KXNFLTOTAL:    "Will there be over 63.5 points scored?"
#                    "Will there be over 60.5 points scored?"
#   - KXNCAAFTOTAL:  "Over 73.5 points scored"
#                    "Over 70.5 points scored"
#
# Totals markets do NOT carry a `custom_strike` field (absent entirely; the strike
# is only encoded in the ticker suffix, e.g. "-64", and in the title's point number).
# By contrast, KXNFLGAME/KXNFLSPREAD/KXNCAAFGAME/KXNCAAFSPREAD markets DO carry
# `custom_strike": {"football_team": "<uuid>"}` identifying the team side.
#
# Task 5 fills in the matching tests below.
