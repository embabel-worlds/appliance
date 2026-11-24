# Realm audit: baseline

The first score of every realm against realm-spec
[`REALM_QUALITY.md`](https://github.com/embabel-worlds/realm-spec/blob/main/REALM_QUALITY.md), taken with
`embabel realm audit` on 2026-11-24, before any realm was brought up to it. It is the line later work is
measured from; re-run the command to see where a realm stands now.

55 realm checkouts were audited, each at its committed HEAD, so work in progress in a working tree is
not counted. Working copies (`-fix…`, spike and draft directories, and second checkouts of a realm
already audited) were skipped. The 31 public realms are named below; the other
24 are private or unpublished, and count in the rule summary without being named.
Mean score across all 54 scored realms: **78**.

How to read it: each realm's score is a weighted mean over the rules that apply to it, MUST weighing three
times SHOULD, each rule scored by the share of its declarations that meet it. A cell is `failed/checked`
for that rule, `✓` when every declaration met it, empty when the rule did not apply. The rule ids are the
standard's.

## The five business realms

| Realm | Commit | Score | MUST | SHOULD | Most frequent findings |
|---|---|---:|---:|---:|---|
| realm-odoo | `a7a2dec` | 81 | 30 | 37 | P1 ×12, R1 ×11, T2 ×6, R2 ×1, R3 ×13, V7 ×7 |
| realm-lago | `0854b5f` | 83 | 14 | 20 | R1 ×6, J1 ×4, T2 ×3, P1 ×1, R3 ×6, V7 ×6 |
| realm-chatwoot | `c49f9fe` | 90 | 3 | 8 | T2 ×2, P1 ×1, V7 ×3, P3 ×2, R6 ×1, V5 ×1 |
| realm-business-vocabulary | `b8d7c02` | 93 | 3 | 3 | T2 ×2, P1 ×1, P3 ×2, X1 ×1 |
| realm-account-health | `d2fb3d8` | 81 | 1 | 52 | T2 ×1, V7 ×32, V5 ×9, V4 ×7, P3 ×1, V6 ×1 |

## Every rule, across all realms

Where the estate as a whole stands, and so what to fix first.

| Rule | Level | Realms | Checked | Failed | Met |
|---|---|---:|---:|---:|---:|
| J1 every join has a description, written for the question that traverses it | MUST | 40 | 617 | 104 | 83% |
| K1 a cross-system key (domain, email, account key) is declared as one | SHOULD | 6 | 9 | 7 | 22% |
| M1 realm.yml has a description | MUST | 54 | 54 | 0 | 100% |
| M2 the realm's first sentence says what it holds in 120 characters | SHOULD | 54 | 54 | 27 | 50% |
| P1 every property has a description that says more than its name | MUST | 51 | 5012 | 260 | 94% |
| P2 a money or measure property states its currency or unit | SHOULD | 27 | 219 | 110 | 49% |
| P3 a status-like property lists its values | SHOULD | 38 | 188 | 83 | 55% |
| P4 a date or time property says which moment it is and how it is written | SHOULD | 38 | 215 | 105 | 51% |
| P5 a numeric or boolean property declares its type | SHOULD | 39 | 153 | 12 | 92% |
| R1 a producer that can filter at its source declares pushdown (rules, or [] for none) | MUST | 28 | 364 | 281 | 22% |
| R2 a producer that reads a remote source declares a cache | MUST | 28 | 321 | 25 | 92% |
| R3 a keyed producer's cache remembers a clean miss (negativeTtlSeconds) | SHOULD | 25 | 260 | 103 | 60% |
| R4 a paged producer states its page size and page cap | SHOULD | 9 | 48 | 0 | 100% |
| R5 the page size is the source's maximum | SHOULD | 1 | 1 | 0 | 100% |
| R6 a source that reports a total is counted at the source (aggregates: count) | SHOULD | 4 | 10 | 8 | 19% |
| R7 a single record is read through the source's by-id endpoint, not a listing | SHOULD | 4 | 7 | 5 | 28% |
| R8 a remote pushdown rule states its op (left out, it is CONTAINS) | SHOULD | 12 | 182 | 4 | 97% |
| T1 every type has a description | MUST | 52 | 666 | 13 | 98% |
| T2 a type's first 320 characters say what one node is and what identifies it | MUST | 51 | 656 | 311 | 52% |
| T3 a type reached by more than one join, populated, or a spine, declares its identity | MUST | 28 | 91 | 8 | 91% |
| T5 every other fetched type declares its identity | SHOULD | 39 | 343 | 133 | 61% |
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
| realm-dev-vocabulary | 86 | 0 | 18 | 12 | 0 | 0 |  |  | ✓ | ✓ | ✓ | 1/1 | ✓ | 16/16 | ✓ |  |  |  |  |  |  |  |  | ✓ | ✓ |  |  |  |  |  |  |  |  |  | 1/1 | ✓ |
| realm-github-actions | 86 | 10 | 35 | 7 | 6 | 24 | ✓ |  | ✓ | ✓ | 1/70 |  | 2/5 | ✓ |  | 5/6 | ✓ | ✓ | ✓ |  |  |  | ✓ | ✓ | 4/7 | ✓ | 1/4 | ✓ | ✓ |  | 2/103 | 6/24 | ✓ | 24/24 | ✓ | ✓ |
| realm-impromptu | 85 | 8 | 24 | 10 | 9 | 17 | ✓ |  | ✓ | ✓ | ✓ | 1/1 | ✓ | ✓ | ✓ | 1/4 | ✓ | 4/4 |  |  |  |  |  | ✓ | 7/10 | ✓ | ✓ | ✓ | ✓ |  | ✓ | 10/17 | ✓ | 8/17 | 1/1 | ✓ |
| realm-java-project | 84 | 13 | 44 | 12 | 11 | 12 | ✓ |  | ✓ | 1/1 | 7/122 | 3/3 | 1/4 |  | ✓ |  |  |  |  |  |  |  |  | ✓ | ✓ |  | ✓ | ✓ | ✓ | ✓ | 18/22 | 9/12 | ✓ | 11/12 | 1/1 | 6/128 |
| realm-county-cricket | 83 | 17 | 30 | 6 | 0 | 20 |  |  | ✓ | 1/1 | 13/94 | 1/1 | ✓ | ✓ | ✓ |  |  |  |  |  |  |  |  | ✓ | 4/6 |  |  | ✓ | ✓ | ✓ | ✓ | 8/20 | ✓ | 20/20 | ✓ | ✓ |
| realm-lago | 83 | 14 | 20 | 6 | 9 | 6 | 4/9 | 1/1 | ✓ | ✓ | 1/53 | 1/8 | ✓ | 2/7 |  | 6/9 | ✓ | 6/9 | ✓ |  | 4/5 | ✓ | ✓ | ✓ | 3/6 | ✓ |  | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | 6/6 | ✓ | ✓ |
| realm-scripture-vocabulary | 82 | 2 | 17 | 5 | 0 | 9 |  |  | ✓ | ✓ | ✓ |  |  | 1/1 | ✓ |  |  |  |  |  |  |  |  | ✓ | ✓ |  |  | ✓ | ✓ | 2/3 | ✓ | 6/9 | ✓ | 9/9 | 1/1 | ✓ |
| realm-account-health | 81 | 1 | 52 | 2 | 0 | 35 |  |  | ✓ | 1/1 | ✓ |  | 1/2 |  |  |  |  |  |  |  |  |  |  | ✓ | 1/2 |  |  | ✓ | ✓ | ✓ | 7/10 | 9/35 | 1/35 | 32/35 | 1/1 | ✓ |
| realm-github | 81 | 18 | 43 | 14 | 17 | 17 | 4/22 | 1/1 | ✓ | 1/1 | 1/90 |  | ✓ | 1/5 | 1/1 | 8/17 | ✓ | 7/17 | ✓ |  |  | 2/3 | 4/12 | ✓ | 5/14 | ✓ | ✓ | ✓ | ✓ |  | 3/50 | 6/17 | ✓ | 17/17 | ✓ | ✓ |
| realm-odoo | 81 | 30 | 37 | 10 | 18 | 9 | ✓ | 2/3 | ✓ | ✓ | 12/81 | 3/4 | 1/2 | 5/7 | 2/2 | 11/18 | 1/18 | 13/17 | ✓ |  |  |  | ✓ | ✓ | 6/10 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | 4/9 | ✓ | 7/9 | ✓ | ✓ |
| realm-bible | 80 | 59 | 91 | 40 | 17 | 53 | ✓ |  | ✓ | ✓ | 9/268 | 1/5 | 2/7 | 6/13 | 1/4 | 17/17 | ✓ | ✓ |  |  |  |  |  | ✓ | 30/40 | ✓ | ✓ | ✓ | ✓ | 3/12 | ✓ | 31/53 | ✓ | 49/53 | 1/1 | ✓ |
| realm-chess | 80 | 17 | 38 | 12 | 12 | 13 | ✓ |  | ✓ | ✓ | 2/133 | 4/6 | 1/5 | 1/1 |  | 7/12 | ✓ | 7/12 |  |  |  |  | ✓ | ✓ | 8/12 | ✓ | ✓ | ✓ | ✓ |  | ✓ | 11/13 | ✓ | 13/13 | 1/1 | ✓ |
| realm-homelessness-vancouver-sydney | 80 | 5 | 18 | 4 | 0 | 14 |  |  | ✓ | 1/1 | 1/44 | 1/2 | ✓ |  |  |  |  |  |  |  |  |  |  | ✓ | 4/4 |  |  | ✓ | ✓ |  | ✓ | 4/14 | ✓ | 12/14 | ✓ | ✓ |
| realm-java-code | 80 | 16 | 46 | 7 | 19 | 16 | 4/22 |  | ✓ | 1/1 | 7/113 |  | 1/6 |  | ✓ |  |  |  |  |  |  |  |  | ✓ | 5/7 | ✓ | ✓ | ✓ | ✓ |  | 21/32 | 8/16 | ✓ | 14/16 | 1/1 | ✓ |
| realm-fr-streets | 78 | 81 | 47 | 29 | 40 | 21 | 12/50 |  | ✓ | 1/1 | 9/205 | 3/3 | 1/4 | 2/2 | ✓ | 40/40 | ✓ | 15/26 | ✓ |  |  |  |  | ✓ | 20/29 | ✓ | 1/8 | ✓ | ✓ | ✓ | ✓ | 4/21 | ✓ | 20/21 | ✓ | ✓ |
| realm-java-source-code | 78 | 5 | 41 | 5 | 7 | 13 | ✓ |  | ✓ | 1/1 | ✓ |  | 1/2 |  | ✓ |  |  |  |  |  |  |  |  | 1/5 | 4/4 | ✓ | ✓ | ✓ | ✓ | ✓ | 17/24 | 8/13 | ✓ | 13/13 | 1/1 | ✓ |
| realm-oss-health | 77 | 5 | 12 | 4 | 2 | 8 | ✓ |  | ✓ | ✓ | ✓ | 1/1 |  | ✓ | 2/2 | 2/2 | ✓ | ✓ |  |  |  |  |  | ✓ | 3/4 |  | ✓ | ✓ | ✓ |  | ✓ | 2/8 | ✓ | 6/8 | 1/1 | ✓ |
| realm-gov-uk | 76 | 21 | 47 | 15 | 9 | 18 | ✓ |  | ✓ | 1/1 | 3/100 |  | 8/8 | 6/18 | ✓ | 9/9 | ✓ | ✓ |  |  |  |  |  | ✓ | 9/15 |  | 1/9 | ✓ | ✓ |  | 15/25 | 1/18 | ✓ | 14/18 | 1/1 | ✓ |
| realm-npm | 76 | 23 | 21 | 14 | 13 | 12 | ✓ |  | ✓ | ✓ | ✓ |  |  | 2/3 | 4/4 | 12/12 | ✓ | 2/12 |  |  |  |  |  | ✓ | 10/14 | 1/2 | 1/11 | ✓ | ✓ |  | ✓ | 1/12 | ✓ | 10/12 | 1/1 | ✓ |
| realm-quran | 76 | 5 | 17 | 4 | 0 | 9 |  |  | ✓ | ✓ | ✓ |  |  |  |  |  |  |  |  |  |  |  |  | ✓ | 2/4 |  |  | ✓ | ✓ | 3/3 | ✓ | 8/9 | ✓ | 8/9 | 1/1 | ✓ |
| realm-domesday | 75 | 26 | 89 | 10 | 2 | 49 | ✓ |  | ✓ | 1/1 | 13/171 | 10/12 | ✓ |  | ✓ | 1/1 | 1/2 | ✓ |  |  |  |  |  | ✓ | 8/10 | ✓ | ✓ | ✓ | ✓ | 3/8 | ✓ | 31/49 | ✓ | 47/49 | ✓ | ✓ |
| realm-ca-streets | 74 | 63 | 114 | 38 | 36 | 54 | 15/36 | 2/2 | ✓ | 1/1 | ✓ | 18/30 |  | ✓ | ✓ | 36/36 | ✓ | 6/36 |  |  |  |  |  | ✓ | 12/38 |  | 28/36 | ✓ | ✓ |  | ✓ | 16/54 | ✓ | 42/54 | 1/1 | ✓ |
| realm-jheapo | 73 | 63 | 89 | 19 | 23 | 39 | 9/28 |  | ✓ | 1/1 | 19/135 | 5/19 | 4/4 |  | ✓ | ✓ | 21/21 |  |  |  |  |  | ✓ | 2/19 | 11/17 | ✓ | 2/13 | 1/39 | ✓ |  | 14/43 | 25/39 | 1/39 | 36/39 | 1/1 | ✓ |
| realm-uk-streets | 73 | 91 | 141 | 36 | 40 | 60 | ✓ | 1/1 | ✓ | 1/1 | 6/361 | 20/48 | 12/20 | 7/14 | 1/31 | 39/40 | ✓ | 19/25 | ✓ |  | 1/1 |  | ✓ | ✓ | 29/36 | ✓ | 2/30 | ✓ | ✓ | 17/24 | ✓ | 18/60 | 1/60 | 58/60 | ✓ | ✓ |
| realm-tanakh | 72 | 6 | 19 | 5 | 0 | 10 |  |  | ✓ | ✓ | ✓ |  |  | 1/1 | ✓ |  |  |  |  |  |  |  |  | ✓ | 4/5 |  |  | ✓ | ✓ | 2/2 | ✓ | 8/10 | ✓ | 9/10 | 1/1 | ✓ |
| realm-book-of-mormon | 70 | 5 | 16 | 4 | 0 | 7 |  |  | ✓ | 1/1 | ✓ |  |  |  | ✓ |  |  |  |  |  |  |  |  | ✓ | 3/4 |  |  | ✓ | ✓ | 2/2 | ✓ | 7/7 | ✓ | 7/7 | 1/1 | ✓ |
| realm-jvm-logs | 70 | 11 | 22 | 8 | 3 | 10 | ✓ |  | ✓ | ✓ | ✓ | 2/2 |  | 1/1 | ✓ |  |  |  |  |  |  |  |  | 5/8 | 1/3 | 5/5 | 2/3 | ✓ | ✓ |  | 5/9 | 1/10 | ✓ | 10/10 | 1/1 | ✓ |
| realm-lds-scriptures | 68 | 8 | 24 | 7 | 0 | 10 |  |  | ✓ | 1/1 | ✓ |  |  | 2/2 | ✓ |  |  |  |  |  |  |  |  | ✓ | 5/7 |  |  | ✓ | ✓ | 3/3 | ✓ | 10/10 | ✓ | 10/10 | 1/1 | ✓ |

## Regenerating

```
embabel realm audit <directory of realm checkouts> --markdown   # the tables
embabel realm audit <realm> -v                                    # one realm's findings, each naming its rule
```
