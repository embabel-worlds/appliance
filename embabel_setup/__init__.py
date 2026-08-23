"""The appliance's setup, as modules rather than one file.

WHY THIS EXISTS. setup.py grew to 3,860 lines. Its section comments were doing a
module's job — announcing a boundary the language was not enforcing — and
nothing stopped a function in one concern reaching into another's state. A file
that long is also unreviewable: a diff to the colour palette and a diff to the
backup logic look identical from outside.

WHAT IS AND IS NOT A MODULE HERE. The split follows the seams that already
existed as section headers, because those were drawn where the concerns actually
part. Each module states its own dependencies at the top, so the import list is
the dependency graph and a cycle is a compile error rather than a surprise.

setup.py REMAINS THE ENTRY POINT and re-exports this package, because three
things depend on that: `embabel` loads setup.py by path and reads its module
namespace, ./me.py and ./worlds.py exec it, and install.sh runs those. The
facade is not legacy — it is the contract those callers already have.
"""
