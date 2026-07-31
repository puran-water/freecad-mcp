# Vendored workbench patches

Fixes this server depends on that are not yet upstream. Each patch names the
upstream commit it applies to, so a workbench update makes a stale patch fail
loudly rather than apply to the wrong place.

Apply against the deployed workbench, e.g. on Windows:

    /mnt/c/Users/hvksh/AppData/Roaming/FreeCAD/Mod/Quetzal

| Patch | Upstream | Status |
|---|---|---|
| `quetzal-makevalve-rating.patch` | Quetzal @ 76cc1a6 (v1.8.9) | not yet submitted |
