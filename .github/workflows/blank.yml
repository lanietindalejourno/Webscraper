name: Update monitor

on:
  schedule:
    - cron: "0 */6 * * *"
  workflow_dispatch:

permissions:
  contents: write

jobs:
  update:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install requests beautifulsoup4
      - run: python scrape.py
      - name: Save results
        run: |
          git config user.name "monitor-bot"
          git config user.email "monitor-bot@users.noreply.github.com"
          git add docs
          git diff --staged --quiet || git commit -m "Update monitor"
          git push
