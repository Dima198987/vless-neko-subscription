# VLESS → NekoBox subscription (GitHub Actions)

Collects VLESS share links from OutlineKeys, tests each candidate using `sing-box` plus an HTTP request through its local SOCKS5 proxy, and writes successful links to `working_vless.txt`.

Use only servers you are permitted to use. Public proxy nodes are untrusted: avoid sensitive accounts and traffic.

## Setup

1. Create a GitHub repository. For a directly importable raw URL, it must be **public**; this means anyone can view the collected profile links.
2. Upload all project files, preserving `.github/workflows/update.yml`.
3. Under **Settings → Actions → General → Workflow permissions**, allow read/write permissions so the workflow can commit the refreshed list.
4. Go to **Actions → Update VLESS subscription** and choose **Run workflow** for the first run.
5. Check the workflow logs and `working_vless.txt` after it completes.

The workflow runs hourly using `0 * * * *` (UTC). GitHub may delay scheduled runs during high load; it is not guaranteed to start exactly at the hour. Scheduled workflows run from the default branch.

## NekoBox subscription URL

After you push the repository and the file exists on its default branch, replace `OWNER` and `REPO` in:

`https://raw.githubusercontent.com/OWNER/REPO/main/working_vless.txt`

If your default branch is not `main`, replace `main` with its name. In NekoBox, add/import from URL and paste that raw URL. The URL is public, so every VLESS link in the file is public too.

## Notes

- The collector scans the source page and up to `MAX_PAGES` page-number variants; site layout changes may require updating the scraper.
- It checks up to `MAX_NODES` profiles per run. Defaults: 5 pages and 100 nodes.
- Real HTTP success is a point-in-time test, not a guarantee of reliability or privacy.
- Some VLESS transports or URI parameters may not translate to sing-box and are skipped rather than treated as working.
- If the list is empty, inspect the Actions logs. The source may have changed, or no candidates may have passed the test.
