# The words the appliance says

Everything a person reads while setting up lives here, not in `setup.py`.

Prose has different editors, different reviewers and a different cadence from
the logic around it. Buried in `print()` calls it could only be changed by
somebody willing to open `setup.py`, hand-wrap to the right column, and read a
diff through a wall of quoting — so it drifted from
[worlds.embabel.com](https://worlds.embabel.com), which is the one place it
has to agree with.

## Editing

Write paragraphs. Separate them with a blank line. **Do not wrap lines and do
not indent** — `say()` wraps to 76 columns and indents by two at render time,
so a sentence that grows by two words reflows one paragraph in the diff instead
of four lines.

```
You extend a world by installing a realm: a folder of declarative YAML in git.
Drop one in and the runtime wires up what it declares.
```

**A block that is already indented is passed through exactly as written.** That
is the escape hatch, for the things wrapping would ruin:

```
    embabel realms list        what the appliance can see
    embabel realms link DIR    point it somewhere else
```

Internal alignment inside such a block is preserved, so tables line up.

## Values from the program

`{name}` placeholders are filled by the call site:

```
    Destination: {endpoint}
```

```python
say("usage-reporting", endpoint=PHONE_HOME_ENDPOINT)
```

A placeholder the call site does not pass is a crash at the moment a user
reaches that step, so it is checked instead:

```bash
python3 scripts/check-copy.py
```

That reports a `say()` with no file, a file no `say()` uses, and any
placeholder the call site does not supply. Run it after editing.

## No styling in here

No colour, no escape codes, no box drawing. The code decides how a block is
presented — `heading()` draws the rule above it, the palette in `setup.py`
decides what is indigo. Copy is words.
