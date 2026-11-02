- CI no longer cancels the pytest legs at GYST's 15-minute default: the suite
  takes about 12.6 minutes on a fast runner, so `ci.yml` now passes
  `timeout-minutes: 30` to `python-ci.yml` (#433). The 3DEP 1/3-arc-second
  tile list is regenerated with `scripts/gen_3dep_tiles.py --fetch` because
  USGS re-staged 57 tiles, which failed the weekly pin-review job (D-068).
