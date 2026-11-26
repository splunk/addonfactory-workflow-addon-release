# License evidence and distribution scope

## Classify each candidate

| Evidence | Decision |
|---|---|
| Exact upstream artifact identifies one main license | Select that license; inspect supporting bundled material separately. |
| Upstream grants an `OR` choice covering the same shipped code | Choose an available license and record why the choice covers all relevant files. |
| `AND` licenses apply together to the entire component | Preserve all obligations; leave primary TBD if no truthful main/supporting distinction can be established. |
| `WITH` exception | Keep the exception attached to the license identifier and include its applicable text. |
| Different licenses cover vendored files/subprojects | Map each license to paths and check those paths against the TA build and shipped artifact. |
| License match in sample text, tests, docs, or generated metadata | Inspect the matched material and prove its distribution/obligations before discarding a candidate. |
| Upstream or distribution evidence unavailable | Record the missing evidence and leave the affected field TBD. |

FOSSA detection is evidence to investigate; detection counts and the declared
field do not alone establish which license covers the distributed material.
Do not downgrade a genuine obligation because another license is called main.

## Check TA distribution

Follow the dependency from the TA's exact lockfile/build configuration to
what is delivered. Confirm the report revision matches the artifact and
release identity. Use an immutable archive or reproduce the build with pinned
inputs. Record commands and paths sufficient for another reviewer to repeat
the conclusion. A package-level license may still impose redistribution terms
when only part of the package is delivered.

- **Python:** inspect vendored libraries, wheel/sdist contents, `.dist-info`
  licenses, package data, and files in the TA release archive. Import search
  alone does not prove that a vendored file is absent.
- **JavaScript:** inspect bundler configuration, resolved dependency version,
  build inputs, emitted bundles, sourcemaps/metafiles, and retained attribution
  comments/files. `devDependency` alone does not prove absence from a bundle.
- **Go:** inspect vendoring, module version, target/build tags, generated code,
  and compiler/build dependency evidence. Unused source files may be absent
  from a binary yet still ship in a source/vendor archive.
- **Other native/bundled code:** inspect linked/copied/generated artifacts,
  build manifests and notice propagation. If the artifact cannot prove an
  omission, do not assert it merely from a source call graph.

Attach applicable supporting license text and copyright to notices. Preserve
any required upstream NOTICE text separately as `notice_text`; license text
and notice text serve different purposes. Preserve relevant main-component
NOTICE attributions in `copyright_holder` and include any additional required
NOTICE text in a notice entry named for that upstream file, using the main
license when necessary. Do not drop a required notice just because it adds
no second license.

## Check copyright

Inventory the exact tagged source package and published artifact first. Search
their **entire extracted trees**, including generated code and sourcemap
sources, for case-insensitive `copyright`, `©`, and
`SPDX-FileCopyrightText`. For a local text tree, start with:

```sh
rg -l -i --hidden --no-ignore -e 'copyright' -e '©' \
  -e 'SPDX-FileCopyrightText' /path/to/extracted-package
```

Inspect the surrounding text in every matching file, including
NOTICE/COPYRIGHT/LICENSE/COPYING, metadata, and source headers. Do not stop
after the first match in a file; license boilerplate often precedes the real
statement. For binary or nested archives, extract or inspect their contents;
a text search that skips them is not a complete review. Retain actual
statements and years. Several owners may apply to different included file
groups. Classify each match as main-package attribution, embedded third-party
attribution, example/template, or material absent from the TA distribution,
and record evidence for discarded matches. Boilerplate such as
`<copyright holder>` or `[yyyy]` supplies no ownership information. Neither
do liability clauses beginning `COPYRIGHT HOLDERS BE LIABLE` or license
conditions beginning `copyright notice, this list of conditions`; exclude
those lines from the copyright field even when real holder lines occur nearby.
Treat copyright settings in documentation builders, such as Sphinx
`doc/source/conf.py`, as documentation attribution evidence. Check whether
that documentation is in the published wheel and final TA artifact before
placing it in credits. A Sphinx footer setting alone does not establish
ownership of the shipped runtime code; record the scope decision in the
resolution rule rather than silently discarding the statement.

A package's main Apache LICENSE can have no named holder while an embedded
helper has its own MIT grant and explicit statement. If the helper ships,
preserve its complete grant and attribution in a supporting notice. A main
report field may retain an explicitly documented manual attribution such as
`Splunk Inc.`. Keep that policy choice distinct from an upstream declaration;
a different version's copyright is not a replacement for this exact-version
rule. Check the consuming TA's bundle/source map for the helper's applicability.

If complete evidence reveals no declared owner and no manual attribution was
chosen, use `No copyright holder stated in the distributed package.` and list
the exact artifact and paths checked in the rule's reason/source. A manual
attribution must identify its decision provenance and scope; an author field
alone is not upstream ownership evidence. Inaccessibility, a missing FOSSA cell
or a detector failure is not completed review. Legacy empty overrides remain
readable but are not valid public nonempty-holder output. Do not replace a real
upstream holder declaration with an absence statement. Preserve supporting
attributions independently.

## Primary reference material

- [SPDX license expressions](https://spdx.github.io/spdx-spec/v2.3/SPDX-license-expressions/):
  `OR` expresses alternatives; `AND` expresses combined requirements; `WITH`
  attaches an exception. Inspect the actual upstream grant before selecting.
- [Apache License 2.0, section 4](https://www.apache.org/licenses/LICENSE-2.0.html):
  redistribution includes license and relevant attribution/NOTICE obligations.
  Determine which notices pertain to included material from evidence.
- [Apache licensing guidance](https://infra.apache.org/licensing-howto.html)
  and [Apache license FAQ](https://www.apache.org/foundation/license-faq.html):
  inspect third-party inclusions and the distribution's LICENSE/NOTICE files.

Read the actual license version and exception text when evaluating an
obligation. These references explain classification; they do not resolve any
specific dependency or authorize silently removing attribution.
