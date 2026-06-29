# Prod cutover: two-image + domain mappings → single image + shared LB

Moves prod onto the same architecture as staging:

- one Cloud Run service (`almanac-backend`) running the single `ai-almanac-web`
  image (FastAPI + bundled SPA) on port 8765, and
- the shared load balancer in `load_balancer.tf` instead of Cloud Run domain
  mappings (which strip `Authorization` and break Globus auth).

The LB was the staging LB extended in place: the front-end resources keep their
`almanac-staging-*` names and the prod NEG/backend/cert + a host-rule were added,
so the apply is additive (no destroy, no staging downtime).

## Status

Done (prod almanac DB snapshot taken beforehand):

1. Built + pushed `ai-almanac-web:<sha>` to Artifact Registry (deploy by sha;
   `:latest` is owned by staging and is stale).
2. Created `almanac-migrate` and ran prod DB migrations to completion.
3. Swapped the prod backend to the single image atomically with the port and a
   memory bump (the image needs ~2.4 GiB at startup; prod is now `cpu=2`,
   `memory=4Gi`):

   ```bash
   # one-time: image + port together (a port-only change breaks the old image),
   # via TF with ignore_changes[image] temporarily lifted and -var app_image set.
   ```

4. Stood up prod routing on the LB (prod NEG/backend/cert, host-rule
   `ai-almanac.org → prod`) without touching domain mappings. Verified by
   probing the LB IP with `Host: ai-almanac.org` → 200; staging unaffected.

## Remaining

5. **Point prod DNS** `ai-almanac.org` A record at the LB IP (`tofu output
   lb_ip` → `35.190.46.252`), replacing the old `ghs.googlehosted.com` mapping.
   The `almanac-prod-cert` managed cert provisions once DNS resolves here
   (15–60 min); the domain is down during that window, so flip in a maintenance
   window. `api.ai-almanac.org` is retired — its A/CNAME can be removed too.

6. **Verify** `https://ai-almanac.org` serves the SPA and Globus login works
   (Authorization now reaches the backend through the LB).

7. **Clean up** — full apply to delete the now-unused prod + staging domain
   mappings and the retired frontend services/SAs (single-image serves the SPA):

   ```bash
   tofu apply
   ```

   Do this only after step 6 verifies, so the domain mappings aren't removed
   while anything still resolves to them.
