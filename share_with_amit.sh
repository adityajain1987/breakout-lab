#!/bin/bash
# Generate today's HTML report + push to GitHub Pages.
# Stable URL: https://adityajain1987.github.io/breakout-lab-share/
# Send that URL to Amit ONCE — bookmarkable, updates daily when this script runs.
#
# Usage:
#   ./share_with_amit.sh                 # generate + push to GitHub Pages
#   ./share_with_amit.sh --top 20        # bigger table
#   ./share_with_amit.sh --features 8    # more featured stocks
#   ./share_with_amit.sh --catbox        # also upload temp catbox link (3-day backup)

set -e
cd "$(dirname "$0")"

PUBLIC_URL="https://adityajain1987.github.io/breakout-lab-share/"
SHARE_DIR="$HOME/Desktop/Claude/breakout-lab-share"

# Parse our flags vs flags to pass through to the report generator
USE_CATBOX=false
GEN_ARGS=()
for arg in "$@"; do
    if [[ "$arg" == "--catbox" ]]; then
        USE_CATBOX=true
    else
        GEN_ARGS+=("$arg")
    fi
done

echo "→ Generating fresh HTML report..."
.venv/bin/python -m reports.generate_html_report "${GEN_ARGS[@]}"

# Find the latest report
LATEST=$(ls -t reports/breakout_lab_*.html | head -1)
SIZE=$(du -h "$LATEST" | cut -f1)
echo ""
echo "→ Local file: $LATEST ($SIZE)"

# --- Push to GitHub Pages ---
if [ -d "$SHARE_DIR/.git" ]; then
    echo ""
    echo "→ Updating GitHub Pages..."
    cp "$LATEST" "$SHARE_DIR/index.html"
    # Also save a date-stamped archive copy so old reports are still browsable
    DATE_TAG=$(basename "$LATEST" .html | sed 's/breakout_lab_//')
    cp "$LATEST" "$SHARE_DIR/${DATE_TAG}.html"

    cd "$SHARE_DIR"
    git add index.html "${DATE_TAG}.html" 2>/dev/null
    if git diff --cached --quiet; then
        echo "  (no changes — skipping push)"
    else
        git commit -m "Update report for ${DATE_TAG}" --quiet
        git push --quiet
        echo "  ✓ Pushed to GitHub. Pages will refresh in ~30 sec."
    fi
    cd - >/dev/null
    echo ""
    echo "═══════════════════════════════════════════════════════════"
    echo "  ✅ Live at (stable URL Amit can bookmark):"
    echo ""
    echo "     $PUBLIC_URL"
    echo ""
    echo "  📁 Archive of all daily reports:"
    echo "     https://github.com/adityajain1987/breakout-lab-share"
    echo ""
    echo "═══════════════════════════════════════════════════════════"
    if command -v pbcopy >/dev/null 2>&1; then
        echo "$PUBLIC_URL" | pbcopy
        echo "  📋 (URL copied to clipboard — paste into WhatsApp / iMessage)"
    fi
else
    echo "  ⚠️  $SHARE_DIR not initialized as git repo. Run setup once."
fi

# --- Optional: also upload to catbox (temp backup) ---
if [ "$USE_CATBOX" = true ]; then
    echo ""
    echo "→ Also uploading to catbox.moe (temp backup, 72-hour expiry)..."
    URL=$(curl -sS -F "reqtype=fileupload" -F "time=72h" -F "fileToUpload=@${LATEST}" \
        https://litterbox.catbox.moe/resources/internals/api.php)
    if [[ "$URL" =~ ^https://litter ]]; then
        echo "  ✓ Backup link: $URL"
    fi
fi

# Log the share event
mkdir -p logs
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ)  $PUBLIC_URL  ($LATEST)" >> logs/shared_links.log
