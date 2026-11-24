# workspace/

The default mount for **your projects' working trees**, read by the code realms
(`realm-polyglot-code` and the realms built on it). A project key that is a bare
folder name reads the folder of that name here, live: `shop` reads
`workspace/shop`, as it is on disk now, edits and all.

Point it at the folder your checkouts already live in instead, in `.env`:

```bash
EMBABEL_PROJECT_WORKSPACE=~/src
```

Read-only. You and your coding agent edit on the host; the appliance only reads,
which is what lets it answer about code that has not been pushed yet.
