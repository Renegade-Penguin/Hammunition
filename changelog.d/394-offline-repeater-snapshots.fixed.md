- Repeater lists fetched on request (ETCC, Brandmeister, hearham) are now read from a
  `file://` Bunker export, with the same confinement as the catalogue (no symlinked
  components, regular files only, size-bounded), instead of silently skipping it for
  the publisher; under `--offline` a list the export does not hold is refused,
  naming `repeater-snapshots/<name>` (#394, D-078, D-085).
