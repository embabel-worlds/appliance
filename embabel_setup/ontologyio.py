"""`embabel realm import owl|skos|ossie|okf` and `embabel realm export owl|okf`: open standards in,
open standards out, with no appliance running.

The server image carries `ontology-io`, a second command beside `realm-validate`: an OWL or RDFS
ontology (Turtle, RDF/XML, N-Triples, JSON-LD), a SKOS scheme, an Apache Ossie model or an OKF bundle
becomes a realm draft — realm.ts, DERIVE rule sets, an alignment of every name to its IRI, the source
itself under ontology/source/, and a mapping report naming what was refused — and a realm goes back
out as OWL or as an OKF bundle. This runs it in a throwaway container, as realmvalidate.py runs the
validator:

    docker run --rm --entrypoint ontology-io -v <file dir>:/in:ro -v <out parent>:/out <image> \
        import owl /in/<file> --out /out/<name> [options]

An OKF bundle is a directory: it is mounted at /in itself.

THE IMAGE IS THIS APPLIANCE'S, so what the import writes is what the server you will install into
loads, and `embabel realm validate <out>` says whether it does. Nothing running is touched.
"""

from __future__ import annotations
import os
import subprocess

from .realmvalidate import DOCKER_COULD_NOT_RUN, NO_SUCH_COMMAND

ENTRYPOINT = "ontology-io"


def import_argv(ontology: str, out: str, image: str, *, fmt: str = "owl", name: str | None = None, mode: str | None = None,
                sparql_endpoint: str | None = None, data_url_base: str | None = None,
                term_label: str | None = None, ontology_doc: str | None = None, mapping_doc: str | None = None,
                jdbc_url: str | None = None, imports: str | None = None, catalog: str | None = None,
                no_resolve_namespaces: bool = False, roots: list[str] | None = None) -> list[str]:
    """The `docker run` that imports [ontology] into the directory [out].

    The file's directory is mounted at /in, so an Ossie mapping's relative references, and the
    --ontology and --mapping files, resolve when they sit beside it.
    """
    ontology = os.path.abspath(ontology)
    out = os.path.abspath(out)
    parent = os.path.dirname(out) or os.sep
    bundle = fmt == "okf"
    in_dir = ontology.rstrip(os.sep) if bundle else os.path.dirname(ontology)
    argv = ["docker", "run", "--rm", "--entrypoint", ENTRYPOINT,
            "-v", f"{in_dir}:/in:ro", "-v", f"{parent}:/out"]
    tail = ["import", fmt, "/in" if bundle else f"/in/{os.path.basename(ontology)}", "--out", f"/out/{os.path.basename(out)}"]
    for flag, value in (("--name", name), ("--mode", mode), ("--sparql-endpoint", sparql_endpoint),
                        ("--data-url-base", data_url_base), ("--term-label", term_label), ("--jdbc-url", jdbc_url)):
        if value:
            tail += [flag, value]
    for flag, value in (("--ontology", ontology_doc), ("--mapping", mapping_doc)):
        if value:
            if os.path.dirname(os.path.abspath(value)) != in_dir:
                raise ValueError(f"{flag} must sit in the same directory as {ontology}: only that directory is mounted")
            tail += [flag, f"/in/{os.path.basename(value)}"]
    if imports:
        tail += ["--imports", imports]
    if catalog:
        if os.path.abspath(catalog.rstrip(os.sep)) != in_dir:
            raise ValueError(f"--catalog must be the file's own directory, {in_dir}: only that directory is mounted")
        tail += ["--catalog", "/in"]
    if no_resolve_namespaces:
        tail.append("--no-resolve-namespaces")
    for root in roots or []:
        tail += ["--root", root]
    return argv + [image] + tail


def export_argv(realm: str, out: str, image: str, *, fmt: str = "owl", base: str | None = None) -> list[str]:
    """The `docker run` that exports the realm checkout [realm] to [out]: a Turtle file for owl, a bundle directory for okf."""
    realm = os.path.abspath(realm)
    out = os.path.abspath(out)
    parent = os.path.dirname(out) or os.sep
    argv = ["docker", "run", "--rm", "--entrypoint", ENTRYPOINT,
            "-v", f"{realm}:/in/{os.path.basename(realm.rstrip(os.sep)) or 'realm'}:ro", "-v", f"{parent}:/out"]
    tail = ["export", fmt, f"/in/{os.path.basename(realm.rstrip(os.sep)) or 'realm'}", "--out", f"/out/{os.path.basename(out)}"]
    if base:
        tail += ["--base", base]
    return argv + [image] + tail


def run(argv: list[str], image: str) -> int:
    """Run, streaming the tool's own output; only docker's failures get a line from here.

    The converter's exit status: 0 done, 1 done with refusals (the report names them), 2 could not run.
    """
    try:
        status = subprocess.run(argv).returncode
    except FileNotFoundError:
        print("  Docker is not installed, and the converter runs in the appliance's image.")
        return 2
    if status == NO_SUCH_COMMAND:
        print(f"  {image} predates `ontology-io`. Upgrade the appliance, or name a newer image with --image.")
        return 2
    if status == DOCKER_COULD_NOT_RUN:
        print(f"  Docker could not run {image} — is Docker running, and can it pull that image?")
        return 2
    return status


def run_import(ontology: str, out: str, image: str, **options) -> int:
    if options.get("fmt") == "okf":
        if not os.path.isdir(ontology):
            print(f"  Not a directory: {ontology} (an OKF bundle is a directory with an index.md)")
            return 2
    elif not os.path.isfile(ontology):
        print(f"  Not a file: {ontology}")
        return 2
    os.makedirs(os.path.dirname(os.path.abspath(out)) or os.sep, exist_ok=True)
    try:
        argv = import_argv(ontology, out, image, **options)
    except ValueError as e:
        print(f"  {e}")
        return 2
    return run(argv, image)


def run_export(realm: str, out: str, image: str, *, fmt: str = "owl", base: str | None = None) -> int:
    if not os.path.isdir(realm):
        print(f"  Not a directory: {realm}")
        return 2
    os.makedirs(os.path.dirname(os.path.abspath(out)) or os.sep, exist_ok=True)
    return run(export_argv(realm, out, image, fmt=fmt, base=base), image)
