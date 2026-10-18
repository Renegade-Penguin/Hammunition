- `maps repeaters fetch-etcc`, `fetch-brandmeister` and `fetch-hearham` take `--offline`,
  which reads the list only from the station's Bunker and refuses, naming
  `repeater-snapshots/<name>`, when it is missing; a hostile deeply nested JSON export
  is a typed input error, and console errors name the export, not where it is mounted
  (#394).
