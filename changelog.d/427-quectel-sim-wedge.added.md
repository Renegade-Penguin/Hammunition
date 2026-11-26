- A Quectel EM160R-GL whose SIM comes back from a suspend or a boot in QMI state
  `illegal` (ModemManager then fails it and never retries) is recovered by a new
  resume step: `hardware apply` installs `hammunition-modem-resume.service`, which makes
  one bounded SIM power-cycle attempt, reports the steps it ran, and exits non-zero
  naming the manual steps if it fails. `hardware state` shows "SIM wedged" instead of a
  silent off state. `--no-modem-resume` leaves it out. New catalog entry `quectel-em160r-gl`
  (#427, D-058 amendment 2026-10-27).
