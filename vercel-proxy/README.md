# MAS Ideation Vercel proxy

This zero-build Vercel project gives the Cloud Run application a stable custom
domain. All paths, including `/survey/` and the HTTP long-polling API, are
rewritten to the deployed Cloud Run service.
