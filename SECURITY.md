# Security policy

SmartPoli handles health information, so security reports are taken seriously.

**Report a vulnerability privately**: email garvchopra85@gmail.com (or use GitHub's "Report a vulnerability" under the Security tab).
Please do not open a public issue. Include what you found, how to reproduce it, and the impact. We aim to reply within 3 days.

Please do not test against real users' data, and do not run denial-of-service or automated scanning against the live site.

Never commit secrets: `.env` is git-ignored, `.env.example` holds placeholders only. Any secret that ever reaches Git history must be rotated.
