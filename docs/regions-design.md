# Regions as an attribute: study areas, places and coverage

Status, 2026-11-13: built. This document describes how the platform treats place now,
why, and what is still open.

## Principles

The platform should work for any place, not only the two it started with.

- **A region is an optional attribute, never a primary identifier.** Nothing looks
  other things up by it.
- **Places are looked up, never created.** Countries and administrative units exist in
  the world; nothing about one is ours to define. Users and admins cannot create,
  edit or delete regions.
- **A run always has a spatial extent; what is optional is a named place for it.**

A region used to be the key everything hung from: a dataset belonged to one, a run was
made in one, and it decided what "onset" meant and which boundaries existed. That made a
global archive register once per region and made every new place a catalogue row, so
the one catalogue row's five unrelated jobs now sit with their natural owners:

| Job | Owner now |
| --- | --- |
| Join key between datasets | The dataset's own coverage |
| Scoring extent | The run's study area |
| Administrative boundaries | The place itself |
| Season and event meaning | Season presets and the run's onset definition |
| Label | The run's study area |

## The model

### Study area

A run's spatial selection is its **study area**: a box, or named places. It is a value
on the run, frozen with the run like every other setting. It is stored under the
`focus_area` key, which old runs already use.

- A **box** is four coordinates.
- **Places** name their country (`iso`), a level (`adm0` country, `adm1`, `adm2`) and
  the unit names, and carry their outlines, attached at submission so the run keeps the
  exact geometry it was scored on. The Modal app turns outlines into a mask on the
  observation grid.

A run with no study area is scored on the overlap of its datasets. Units stop at
districts because sub-districts are often smaller than a grid cell.

A workspace can hold a default study area in its settings. It prefills new runs, is the
area for any run that names none, and names the workspace's place in the summary. The
run forms can still choose "All of the data".

### Places

A place is a country or an administrative unit inside one, from geoBoundaries. Places
are reference data: read, never written. The server exposes the country list and a
country's outline or the units inside it, by country code with no region involved. The
assistant turns a name into a country code (`find_country`) and lists a country's areas
(`list_areas`). Places other than administrative units (river basins, "the Sahel") are
out of scope; a box covers them until uploaded shapes are designed.

### Dataset coverage

A dataset's `spatial_bounds` is what it covers, inferred at registration. Its `region`
is only an optional label. Two sources go together when both have data somewhere in
common; one rule, shared by the server and the web client, is used to offer models and
to refuse a benchmark or blend whose forecast source has no data where the observations
do. A global archive is registered once and serves every study area inside it. A source
of unknown extent (registered before extents were inferred) is matched by its region
label.

### What ROMP is given

ROMP's built-in Ethiopia and India definitions stay, so past results stay comparable.
The accommodation is one rule at the ROMP boundary:

| Study area | ROMP region | Mask |
| --- | --- | --- |
| The country itself, where ROMP has a built-in (ETH, IND) | ROMP's named region | none |
| Units inside such a country | ROMP's named region | the units' outlines |
| Places in any other country | `custom`, bounded by the places' extent | the places' outlines |
| A box with a region | the run's region | the box |
| A box with no region | `custom`, bounded by the datasets' overlap | the box |
| None | `custom`, bounded by the datasets' overlap | none |

A run's region is the one asked for, else the one its observations are filed under,
else its model's. A region the data does not reach is refused before anything runs. A
run scored by place records no `region_id`, the place names as `region_name`, and
`custom` as the ROMP region.

### Season and event definition

Neither is a property of a place: a place can have several seasons, and one season can
span places. A season is a named window. Season presets live in their own packaged file,
each listing the countries where it is the usual season (only Kiremt is packaged). A
workspace with no season of its own starts from the preset usual in its country, taken
from its study area, else its runs' region. A run records the dates it used; nothing
looks a season up by place. The onset definition is chosen for the run the same way.

Blends are told which units to train on (`use_adm3_domain`), decided from the blend's
country: its area's when it names one, else the country its observations are filed
under. A blend from before that still reads its region.

## Rollback contract

Deploys run migrations before routing traffic, so a rollback runs old code on the new
schema. Everything about regions is additive first:

- Job configs keep `region_id`, `region_name` and `romp_region` for runs that have
  them. New runs write the study area and keep writing `region_name` as the label, so
  the previous release still shows a name.
- `data_sources.region` stays as a nullable column. It stops being required, then stops
  being read, and is only dropped after that.

Still there, on purpose:

- **Listing regions and reading their boundaries.** Run setup and the Data page still
  read both.
- **Rows that are not built in.** A region someone drew earlier is still read and
  scored as before. Deleting those rows, and then the columns only they use, waits for a
  later release, so a rollback of this one still finds what it expects.
- **The data-management setting.** It only decides who can register datasets.

## Known limits

- **Longitude convention.** Outlines and overlaps use −180 to 180. A dataset on a 0 to
  360 grid does not overlap a place west of Greenwich, so such a run is refused as not
  overlapping.
- **Observations filed under no region, with no place named, are blended on grid
  cells** even when they cover Ethiopia, because nothing says which country they are in.
  Naming Ethiopia as the area, or filing the observations under it, restores the
  sub-district blend.
- **The Data page's region field** is a label and stays a list of catalogue regions.
- **A benchmark's region fallback.** Old configs with no region still fall back to
  ROMP's Ethiopia in the ROMP adapter; new submissions always set one.
- **Choosing a season preset by hand.** A preset applies as a workspace's starting
  season; the season form does not list presets to pick from.
- **Region-id wording.** The web onset helpers still call the season a monsoon only for
  India.
- **The blend app's country decision has not run on Modal**; it is covered by tests of
  the functions it touches, not by a deployed run.

## Open questions

1. **A box inside a country ROMP knows.** It keeps ROMP's named region with the box as
   a mask when the run has a region. Should a box name the country it sits in, or take
   the custom path like every other box?
2. **What ROMP's named regions carry** beyond a mask. It decides how soon a
   boundary-derived mask could replace them.
3. **Study areas across countries.** Places name one country; a selection spanning two
   is a box. Should it be a list of places?
4. **Large study areas.** With global datasets a user can ask for a very large area.
   Does the cost check need an area limit, and how does staging subset a global archive
   for a small study area?
5. **Where season presets live.** Packaged configuration, or the database so an admin
   can add one.
