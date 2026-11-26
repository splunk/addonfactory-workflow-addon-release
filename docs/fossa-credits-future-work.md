# FOSSA credits future research and work

Collected on 30 October 2026 from the AWS TA 8.1.2 credits audit. These are
deferred investigations, not approved implementation requirements. Resolve the
evidence and reporting approach before adding a new scanner or changing the
public credits format.

## Native and runtime dependency inventory

The shipped `cryptography 45.0.6` binaries contain OpenSSL 3.5.2 and Rust crate
version/source-path evidence that is absent from the current credits. The
release also contains Windows `python3.dll` and compiled Go/Rust runtime code.
The existing libffi notice under cffi is already present; its exact version
and platform linkage remain open. The self_cell symbols establish a review
candidate, but the proposed 1.2.0 version is inferred from a lockfile rather
than an exact binary version declaration.

Research which existing build SBOMs, binary inspection tools and FOSSA scan
inputs can establish the complete distributed inventory. Compare Windows,
Linux and macOS variants. Distinguish a binary marker from proof of linked
code, and runtime dependencies from build/procedural-macro dependencies.
Determine how to represent each confirmed component and applicable terms in
the existing resolutions/additions/notices configuration before implementing
an inventory gate. Do not describe the audit's observed crate list as complete.

## Applicability of embedded code and license notices

Status: pending further review after the 30 October 2026 audit, together with
native inventory research. This includes uncertainty about code snippets,
adaptations and data used inside dependencies, not only standalone libraries.

For an identified third-party file, code fragment, palette/data set or
adaptation, determine whether the covered material is distributed in the TA.
Compiled or bundled code counts even when the original file/header is absent.
Source included in an archive may count even when never executed.

Open candidates include chroma-js ColorBrewer/X11 data; numbro's Numeral.js
and BigNumber portions; ace-builds modes/themes; jsPDF/Adobe portions; vfile's
browser minpath; moment-timezone inside @splunk/moment; embedded
fast-deep-equal inside fast-json-patch; Arrow Go rounding's NumPy provenance;
and the exact coverage of MapLibre's already-retained embedded notices.
For react-ui 5.0.0, the shipped TextArea shadow-height implementation has a
small height adjustment compared with the published package. Its identified
Call-Em-All MIT notice is retained conservatively; exact source/build identity
is a separate provenance question for future review.
For Arrow, absence of NumPy C files does not settle derivation of the compiled
Go implementation. MapLibre's notices are currently included conservatively,
so uncertain scope is not a demonstrated missing public notice.

Use exact source maps, build/render module records, compiled source evidence
and code provenance comparisons. Absence of marker strings or direct imports
does not prove removal. The UCC Rollup licenses manifest provides positive
package-name rendering evidence, while its name-based aggregation can hide
multiple versions and does not establish every embedded subcomponent's scope.

The current notice contract already supports `applicability: unresolved` and
blocks completion for that state. Establish evidence for `confirmed` or
`not_shipped` before changing those decisions. Decide whether the existing
review skill and evidence records are sufficient before adding automation.
