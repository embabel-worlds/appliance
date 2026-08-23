# Contributing

Thanks for looking. Issues and pull requests are welcome.

## Sign your commits (DCO)

This project uses the [Developer Certificate of Origin](https://developercertificate.org)
rather than a contributor licence agreement. It is one line per commit and no paperwork:
you are certifying that you wrote the change, or otherwise have the right to submit it
under this project's licence.

```bash
git commit -s -m "your message"
```

`-s` appends the trailer that does it:

```
Signed-off-by: Your Name <your.email@example.com>
```

Use your real name and an address you read. If you forget on the last commit,
`git commit --amend -s` fixes it; for a branch, `git rebase --signoff main`.

**Why this and not a CLA.** A CLA asks you to assign or licence rights to us, which is
a real decision that needs real attention. The DCO asks only that you confirm the change
is yours to give — which is the question that actually matters for accepting a patch, and
it keeps the licensing of this project honest: what you contribute stays under the licence
you found it under.

## Licence

Contributions are accepted under the [Apache License 2.0](LICENSE), the same terms the
project is released under.

## Changing what the appliance says

The setup wizard's prose lives in [`copy/`](copy/), one `.txt` file per block,
not in `setup.py`. Write unwrapped paragraphs; `say()` wraps and indents them.
See [copy/README.md](copy/README.md), and run `python3 scripts/check-copy.py`
after editing — it catches a block with no file, a file nothing prints, and a
`{placeholder}` the caller does not supply.

Keep it consistent with [worlds.embabel.com](https://worlds.embabel.com); the
site and the installer describing the product differently is the failure this
directory exists to prevent.
