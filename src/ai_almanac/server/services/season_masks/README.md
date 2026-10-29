# Season masks

The cells whose rains a region's onset is about. Benchmarks and blends in a
region listed in `focus_area.SEASON_MASKS` (mirrored by
`envs/blend_recipe.SEASON_MASKS`) score only these cells unless the job turns
the mask off (`season_mask: false`) or brings its own `nc_mask`.

## `ethiopia_jjas_0p25.nc`

Ethiopia's June–September (Kiremt) rainfall areas on the 0.25° grid of
3–15°N, 33–48°E: one boolean variable, `precip`, true where the cell is kept.

- Source: `demo/data/jjas_seasonal_mask_0p25.nc` in AI-Almanac/ROMP at
  8b8a28c, byte for byte (md5 `09cf2636089c433d8e1b7d75848f9675`). The file
  records no provenance of its own.
- What it matches: against CHIRPS v3, 1998–2025, it keeps every cell where
  June–September brings at least about half the year's rain (99–100% of those
  at 50% or more, none under 30%); 51% of CHIRPS land cells in the box.
- Highlands: 85% of cells at 1,500 m or more (ERA5 orography) are kept, 90–96%
  above 2,000 m. Those left out are mostly the southern highlands, whose rains
  fall in March–May and October–December (about 36% in June–September).
- It covers the whole box, so cells in Eritrea, Sudan, South Sudan, Kenya and
  Somalia are kept or dropped by the same rule.
