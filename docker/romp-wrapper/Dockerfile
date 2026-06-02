FROM us-central1-docker.pkg.dev/ai-almanac/almanac/romp:latest

# Ensure Cloud Run staging and post-ROMP Earth2Studio metrics dependencies are available.
RUN pip install --no-cache-dir google-cloud-storage "earth2studio[data]" gcsfs zarr

COPY entrypoint.sh /almanac-entrypoint.sh
RUN chmod +x /almanac-entrypoint.sh

ENTRYPOINT ["/almanac-entrypoint.sh"]
