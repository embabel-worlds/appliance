# wasm-modules

The operator's wasm dependency module store, mounted read-only into the worlds
server at `/wasm-modules`. Empty means no realm that declares wasm dependencies
(SQLite, H3, ...) can load them; that is the fail-closed default, not an error.

To provision it, run the engine repo's script, which hashes module bytes into
content-addressed files (`<sha256>.wasm`), writes `allowlist.json`, and stamps the
matching digests into your realm checkouts:

    <engine-repo>/scripts/wasm/provision-dependency-store.py \
        --store <this directory> \
        --realm <realms-dir>/realm-ledger

Placing bytes and an allowlist entry here is the authorization decision: a realm's
declared digest must match a file in this store byte for byte, and the allowlist
entry decides the capability kind and callable methods. `EMBABEL_WASM_MODULES_DIR`
in `.env` points the mount anywhere else.
