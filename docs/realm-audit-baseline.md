# Realm audit: baseline

The first score of every realm against realm-spec
[`REALM_QUALITY.md`](https://github.com/embabel-worlds/realm-spec/blob/main/REALM_QUALITY.md), taken with
`embabel realm audit` on 2026-11-24, before any realm was brought up to it. It is the line later work is
measured from; re-run the command to see where a realm stands now.

55 realm checkouts were audited, each at its committed HEAD, so work in progress in a working tree is
not counted. Working copies (`-fix…`, spike and draft directories, and second checkouts of a realm
already audited) were skipped. The 31 public realms are named below; the other
24 are private or unpublished, and count in the rule summary without being named.
Mean score across all 55 scored realms: **78**.

How to read it: each realm's score is a weighted mean over the rules that apply to it, MUST weighing three
times SHOULD, each rule scored by the share of its declarations that meet it. A cell is `failed/checked`
for that rule, `✓` when every declaration met it, empty when the rule did not apply. The rule ids are the
standard's.

## Rescored 2026-11-25, same commits

The checker's first run had false positives, found by the agents bringing realms up to the standard.
The tables below are the same 55 checkouts at the same commits, rescored with them corrected, so a
difference from the first run is the checker's, not a realm's:

- Name heuristics read a name's words, not its letters. `teams`, `synonyms`, `meshTerms` and
  `menAtArms` were measures in ms; `feedState`, `arrayClassName`, `registrationStatus` and
  `celebration` were money or a ratio by a fragment (`fee`, `arr`, `ratio`); `candidateId`,
  `candidateName` and `mandateStart` were dates because they hold the letters `date`.
- A property named `value` is no longer taken for money.
- A unit is a whole word, and `ply`/`plies` and `pence` are units.
- A property that declares its `values:` is a category, and is not asked for a unit (`speed` in a
  game of chess is bullet, blitz or rapid).
- An apostrophe ("the plan's") no longer opens a quotation that swallows the backticked values after it.
- The realm description rules (M1, M2) apply to every realm, since routing shows the description
  whatever the realm declares. An app-only realm, unscored before, is now scored on them.

Scores that moved: realm-chess 80 → 82, realm-county-cricket 83 → 85, realm-fr-streets 78 → 81,
realm-impromptu 85 → 87, realm-domesday 75 → 76, realm-homelessness-vancouver-sydney 80 → 81, and one
private realm 73 → 74, each from false P2/P4 findings dropped. realm-bible went 80 → 78
with no finding added: its one real P2 finding (`total`) had been averaged with four passing checks
on names that only held `ratio` or `ms` (`commemoration`, `psalms`), and now stands alone. One
private app-only realm gained a score (75). Several more realms lost false findings without their
rounded score moving. The mean is unchanged at 78.

## Rescored again: entries that only add joins

Joins accumulate across every declaration of a type name, so a realm contributes an edge from its own
anchor into a type another realm owns with an entry holding only the joins: no properties, no
description (realm-jvm-logs and realm-jheapo into realm-java-code's `JavaMethod`, `SourceSpan` and the
rest). The checker scored such
an entry as a declaration of the type, asking it for a description (T1) and an identity (T3, T5) that
belong to the owning realm and are scored there. It no longer does; the joins themselves are still held
to J1. Same checkouts, same commits:

- realm-jvm-logs 70 → 77 (T1 5/8 → ✓, T3 5/5 → 2/2, T5 2/3 → ✓). Its two remaining T3 findings,
  `JavaThrowable` and `JavaStackFrame`, are its own types.
- realm-jheapo 73 → 74 (T1 2/19 → ✓, T5 2/13 → ✓).
- One private realm 59 → 82, all three of its type findings having been join-only entries.

Across the estate T1 loses 9 checks and 9 failures, T3 4 and 4, T5 5 and 3. The mean rises by about
half a point (31 points over 55 realms).

## The five business realms

| Realm | Commit | Score | MUST | SHOULD | Most frequent findings |
|---|---|---:|---:|---:|---|
| realm-odoo | `a7a2dec` | 81 | 30 | 37 | P1 ×12, R1 ×11, T2 ×6, R2 ×1, R3 ×13, V7 ×7 |
| realm-lago | `0854b5f` | 83 | 14 | 19 | R1 ×6, J1 ×4, T2 ×3, P1 ×1, R3 ×6, V7 ×6 |
| realm-chatwoot | `c49f9fe` | 90 | 3 | 8 | T2 ×2, P1 ×1, V7 ×3, P3 ×2, R6 ×1, V5 ×1 |
| realm-business-vocabulary | `b8d7c02` | 93 | 3 | 3 | T2 ×2, P1 ×1, P3 ×2, X1 ×1 |
| realm-account-health | `d2fb3d8` | 81 | 1 | 52 | T2 ×1, V7 ×32, V5 ×9, V4 ×7, P3 ×1, V6 ×1 |

## Every rule, across all realms

Where the estate as a whole stands, and so what to fix first.

| Rule | Level | Realms | Checked | Failed | Met |
|---|---|---:|---:|---:|---:|
| J1 every join has a description, written for the question that traverses it | MUST | 40 | 617 | 104 | 83% |
| K1 a cross-system key (domain, email, account key) is declared as one | SHOULD | 6 | 9 | 7 | 22% |
| M1 realm.yml has a description | MUST | 55 | 55 | 0 | 100% |
| M2 the realm's first sentence says what it holds in 120 characters | SHOULD | 55 | 55 | 28 | 49% |
| P1 every property has a description that says more than its name | MUST | 51 | 5012 | 260 | 94% |
| P2 a money or measure property states its currency or unit | SHOULD | 21 | 155 | 70 | 54% |
| P3 a status-like property lists its values | SHOULD | 38 | 188 | 83 | 55% |
| P4 a date or time property says which moment it is and how it is written | SHOULD | 37 | 213 | 101 | 52% |
| P5 a numeric or boolean property declares its type | SHOULD | 39 | 153 | 12 | 92% |
| R1 a producer that can filter at its source declares pushdown (rules, or [] for none) | MUST | 28 | 364 | 281 | 22% |
| R2 a producer that reads a remote source declares a cache | MUST | 28 | 321 | 25 | 92% |
| R3 a keyed producer's cache remembers a clean miss (negativeTtlSeconds) | SHOULD | 25 | 260 | 103 | 60% |
| R4 a paged producer states its page size and page cap | SHOULD | 9 | 48 | 0 | 100% |
| R5 the page size is the source's maximum | SHOULD | 1 | 1 | 0 | 100% |
| R6 a source that reports a total is counted at the source (aggregates: count) | SHOULD | 4 | 10 | 8 | 19% |
| R7 a single record is read through the source's by-id endpoint, not a listing | SHOULD | 4 | 7 | 5 | 28% |
| R8 a remote pushdown rule states its op (left out, it is CONTAINS) | SHOULD | 12 | 182 | 4 | 97% |
| T1 every type has a description | MUST | 51 | 657 | 4 | 99% |
| T2 a type's first 320 characters say what one node is and what identifies it | MUST | 51 | 656 | 311 | 52% |
| T3 a type reached by more than one join, populated, or a spine, declares its identity | MUST | 27 | 87 | 4 | 95% |
| T5 every other fetched type declares its identity | SHOULD | 38 | 338 | 130 | 62% |
| V1 every view has a description saying what it answers | MUST | 51 | 872 | 5 | 99% |
| V2 every view param has a type and a description | MUST | 50 | 1308 | 0 | 100% |
| V3 a param matched partially (CONTAINS, STARTS WITH, =~) says it is partial | MUST | 26 | 84 | 44 | 47% |
| V4 every view param has a default, or states whether it is required | SHOULD | 50 | 1308 | 301 | 76% |
| V5 a view's description says what it does not answer, or how it is bounded | SHOULD | 51 | 872 | 353 | 59% |
| V6 a view's name says what it counts | SHOULD | 51 | 872 | 9 | 98% |
| V7 a view's first sentence says what it answers in 80 characters | SHOULD | 51 | 872 | 760 | 12% |
| X1 the realm ships worked Cypher examples over its main joins | SHOULD | 54 | 54 | 43 | 20% |
| Z1 nothing is declared that the host ignores | MUST | 51 | 5371 | 7 | 99% |

## Public realms


| Realm | Score | MUST | SHOULD | Types | Producers | Views | J1 | K1 | M1 | M2 | P1 | P2 | P3 | P4 | P5 | R1 | R2 | R3 | R4 | R5 | R6 | R7 | R8 | T1 | T2 | T3 | T5 | V1 | V2 | V3 | V4 | V5 | V6 | V7 | X1 | Z1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| realm-business-vocabulary | 93 | 3 | 3 | 14 | 0 | 0 |  |  | ✓ | ✓ | 1/94 | ✓ | 2/8 | ✓ | ✓ |  |  |  |  |  |  |  |  | ✓ | 2/14 | ✓ |  |  |  |  |  |  |  |  | 1/1 | ✓ |
| realm-chatwoot | 90 | 3 | 8 | 3 | 3 | 4 | ✓ | ✓ | ✓ | ✓ | 1/26 |  | 2/5 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |  | 1/2 |  | ✓ | ✓ | 2/3 | ✓ | ✓ | ✓ | ✓ |  | ✓ | 1/4 | ✓ | 3/4 | 1/1 | ✓ |
| realm-astrology | 87 | 19 | 25 | 16 | 9 | 19 | 2/14 |  | ✓ | ✓ | 8/134 |  | 3/3 | 1/3 |  | ✓ | ✓ | ✓ |  |  |  |  | ✓ | ✓ | 9/16 | ✓ | ✓ | ✓ | ✓ |  | ✓ | 10/19 | ✓ | 10/19 | 1/1 | ✓ |
| realm-impromptu | 87 | 8 | 23 | 10 | 9 | 17 | ✓ |  | ✓ | ✓ | ✓ |  | ✓ | ✓ | ✓ | 1/4 | ✓ | 4/4 |  |  |  |  |  | ✓ | 7/10 | ✓ | ✓ | ✓ | ✓ |  | ✓ | 10/17 | ✓ | 8/17 | 1/1 | ✓ |
| realm-dev-vocabulary | 86 | 0 | 18 | 12 | 0 | 0 |  |  | ✓ | ✓ | ✓ | 1/1 | ✓ | 16/16 | ✓ |  |  |  |  |  |  |  |  | ✓ | ✓ |  |  |  |  |  |  |  |  |  | 1/1 | ✓ |
| realm-github-actions | 86 | 10 | 35 | 7 | 6 | 24 | ✓ |  | ✓ | ✓ | 1/70 |  | 2/5 | ✓ |  | 5/6 | ✓ | ✓ | ✓ |  |  |  | ✓ | ✓ | 4/7 | ✓ | 1/4 | ✓ | ✓ |  | 2/103 | 6/24 | ✓ | 24/24 | ✓ | ✓ |
| realm-county-cricket | 85 | 17 | 29 | 6 | 0 | 20 |  |  | ✓ | 1/1 | 13/94 |  | ✓ | ✓ | ✓ |  |  |  |  |  |  |  |  | ✓ | 4/6 |  |  | ✓ | ✓ | ✓ | ✓ | 8/20 | ✓ | 20/20 | ✓ | ✓ |
| realm-java-project | 84 | 13 | 42 | 12 | 11 | 12 | ✓ |  | ✓ | 1/1 | 7/122 | 1/1 | 1/4 |  | ✓ |  |  |  |  |  |  |  |  | ✓ | ✓ |  | ✓ | ✓ | ✓ | ✓ | 18/22 | 9/12 | ✓ | 11/12 | 1/1 | 6/128 |
| realm-lago | 83 | 14 | 19 | 6 | 9 | 6 | 4/9 | 1/1 | ✓ | ✓ | 1/53 | ✓ | ✓ | 2/7 |  | 6/9 | ✓ | 6/9 | ✓ |  | 4/5 | ✓ | ✓ | ✓ | 3/6 | ✓ |  | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | 6/6 | ✓ | ✓ |
| realm-chess | 82 | 17 | 36 | 12 | 12 | 13 | ✓ |  | ✓ | ✓ | 2/133 | 3/5 | 1/5 |  |  | 7/12 | ✓ | 7/12 |  |  |  |  | ✓ | ✓ | 8/12 | ✓ | ✓ | ✓ | ✓ |  | ✓ | 11/13 | ✓ | 13/13 | 1/1 | ✓ |
| realm-scripture-vocabulary | 82 | 2 | 17 | 5 | 0 | 9 |  |  | ✓ | ✓ | ✓ |  |  | 1/1 | ✓ |  |  |  |  |  |  |  |  | ✓ | ✓ |  |  | ✓ | ✓ | 2/3 | ✓ | 6/9 | ✓ | 9/9 | 1/1 | ✓ |
| realm-account-health | 81 | 1 | 52 | 2 | 0 | 35 |  |  | ✓ | 1/1 | ✓ |  | 1/2 |  |  |  |  |  |  |  |  |  |  | ✓ | 1/2 |  |  | ✓ | ✓ | ✓ | 7/10 | 9/35 | 1/35 | 32/35 | 1/1 | ✓ |
| realm-fr-streets | 81 | 81 | 42 | 29 | 40 | 21 | 12/50 |  | ✓ | 1/1 | 9/205 |  | 1/4 |  | ✓ | 40/40 | ✓ | 15/26 | ✓ |  |  |  |  | ✓ | 20/29 | ✓ | 1/8 | ✓ | ✓ | ✓ | ✓ | 4/21 | ✓ | 20/21 | ✓ | ✓ |
| realm-github | 81 | 18 | 43 | 14 | 17 | 17 | 4/22 | 1/1 | ✓ | 1/1 | 1/90 |  | ✓ | 1/5 | 1/1 | 8/17 | ✓ | 7/17 | ✓ |  |  | 2/3 | 4/12 | ✓ | 5/14 | ✓ | ✓ | ✓ | ✓ |  | 3/50 | 6/17 | ✓ | 17/17 | ✓ | ✓ |
| realm-homelessness-vancouver-sydney | 81 | 5 | 17 | 4 | 0 | 14 |  |  | ✓ | 1/1 | 1/44 |  | ✓ |  |  |  |  |  |  |  |  |  |  | ✓ | 4/4 |  |  | ✓ | ✓ |  | ✓ | 4/14 | ✓ | 12/14 | ✓ | ✓ |
| realm-odoo | 81 | 30 | 37 | 10 | 18 | 9 | ✓ | 2/3 | ✓ | ✓ | 12/81 | 3/4 | 1/2 | 5/7 | 2/2 | 11/18 | 1/18 | 13/17 | ✓ |  |  |  | ✓ | ✓ | 6/10 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | 4/9 | ✓ | 7/9 | ✓ | ✓ |
| realm-java-code | 80 | 16 | 46 | 7 | 19 | 16 | 4/22 |  | ✓ | 1/1 | 7/113 |  | 1/6 |  | ✓ |  |  |  |  |  |  |  |  | ✓ | 5/7 | ✓ | ✓ | ✓ | ✓ |  | 21/32 | 8/16 | ✓ | 14/16 | 1/1 | ✓ |
| realm-bible | 78 | 59 | 91 | 40 | 17 | 53 | ✓ |  | ✓ | ✓ | 9/268 | 1/1 | 2/7 | 6/13 | 1/4 | 17/17 | ✓ | ✓ |  |  |  |  |  | ✓ | 30/40 | ✓ | ✓ | ✓ | ✓ | 3/12 | ✓ | 31/53 | ✓ | 49/53 | 1/1 | ✓ |
| realm-java-source-code | 78 | 5 | 41 | 5 | 7 | 13 | ✓ |  | ✓ | 1/1 | ✓ |  | 1/2 |  | ✓ |  |  |  |  |  |  |  |  | 1/5 | 4/4 | ✓ | ✓ | ✓ | ✓ | ✓ | 17/24 | 8/13 | ✓ | 13/13 | 1/1 | ✓ |
| realm-oss-health | 77 | 5 | 12 | 4 | 2 | 8 | ✓ |  | ✓ | ✓ | ✓ | 1/1 |  | ✓ | 2/2 | 2/2 | ✓ | ✓ |  |  |  |  |  | ✓ | 3/4 |  | ✓ | ✓ | ✓ |  | ✓ | 2/8 | ✓ | 6/8 | 1/1 | ✓ |
| realm-domesday | 76 | 26 | 81 | 10 | 2 | 49 | ✓ |  | ✓ | 1/1 | 13/171 | 2/5 | ✓ |  | ✓ | 1/1 | 1/2 | ✓ |  |  |  |  |  | ✓ | 8/10 | ✓ | ✓ | ✓ | ✓ | 3/8 | ✓ | 31/49 | ✓ | 47/49 | ✓ | ✓ |
| realm-gov-uk | 76 | 21 | 46 | 15 | 9 | 18 | ✓ |  | ✓ | 1/1 | 3/100 |  | 8/8 | 5/17 | ✓ | 9/9 | ✓ | ✓ |  |  |  |  |  | ✓ | 9/15 |  | 1/9 | ✓ | ✓ |  | 15/25 | 1/18 | ✓ | 14/18 | 1/1 | ✓ |
| realm-npm | 76 | 23 | 21 | 14 | 13 | 12 | ✓ |  | ✓ | ✓ | ✓ |  |  | 2/3 | 4/4 | 12/12 | ✓ | 2/12 |  |  |  |  |  | ✓ | 10/14 | 1/2 | 1/11 | ✓ | ✓ |  | ✓ | 1/12 | ✓ | 10/12 | 1/1 | ✓ |
| realm-quran | 76 | 5 | 17 | 4 | 0 | 9 |  |  | ✓ | ✓ | ✓ |  |  |  |  |  |  |  |  |  |  |  |  | ✓ | 2/4 |  |  | ✓ | ✓ | 3/3 | ✓ | 8/9 | ✓ | 8/9 | 1/1 | ✓ |
| realm-ca-streets | 74 | 63 | 104 | 38 | 36 | 54 | 15/36 | 2/2 | ✓ | 1/1 | ✓ | 8/11 |  | ✓ | ✓ | 36/36 | ✓ | 6/36 |  |  |  |  |  | ✓ | 12/38 |  | 28/36 | ✓ | ✓ |  | ✓ | 16/54 | ✓ | 42/54 | 1/1 | ✓ |
| realm-jheapo | 74 | 61 | 85 | 19 | 23 | 39 | 9/28 |  | ✓ | 1/1 | 19/135 | 3/17 | 4/4 | ✓ | ✓ | ✓ | 21/21 |  |  |  |  |  | ✓ | ✓ | 11/17 | ✓ | ✓ | 1/39 | ✓ |  | 14/43 | 25/39 | 1/39 | 36/39 | 1/1 | ✓ |
| realm-uk-streets | 73 | 91 | 139 | 36 | 40 | 60 | ✓ | 1/1 | ✓ | 1/1 | 6/361 | 18/42 | 12/20 | 7/14 | 1/31 | 39/40 | ✓ | 19/25 | ✓ |  | 1/1 |  | ✓ | ✓ | 29/36 | ✓ | 2/30 | ✓ | ✓ | 17/24 | ✓ | 18/60 | 1/60 | 58/60 | ✓ | ✓ |
| realm-tanakh | 72 | 6 | 19 | 5 | 0 | 10 |  |  | ✓ | ✓ | ✓ |  |  | 1/1 | ✓ |  |  |  |  |  |  |  |  | ✓ | 4/5 |  |  | ✓ | ✓ | 2/2 | ✓ | 8/10 | ✓ | 9/10 | 1/1 | ✓ |
| realm-book-of-mormon | 70 | 5 | 16 | 4 | 0 | 7 |  |  | ✓ | 1/1 | ✓ |  |  |  | ✓ |  |  |  |  |  |  |  |  | ✓ | 3/4 |  |  | ✓ | ✓ | 2/2 | ✓ | 7/7 | ✓ | 7/7 | 1/1 | ✓ |
| realm-jvm-logs | 77 | 3 | 20 | 8 | 3 | 10 | ✓ |  | ✓ | ✓ | ✓ | 2/2 |  | 1/1 | ✓ |  |  |  |  |  |  |  |  | ✓ | 1/3 | 2/2 | ✓ | ✓ | ✓ |  | 5/9 | 1/10 | ✓ | 10/10 | 1/1 | ✓ |
| realm-lds-scriptures | 68 | 8 | 24 | 7 | 0 | 10 |  |  | ✓ | 1/1 | ✓ |  |  | 2/2 | ✓ |  |  |  |  |  |  |  |  | ✓ | 5/7 |  |  | ✓ | ✓ | 3/3 | ✓ | 10/10 | ✓ | 10/10 | 1/1 | ✓ |

## Regenerating


```
embabel realm audit <directory of realm checkouts> --markdown   # the tables
embabel realm audit <realm> -v                                    # one realm's findings, each naming its rule
```
